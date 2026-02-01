"""
Discord Bot Connector с Context7 Enterprise паттернами

Интеграция с Discord API через discord.py для мониторинга
crypto-серверов и каналов с enterprise-grade функциональностью.
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, AsyncGenerator
from datetime import datetime, timedelta
import discord
from discord.ext import commands
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential
from circuitbreaker import CircuitBreaker
import re

from ..utils.config import Config
from ..monitoring.metrics_collector import MetricsCollector

logger = structlog.get_logger(__name__)

class DiscordConnector:
    """
    Enterprise Discord API коннектор с Context7 паттернами
    
    Features:
    - Discord.py bot integration
    - Crypto-servers мониторинг
    - Real-time message streaming
    - Channel-specific sentiment tracking
    - User activity analysis
    - Emoji reaction monitoring
    """
    
    def __init__(self, config: Config):
        self.config = config
        self.metrics = MetricsCollector("discord_connector")
        self.logger = logger.bind(component="discord_connector")
        
        # Discord bot настройки
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guild_messages = True
        intents.guild_reactions = True
        intents.members = True
        
        self.bot = commands.Bot(
            command_prefix='!',
            intents=intents,
            help_command=None
        )
        
        # Circuit breaker
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60,
            expected_exception=Exception
        )
        
        self._connected = False
        self._monitored_guilds = []
        self._message_cache = []
        
        # Crypto Discord сервера (Guild IDs)
        self.crypto_guilds = [
            # Основные crypto Discord сервера (заменить на реальные ID)
            "123456789012345678",  # Bitcoin Community
            "234567890123456789",  # Ethereum
            "345678901234567890",  # CryptoCurrency
            "456789012345678901",  # DeFi Pulse
            "567890123456789012",  # NFT Community
        ]
        
        # Crypto keywords
        self.crypto_keywords = [
            "btc", "bitcoin", "eth", "ethereum", "crypto", "cryptocurrency",
            "blockchain", "defi", "nft", "altcoin", "hodl", "moon", "lambo",
            "diamond hands", "paper hands", "whale", "pump", "dump", "dip",
            "bull", "bear", "satoshi", "gwei", "gas", "staking", "yield"
        ]
        
        self._setup_event_handlers()
    
    def _setup_event_handlers(self):
        """Настройка обработчиков событий бота."""
        
        @self.bot.event
        async def on_ready():
            self._connected = True
            self.logger.info("Discord bot connected", 
                           user=self.bot.user.name, guilds=len(self.bot.guilds))
            self.metrics.increment("connection_success")
        
        @self.bot.event
        async def on_message(message):
            # Избегать обработки собственных сообщений
            if message.author == self.bot.user:
                return
            
            # Фильтрация по crypto-контенту
            if not self._is_crypto_related(message.content):
                return
            
            # Создать данные сообщения
            message_data = await self._parse_message(message)
            self._message_cache.append(message_data)
            
            # Ограничение кэша
            if len(self._message_cache) > 1000:
                self._message_cache = self._message_cache[-500:]
            
            self.metrics.increment("messages_processed")
        
        @self.bot.event
        async def on_reaction_add(reaction, user):
            if user == self.bot.user:
                return
            
            # Отслеживание реакций на crypto-сообщения
            if self._is_crypto_related(reaction.message.content):
                self.metrics.increment("reactions_tracked")
        
        @self.bot.event
        async def on_error(event, *args, **kwargs):
            self.logger.error("Discord bot error", event=event)
            self.metrics.increment("bot_errors")
    
    async def connect(self) -> bool:
        """Запустить Discord бота."""
        try:
            # Запуск бота в фоновом режиме
            asyncio.create_task(self.bot.start(self.config.discord_bot_token))
            
            # Ожидание подключения
            for _ in range(30):  # 30 секунд ожидания
                if self._connected:
                    return True
                await asyncio.sleep(1)
            
            self.logger.error("Discord bot connection timeout")
            return False
            
        except Exception as e:
            self.logger.error("Discord bot connection failed", error=str(e))
            self.metrics.increment("connection_error")
            return False
    
    async def get_guild_messages(
        self,
        guild_id: int,
        channel_name: Optional[str] = None,
        limit: int = 100,
        hours_back: int = 24,
        crypto_filter: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Получить сообщения из Guild (сервера)
        
        Args:
            guild_id: ID сервера Discord
            channel_name: Имя канала (None = все каналы)
            limit: Количество сообщений
            hours_back: Период поиска в часах
            crypto_filter: Фильтровать crypto-контент
        """
        
        if not self._connected:
            await self.connect()
        
        try:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                self.logger.warning("Guild not found", guild_id=guild_id)
                return []
            
            # Временное окно
            after_time = datetime.utcnow() - timedelta(hours=hours_back)
            
            all_messages = []
            
            # Выбор каналов
            channels = []
            if channel_name:
                channel = discord.utils.get(guild.text_channels, name=channel_name)
                if channel:
                    channels = [channel]
            else:
                channels = guild.text_channels[:10]  # Ограничение для производительности
            
            for channel in channels:
                try:
                    # Проверка разрешений
                    if not channel.permissions_for(guild.me).read_message_history:
                        continue
                    
                    messages = []
                    async for message in channel.history(limit=limit//len(channels), after=after_time):
                        if not message.content:
                            continue
                        
                        # Фильтрация crypto-контента
                        if crypto_filter and not self._is_crypto_related(message.content):
                            continue
                        
                        message_data = await self._parse_message(message)
                        messages.append(message_data)
                    
                    all_messages.extend(messages)
                    self.logger.debug("Messages fetched from channel",
                                    guild=guild.name, channel=channel.name, count=len(messages))
                    
                except discord.Forbidden:
                    self.logger.warning("No permission to read channel", 
                                      guild=guild.name, channel=channel.name)
                    continue
                except Exception as e:
                    self.logger.error("Error fetching messages from channel",
                                    guild=guild.name, channel=channel.name, error=str(e))
                    continue
            
            self.metrics.increment("guild_messages_fetched", len(all_messages))
            self.logger.info("Guild messages fetched", guild=guild.name, total=len(all_messages))
            
            return sorted(all_messages, key=lambda x: x["timestamp"], reverse=True)
            
        except Exception as e:
            self.logger.error("Failed to fetch guild messages", guild_id=guild_id, error=str(e))
            return []
    
    async def get_cached_messages(
        self,
        hours_back: int = 1,
        crypto_filter: bool = True
    ) -> List[Dict[str, Any]]:
        """Получить сообщения из кэша за последние часы."""
        
        cutoff_time = datetime.utcnow() - timedelta(hours=hours_back)
        
        filtered_messages = []
        for msg in self._message_cache:
            try:
                msg_time = datetime.fromisoformat(msg["timestamp"].replace('Z', '+00:00'))
                if msg_time >= cutoff_time:
                    if not crypto_filter or self._is_crypto_related(msg["content"]):
                        filtered_messages.append(msg)
            except Exception:
                continue
        
        self.logger.info("Cached messages retrieved", count=len(filtered_messages))
        return sorted(filtered_messages, key=lambda x: x["timestamp"], reverse=True)
    
    async def search_messages(
        self,
        query: str,
        guild_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Поиск сообщений по запросу."""
        
        if not self._connected:
            await self.connect()
        
        results = []
        
        try:
            # Поиск в указанном guild или во всех
            guilds = [self.bot.get_guild(guild_id)] if guild_id else self.bot.guilds
            
            for guild in guilds:
                if not guild:
                    continue
                
                for channel in guild.text_channels[:5]:  # Ограничение каналов
                    try:
                        if not channel.permissions_for(guild.me).read_message_history:
                            continue
                        
                        async for message in channel.history(limit=limit//len(guild.text_channels)):
                            if not message.content:
                                continue
                            
                            if query.lower() in message.content.lower():
                                message_data = await self._parse_message(message)
                                message_data["relevance_score"] = self._calculate_relevance_score(
                                    message.content, query
                                )
                                results.append(message_data)
                    
                    except discord.Forbidden:
                        continue
                    except Exception as e:
                        self.logger.error("Search error in channel", 
                                        guild=guild.name, channel=channel.name, error=str(e))
                        continue
            
            # Сортировка по релевантности
            results.sort(key=lambda x: x["relevance_score"], reverse=True)
            
            self.logger.info("Message search completed", query=query, results=len(results))
            return results
            
        except Exception as e:
            self.logger.error("Message search failed", query=query, error=str(e))
            return []
    
    async def get_guild_stats(self, guild_id: int) -> Dict[str, Any]:
        """Получить статистику по серверу."""
        
        if not self._connected:
            await self.connect()
        
        try:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return {}
            
            # Подсчет активных пользователей
            active_users = sum(1 for member in guild.members if member.status != discord.Status.offline)
            
            return {
                "id": guild.id,
                "name": guild.name,
                "description": guild.description,
                "member_count": guild.member_count,
                "active_members": active_users,
                "text_channels": len(guild.text_channels),
                "voice_channels": len(guild.voice_channels),
                "roles": len(guild.roles),
                "created_at": guild.created_at.isoformat(),
                "owner": str(guild.owner) if guild.owner else None,
                "verification_level": str(guild.verification_level),
                "boost_level": guild.premium_tier,
                "boost_count": guild.premium_subscription_count,
                "platform": "discord"
            }
            
        except Exception as e:
            self.logger.error("Failed to get guild stats", guild_id=guild_id, error=str(e))
            return {}
    
    async def _parse_message(self, message: discord.Message) -> Dict[str, Any]:
        """Парсинг Discord сообщения в стандартный формат."""
        
        # Информация об авторе
        author_data = {
            "id": message.author.id,
            "username": message.author.name,
            "discriminator": message.author.discriminator,
            "display_name": message.author.display_name,
            "is_bot": message.author.bot,
            "is_system": message.author.system,
            "avatar_url": str(message.author.avatar.url) if message.author.avatar else None,
            "created_at": message.author.created_at.isoformat(),
        }
        
        # Информация о канале и сервере
        guild_data = {
            "id": message.guild.id if message.guild else None,
            "name": message.guild.name if message.guild else "Direct Message",
        }
        
        channel_data = {
            "id": message.channel.id,
            "name": message.channel.name if hasattr(message.channel, 'name') else "DM",
            "type": str(message.channel.type),
        }
        
        # Реакции
        reactions = {}
        for reaction in message.reactions:
            emoji = str(reaction.emoji)
            reactions[emoji] = reaction.count
        
        # Вложения
        attachments = [
            {
                "id": att.id,
                "filename": att.filename,
                "size": att.size,
                "url": att.url,
                "content_type": att.content_type
            }
            for att in message.attachments
        ]
        
        return {
            "id": message.id,
            "content": message.content,
            "timestamp": message.created_at.isoformat(),
            "edited_at": message.edited_at.isoformat() if message.edited_at else None,
            "author": author_data,
            "guild": guild_data,
            "channel": channel_data,
            "reactions": reactions,
            "attachments": attachments,
            "pinned": message.pinned,
            "tts": message.tts,
            "mention_everyone": message.mention_everyone,
            "mentions": [str(user) for user in message.mentions],
            "role_mentions": [role.name for role in message.role_mentions],
            "crypto_symbols": self._extract_crypto_symbols(message.content),
            "sentiment_indicators": self._extract_sentiment_indicators(message.content),
            "platform": "discord"
        }
    
    def _extract_crypto_symbols(self, text: str) -> List[str]:
        """Извлечь упоминания криптовалют."""
        symbols = []
        text_upper = text.upper()
        
        # Основные символы
        crypto_symbols = [
            "BTC", "ETH", "ADA", "SOL", "DOT", "LINK", "UNI", "MATIC",
            "AVAX", "ATOM", "FTM", "NEAR", "ALGO", "XRP", "LTC", "BCH"
        ]
        
        for symbol in crypto_symbols:
            if symbol in text_upper or f"${symbol}" in text_upper:
                symbols.append(f"${symbol}")
        
        # Поиск дополнительных символов
        dollar_symbols = re.findall(r'\$[A-Z]{2,6}', text_upper)
        symbols.extend(dollar_symbols)
        
        return list(set(symbols))
    
    def _extract_sentiment_indicators(self, text: str) -> Dict[str, int]:
        """Извлечь индикаторы настроения."""
        text_lower = text.lower()
        
        bullish_terms = ["🚀", "🌙", "💎", "📈", "moon", "lambo", "diamond hands", "hodl", "pump", "bull"]
        bearish_terms = ["📉", "💸", "😭", "dump", "crash", "bear", "paper hands", "sell", "panic", "rekt"]
        neutral_terms = ["📊", "📰", "dip", "consolidation", "analysis", "news", "update"]
        
        return {
            "bullish_count": sum(1 for term in bullish_terms if term in text_lower),
            "bearish_count": sum(1 for term in bearish_terms if term in text_lower),
            "neutral_count": sum(1 for term in neutral_terms if term in text_lower)
        }
    
    def _is_crypto_related(self, text: str) -> bool:
        """Проверить связанность с криптовалютами."""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in self.crypto_keywords)
    
    def _calculate_relevance_score(self, text: str, query: str) -> float:
        """Рассчитать релевантность сообщения."""
        text_lower = text.lower()
        query_lower = query.lower()
        
        # Подсчет вхождений
        count = text_lower.count(query_lower)
        
        # Бонус за crypto-термины
        crypto_bonus = sum(1 for keyword in self.crypto_keywords if keyword in text_lower)
        
        return count * 10 + crypto_bonus
    
    async def health_check(self) -> Dict[str, Any]:
        """Проверка состояния коннектора."""
        try:
            return {
                "status": "healthy" if self._connected else "unhealthy",
                "connected": self._connected,
                "bot_user": str(self.bot.user) if self.bot.user else None,
                "guilds_count": len(self.bot.guilds),
                "cached_messages": len(self._message_cache),
                "circuit_breaker_state": str(self._circuit_breaker.current_state),
                "metrics": self.metrics.get_metrics()
            }
            
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "connected": False
            }
    
    async def disconnect(self) -> None:
        """Закрыть подключение."""
        await self.bot.close()
        self._connected = False
        self.logger.info("Discord connector disconnected")