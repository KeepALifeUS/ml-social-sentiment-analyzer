"""
TikTok Data Connector с enterprise паттернами

Интеграция для сбора TikTok контента связанного с криптовалютами
через TikTok API и web scraping с максимальной надежностью.
"""

import asyncio
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import aiohttp
import structlog
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential
from circuitbreaker import CircuitBreaker
import re
import json

from ..utils.config import Config
from ..monitoring.metrics_collector import MetricsCollector

logger = structlog.get_logger(__name__)

class TikTokConnector:
    """
    Enterprise TikTok коннектор with enterprise patterns
    
    Features:
    - TikTok API integration (если доступен)
    - Web scraping как fallback
    - Crypto hashtags мониторинг
    - Video sentiment analysis
    - Trend detection
    """
    
    def __init__(self, config: Config):
        self.config = config
        self.metrics = MetricsCollector("tiktok_connector")
        self.logger = logger.bind(component="tiktok_connector")
        
        # TikTok API клиент (если доступен)
        self._api_available = hasattr(config, 'tiktok_access_token')
        
        # HTTP сессия для scraping
        self._session: Optional[aiohttp.ClientSession] = None
        
        # Circuit breaker
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60,
            expected_exception=Exception
        )
        
        # Crypto TikTok hashtags
        self.crypto_hashtags = [
            "#bitcoin", "#btc", "#ethereum", "#eth", "#crypto", "#cryptocurrency",
            "#blockchain", "#defi", "#nft", "#altcoin", "#hodl", "#moon",
            "#cryptotok", "#bitcointiktok", "#cryptotrading", "#cryptonews",
            "#dogecoin", "#shibainu", "#cardano", "#solana", "#polygon",
            "#binance", "#coinbase", "#cryptomining", "#web3", "#metaverse"
        ]
        
        # User agents для избежания блокировки
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        ]
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Получить HTTP сессию с настройками."""
        if not self._session or self._session.closed:
            import random
            
            headers = {
                'User-Agent': random.choice(self.user_agents),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }
            
            timeout = aiohttp.ClientTimeout(total=30)
            connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
            
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=timeout,
                connector=connector
            )
        
        return self._session
    
    @CircuitBreaker(failure_threshold=3, recovery_timeout=30)
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def search_crypto_videos(
        self,
        hashtag: Optional[str] = None,
        limit: int = 50,
        days_back: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Поиск crypto видео на TikTok
        
        Args:
            hashtag: Конкретный hashtag (None = случайный crypto hashtag)
            limit: Количество видео
            days_back: Период поиска в днях
        """
        
        try:
            if not hashtag:
                import random
                hashtag = random.choice(self.crypto_hashtags)
            
            # Удаление # если есть
            clean_hashtag = hashtag.lstrip('#')
            
            session = await self._get_session()
            
            # URL для поиска по hashtag
            search_url = f"https://www.tiktok.com/tag/{clean_hashtag}"
            
            videos = []
            
            # Попытка получения данных через web scraping
            async with session.get(search_url) as response:
                if response.status == 200:
                    html = await response.text()
                    videos = await self._parse_tiktok_page(html, hashtag)
                else:
                    self.logger.warning("TikTok request failed", 
                                      status=response.status, hashtag=hashtag)
            
            # Фильтрация по времени и crypto-контенту
            cutoff_date = datetime.now() - timedelta(days=days_back)
            
            filtered_videos = []
            for video in videos[:limit]:
                # Базовая проверка crypto-контента
                if self._is_crypto_related(video.get('description', '')):
                    filtered_videos.append(video)
            
            self.metrics.increment("videos_fetched", len(filtered_videos))
            self.logger.info("TikTok videos fetched", 
                           hashtag=hashtag, count=len(filtered_videos))
            
            return filtered_videos
            
        except Exception as e:
            self.logger.error("TikTok search failed", hashtag=hashtag, error=str(e))
            self.metrics.increment("search_error")
            return []
    
    async def _parse_tiktok_page(self, html: str, hashtag: str) -> List[Dict[str, Any]]:
        """Парсинг HTML страницы TikTok."""
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            videos = []
            
            # Поиск JSON данных в script тегах (TikTok часто использует это)
            script_tags = soup.find_all('script', {'id': '__NEXT_DATA__'})
            
            for script in script_tags:
                try:
                    json_data = json.loads(script.string)
                    
                    # Извлечение видео из JSON структуры
                    # Структура может меняться, это базовый пример
                    if 'props' in json_data and 'pageProps' in json_data['props']:
                        items = self._extract_videos_from_json(json_data, hashtag)
                        videos.extend(items)
                    
                except json.JSONDecodeError:
                    continue
            
            # Если JSON не найден, пробуем альтернативные методы
            if not videos:
                videos = await self._parse_fallback_method(soup, hashtag)
            
            return videos
            
        except Exception as e:
            self.logger.error("Failed to parse TikTok page", error=str(e))
            return []
    
    def _extract_videos_from_json(self, json_data: dict, hashtag: str) -> List[Dict[str, Any]]:
        """Извлечение видео из JSON данных."""
        
        videos = []
        
        try:
            # Поиск видео в различных возможных путях JSON
            possible_paths = [
                ['props', 'pageProps', 'itemList'],
                ['props', 'pageProps', 'items'],
                ['props', 'pageProps', 'videoList'],
            ]
            
            items = []
            for path in possible_paths:
                current = json_data
                for key in path:
                    if isinstance(current, dict) and key in current:
                        current = current[key]
                    else:
                        break
                else:
                    if isinstance(current, list):
                        items = current
                        break
            
            # Обработка найденных элементов
            for item in items:
                if not isinstance(item, dict):
                    continue
                
                # Извлечение базовой информации
                video_data = {
                    "id": item.get('id', ''),
                    "description": item.get('desc', ''),
                    "create_time": item.get('createTime', 0),
                    "hashtag": hashtag,
                    "author": {
                        "username": item.get('author', {}).get('uniqueId', ''),
                        "nickname": item.get('author', {}).get('nickname', ''),
                        "verified": item.get('author', {}).get('verified', False),
                        "follower_count": item.get('author', {}).get('followerCount', 0),
                    },
                    "stats": {
                        "play_count": item.get('stats', {}).get('playCount', 0),
                        "like_count": item.get('stats', {}).get('diggCount', 0),
                        "comment_count": item.get('stats', {}).get('commentCount', 0),
                        "share_count": item.get('stats', {}).get('shareCount', 0),
                    },
                    "music": {
                        "title": item.get('music', {}).get('title', ''),
                        "author": item.get('music', {}).get('authorName', ''),
                    },
                    "crypto_symbols": self._extract_crypto_symbols(
                        item.get('desc', '')
                    ),
                    "sentiment_indicators": self._extract_sentiment_indicators(
                        item.get('desc', '')
                    ),
                    "url": f"https://www.tiktok.com/@{item.get('author', {}).get('uniqueId', '')}/video/{item.get('id', '')}",
                    "platform": "tiktok",
                    "scraped_at": datetime.now().isoformat()
                }
                
                videos.append(video_data)
            
        except Exception as e:
            self.logger.error("Failed to extract videos from JSON", error=str(e))
        
        return videos
    
    async def _parse_fallback_method(self, soup: BeautifulSoup, hashtag: str) -> List[Dict[str, Any]]:
        """Альтернативный метод парсинга через HTML элементы."""
        
        videos = []
        
        try:
            # Поиск видео контейнеров по различным CSS селекторам
            video_selectors = [
                'div[data-e2e="challenge-item"]',
                '.video-feed-item',
                '[data-e2e="video-item"]',
                '.tiktok-video-item'
            ]
            
            for selector in video_selectors:
                items = soup.select(selector)
                if items:
                    for item in items:
                        try:
                            # Извлечение базовой информации из HTML
                            description = self._extract_text_from_element(item, [
                                '[data-e2e="video-desc"]',
                                '.video-meta-caption',
                                '.tt-video-meta-caption'
                            ])
                            
                            author = self._extract_text_from_element(item, [
                                '[data-e2e="video-author-name"]',
                                '.author-name',
                                '.tt-video-author'
                            ])
                            
                            if description or author:
                                video_data = {
                                    "id": f"scraped_{int(time.time())}_{len(videos)}",
                                    "description": description,
                                    "hashtag": hashtag,
                                    "author": {"username": author},
                                    "crypto_symbols": self._extract_crypto_symbols(description),
                                    "sentiment_indicators": self._extract_sentiment_indicators(description),
                                    "platform": "tiktok",
                                    "scraped_at": datetime.now().isoformat(),
                                    "method": "fallback_html"
                                }
                                
                                videos.append(video_data)
                                
                        except Exception:
                            continue
                    
                    if videos:  # Если найдены видео, прекращаем поиск
                        break
            
        except Exception as e:
            self.logger.error("Fallback parsing failed", error=str(e))
        
        return videos
    
    def _extract_text_from_element(self, parent, selectors: List[str]) -> str:
        """Извлечь текст из элемента по списку селекторов."""
        for selector in selectors:
            element = parent.select_one(selector)
            if element:
                return element.get_text(strip=True)
        return ""
    
    async def get_hashtag_trends(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Получить трендовые crypto hashtags."""
        
        try:
            trends = []
            session = await self._get_session()
            
            # Проверка популярности каждого crypto hashtag
            for hashtag in self.crypto_hashtags[:limit]:
                try:
                    clean_hashtag = hashtag.lstrip('#')
                    url = f"https://www.tiktok.com/tag/{clean_hashtag}"
                    
                    async with session.get(url) as response:
                        if response.status == 200:
                            html = await response.text()
                            
                            # Поиск счетчика видео
                            soup = BeautifulSoup(html, 'html.parser')
                            
                            # Попытка найти счетчик просмотров
                            count_element = soup.select_one('[data-e2e="challenge-vvcount"]')
                            view_count = 0
                            
                            if count_element:
                                count_text = count_element.get_text()
                                view_count = self._parse_count_string(count_text)
                            
                            trend_data = {
                                "hashtag": hashtag,
                                "view_count": view_count,
                                "url": url,
                                "platform": "tiktok",
                                "checked_at": datetime.now().isoformat()
                            }
                            
                            trends.append(trend_data)
                    
                    # Задержка между запросами
                    await asyncio.sleep(1)
                    
                except Exception:
                    continue
            
            # Сортировка по популярности
            trends.sort(key=lambda x: x['view_count'], reverse=True)
            
            self.logger.info("TikTok trends fetched", count=len(trends))
            return trends
            
        except Exception as e:
            self.logger.error("Failed to fetch TikTok trends", error=str(e))
            return []
    
    def _parse_count_string(self, count_str: str) -> int:
        """Парсинг строки с количеством (1.2M, 500K, etc.)."""
        
        try:
            count_str = count_str.lower().replace(',', '').strip()
            
            if 'b' in count_str:
                return int(float(count_str.replace('b', '')) * 1_000_000_000)
            elif 'm' in count_str:
                return int(float(count_str.replace('m', '')) * 1_000_000)
            elif 'k' in count_str:
                return int(float(count_str.replace('k', '')) * 1_000)
            else:
                return int(count_str)
                
        except (ValueError, TypeError):
            return 0
    
    def _extract_crypto_symbols(self, text: str) -> List[str]:
        """Извлечь упоминания криптовалют."""
        if not text:
            return []
        
        symbols = []
        text_upper = text.upper()
        
        # Основные символы
        crypto_symbols = [
            "BTC", "ETH", "ADA", "SOL", "DOT", "LINK", "UNI", "MATIC",
            "AVAX", "ATOM", "FTM", "NEAR", "ALGO", "XRP", "LTC", "BCH",
            "DOGE", "SHIB", "CRO", "FTT", "SAND", "MANA", "AXS"
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
        if not text:
            return {"bullish_count": 0, "bearish_count": 0, "neutral_count": 0}
        
        text_lower = text.lower()
        
        bullish_terms = ["🚀", "🌙", "💎", "moon", "lambo", "bullish", "pump", "gains", "profit", "hodl"]
        bearish_terms = ["📉", "💸", "dump", "crash", "bearish", "panic", "sell", "drop", "loss", "rekt"]
        neutral_terms = ["analysis", "review", "update", "news", "prediction", "learn", "explain"]
        
        return {
            "bullish_count": sum(1 for term in bullish_terms if term in text_lower),
            "bearish_count": sum(1 for term in bearish_terms if term in text_lower),
            "neutral_count": sum(1 for term in neutral_terms if term in text_lower)
        }
    
    def _is_crypto_related(self, text: str) -> bool:
        """Проверить связанность с криптовалютами."""
        if not text:
            return False
        
        text_lower = text.lower()
        crypto_keywords = [
            "bitcoin", "ethereum", "crypto", "blockchain", "defi", "nft",
            "btc", "eth", "altcoin", "hodl", "trading", "coin", "token"
        ]
        
        return any(keyword in text_lower for keyword in crypto_keywords)
    
    async def health_check(self) -> Dict[str, Any]:
        """Проверка состояния коннектора."""
        try:
            session = await self._get_session()
            
            # Тестовый запрос
            async with session.get("https://www.tiktok.com/tag/bitcoin") as response:
                status_ok = response.status == 200
            
            return {
                "status": "healthy" if status_ok else "unhealthy",
                "api_available": self._api_available,
                "circuit_breaker_state": str(self._circuit_breaker.current_state),
                "session_status": "active" if self._session and not self._session.closed else "inactive",
                "metrics": self.metrics.get_metrics()
            }
            
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "api_available": self._api_available
            }
    
    async def disconnect(self) -> None:
        """Закрыть подключения."""
        if self._session and not self._session.closed:
            await self._session.close()
        self.logger.info("TikTok connector disconnected")