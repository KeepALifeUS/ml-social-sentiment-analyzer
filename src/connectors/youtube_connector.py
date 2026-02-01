"""
YouTube API Connector с Context7 Enterprise паттернами

Интеграция с YouTube Data API v3 для мониторинга crypto-каналов,
комментариев и трендов с полной надежностью.
"""

import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import structlog
from googleapiclient.discovery import build
from tenacity import retry, stop_after_attempt, wait_exponential
from circuitbreaker import CircuitBreaker
import re

from ..utils.config import Config
from ..monitoring.metrics_collector import MetricsCollector

logger = structlog.get_logger(__name__)

class YouTubeConnector:
    """
    Enterprise YouTube API коннектор с Context7 паттернами
    
    Features:
    - YouTube Data API v3 integration
    - Crypto channels monitoring
    - Comments sentiment tracking
    - Video trend analysis
    - Channel statistics
    """
    
    def __init__(self, config: Config):
        self.config = config
        self.metrics = MetricsCollector("youtube_connector")
        self.logger = logger.bind(component="youtube_connector")
        
        # YouTube API клиент
        self._youtube = build(
            'youtube',
            'v3',
            developerKey=config.youtube_api_key
        )
        
        # Circuit breaker
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60,
            expected_exception=Exception
        )
        
        # Crypto YouTube каналы
        self.crypto_channels = [
            "coin_bureau",           # Coin Bureau
            "aantonop",             # Andreas Antonopoulos
            "IvanOnTech",           # Ivan on Tech
            "CryptosRUs",           # Crypto's R Us
            "BitcoinwithMichael",   # Bitcoin with Michael
            "DataDash",             # DataDash
            "AltcoinDaily",         # Altcoin Daily
            "TheModernInvestor",    # The Modern Investor
            "99Bitcoins",           # 99Bitcoins
            "BlockchainBrad"        # Blockchain Brad
        ]
        
        # Crypto keywords
        self.crypto_keywords = [
            "bitcoin", "ethereum", "crypto", "blockchain", "defi", "nft",
            "altcoin", "hodl", "trading", "investment", "bull", "bear",
            "pump", "dump", "moon", "lambo", "whale", "satoshi"
        ]
    
    @CircuitBreaker(failure_threshold=3, recovery_timeout=30)
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def search_crypto_videos(
        self,
        query: Optional[str] = None,
        published_after: Optional[datetime] = None,
        max_results: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Поиск crypto-видео на YouTube
        
        Args:
            query: Поисковый запрос (None = автоматический crypto query)
            published_after: Дата после которой публиковать
            max_results: Максимальное количество результатов
        """
        
        try:
            if not query:
                query = "cryptocurrency bitcoin ethereum trading analysis"
            
            # Параметры поиска
            search_params = {
                'part': 'id,snippet',
                'q': query,
                'type': 'video',
                'order': 'relevance',
                'maxResults': min(max_results, 50),  # YouTube API limit
                'safeSearch': 'none',
                'videoDefinition': 'any',
                'videoDuration': 'any'
            }
            
            if published_after:
                search_params['publishedAfter'] = published_after.strftime('%Y-%m-%dT%H:%M:%SZ')
            
            # Выполнение поиска
            search_response = self._youtube.search().list(**search_params).execute()
            
            videos = []
            video_ids = []
            
            # Сбор ID видео для получения статистики
            for item in search_response.get('items', []):
                if item['id']['kind'] == 'youtube#video':
                    video_ids.append(item['id']['videoId'])
            
            # Получение статистики видео
            if video_ids:
                stats_response = self._youtube.videos().list(
                    part='statistics,contentDetails',
                    id=','.join(video_ids)
                ).execute()
                
                stats_dict = {
                    item['id']: item 
                    for item in stats_response.get('items', [])
                }
            else:
                stats_dict = {}
            
            # Обработка результатов
            for item in search_response.get('items', []):
                if item['id']['kind'] != 'youtube#video':
                    continue
                
                video_id = item['id']['videoId']
                snippet = item['snippet']
                stats = stats_dict.get(video_id, {}).get('statistics', {})
                content_details = stats_dict.get(video_id, {}).get('contentDetails', {})
                
                video_data = {
                    "id": video_id,
                    "title": snippet['title'],
                    "description": snippet['description'],
                    "published_at": snippet['publishedAt'],
                    "channel_id": snippet['channelId'],
                    "channel_title": snippet['channelTitle'],
                    "thumbnails": snippet['thumbnails'],
                    "view_count": int(stats.get('viewCount', 0)),
                    "like_count": int(stats.get('likeCount', 0)),
                    "comment_count": int(stats.get('commentCount', 0)),
                    "duration": content_details.get('duration', ''),
                    "crypto_symbols": self._extract_crypto_symbols(
                        snippet['title'] + ' ' + snippet['description']
                    ),
                    "sentiment_indicators": self._extract_sentiment_indicators(
                        snippet['title'] + ' ' + snippet['description']
                    ),
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "platform": "youtube"
                }
                
                videos.append(video_data)
            
            self.metrics.increment("videos_fetched", len(videos))
            self.logger.info("Videos fetched successfully", count=len(videos))
            
            return videos
            
        except Exception as e:
            self.logger.error("Video search failed", error=str(e), query=query)
            self.metrics.increment("search_error")
            raise
    
    async def get_video_comments(
        self,
        video_id: str,
        max_results: int = 100,
        crypto_filter: bool = True
    ) -> List[Dict[str, Any]]:
        """Получить комментарии к видео с crypto фильтром."""
        
        try:
            # Получение комментариев
            comments_response = self._youtube.commentThreads().list(
                part='snippet,replies',
                videoId=video_id,
                maxResults=min(max_results, 100),
                order='relevance',
                textFormat='plainText'
            ).execute()
            
            comments = []
            
            for item in comments_response.get('items', []):
                top_comment = item['snippet']['topLevelComment']['snippet']
                
                # Фильтрация по crypto если включена
                comment_text = top_comment['textDisplay']
                if crypto_filter and not self._is_crypto_related(comment_text):
                    continue
                
                comment_data = {
                    "id": item['snippet']['topLevelComment']['id'],
                    "text": comment_text,
                    "author_name": top_comment['authorDisplayName'],
                    "author_channel_id": top_comment.get('authorChannelId', {}).get('value', ''),
                    "like_count": top_comment['likeCount'],
                    "published_at": top_comment['publishedAt'],
                    "updated_at": top_comment['updatedAt'],
                    "video_id": video_id,
                    "crypto_symbols": self._extract_crypto_symbols(comment_text),
                    "sentiment_indicators": self._extract_sentiment_indicators(comment_text),
                    "platform": "youtube"
                }
                
                # Добавление ответов
                replies = []
                if 'replies' in item:
                    for reply_item in item['replies']['comments']:
                        reply_snippet = reply_item['snippet']
                        reply_text = reply_snippet['textDisplay']
                        
                        if not crypto_filter or self._is_crypto_related(reply_text):
                            reply_data = {
                                "id": reply_item['id'],
                                "text": reply_text,
                                "author_name": reply_snippet['authorDisplayName'],
                                "like_count": reply_snippet['likeCount'],
                                "published_at": reply_snippet['publishedAt'],
                                "crypto_symbols": self._extract_crypto_symbols(reply_text),
                                "platform": "youtube"
                            }
                            replies.append(reply_data)
                
                comment_data["replies"] = replies
                comments.append(comment_data)
            
            self.logger.info("Comments fetched", video_id=video_id, count=len(comments))
            return comments
            
        except Exception as e:
            self.logger.error("Failed to fetch comments", video_id=video_id, error=str(e))
            return []
    
    async def get_channel_videos(
        self,
        channel_id: str,
        max_results: int = 50,
        published_after: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Получить видео с канала."""
        
        try:
            # Параметры поиска по каналу
            search_params = {
                'part': 'id,snippet',
                'channelId': channel_id,
                'type': 'video',
                'order': 'date',
                'maxResults': min(max_results, 50)
            }
            
            if published_after:
                search_params['publishedAfter'] = published_after.strftime('%Y-%m-%dT%H:%M:%SZ')
            
            # Поиск видео канала
            search_response = self._youtube.search().list(**search_params).execute()
            
            videos = []
            for item in search_response.get('items', []):
                if item['id']['kind'] != 'youtube#video':
                    continue
                
                snippet = item['snippet']
                video_data = {
                    "id": item['id']['videoId'],
                    "title": snippet['title'],
                    "description": snippet['description'],
                    "published_at": snippet['publishedAt'],
                    "channel_id": channel_id,
                    "channel_title": snippet['channelTitle'],
                    "thumbnails": snippet['thumbnails'],
                    "crypto_symbols": self._extract_crypto_symbols(
                        snippet['title'] + ' ' + snippet['description']
                    ),
                    "url": f"https://www.youtube.com/watch?v={item['id']['videoId']}",
                    "platform": "youtube"
                }
                
                videos.append(video_data)
            
            self.logger.info("Channel videos fetched", 
                           channel_id=channel_id, count=len(videos))
            return videos
            
        except Exception as e:
            self.logger.error("Failed to fetch channel videos", 
                           channel_id=channel_id, error=str(e))
            return []
    
    async def get_channel_statistics(self, channel_id: str) -> Dict[str, Any]:
        """Получить статистику канала."""
        
        try:
            channel_response = self._youtube.channels().list(
                part='statistics,snippet,brandingSettings',
                id=channel_id
            ).execute()
            
            if not channel_response.get('items'):
                return {}
            
            channel = channel_response['items'][0]
            snippet = channel['snippet']
            stats = channel['statistics']
            
            return {
                "id": channel_id,
                "title": snippet['title'],
                "description": snippet['description'],
                "published_at": snippet['publishedAt'],
                "country": snippet.get('country', ''),
                "view_count": int(stats['viewCount']),
                "subscriber_count": int(stats['subscriberCount']),
                "video_count": int(stats['videoCount']),
                "thumbnails": snippet['thumbnails'],
                "platform": "youtube"
            }
            
        except Exception as e:
            self.logger.error("Failed to get channel statistics", 
                           channel_id=channel_id, error=str(e))
            return {}
    
    async def get_trending_crypto_videos(
        self,
        region_code: str = 'US',
        max_results: int = 50
    ) -> List[Dict[str, Any]]:
        """Получить трендовые crypto видео."""
        
        try:
            # Получение трендовых видео
            videos_response = self._youtube.videos().list(
                part='id,snippet,statistics',
                chart='mostPopular',
                regionCode=region_code,
                maxResults=max_results,
                videoCategoryId='25'  # News & Politics (часто содержит crypto)
            ).execute()
            
            crypto_videos = []
            
            for item in videos_response.get('items', []):
                snippet = item['snippet']
                title_desc = snippet['title'] + ' ' + snippet['description']
                
                # Фильтр crypto-контента
                if self._is_crypto_related(title_desc):
                    stats = item['statistics']
                    
                    video_data = {
                        "id": item['id'],
                        "title": snippet['title'],
                        "description": snippet['description'],
                        "published_at": snippet['publishedAt'],
                        "channel_id": snippet['channelId'],
                        "channel_title": snippet['channelTitle'],
                        "view_count": int(stats.get('viewCount', 0)),
                        "like_count": int(stats.get('likeCount', 0)),
                        "comment_count": int(stats.get('commentCount', 0)),
                        "crypto_symbols": self._extract_crypto_symbols(title_desc),
                        "sentiment_indicators": self._extract_sentiment_indicators(title_desc),
                        "trending_rank": len(crypto_videos) + 1,
                        "url": f"https://www.youtube.com/watch?v={item['id']}",
                        "platform": "youtube"
                    }
                    
                    crypto_videos.append(video_data)
            
            self.logger.info("Trending crypto videos fetched", count=len(crypto_videos))
            return crypto_videos
            
        except Exception as e:
            self.logger.error("Failed to fetch trending videos", error=str(e))
            return []
    
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
        
        bullish_terms = ["moon", "lambo", "bullish", "pump", "rocket", "gains", "profit", "hodl"]
        bearish_terms = ["dump", "crash", "bearish", "panic", "sell", "drop", "down", "loss"]
        neutral_terms = ["analysis", "review", "update", "news", "prediction", "forecast"]
        
        return {
            "bullish_count": sum(1 for term in bullish_terms if term in text_lower),
            "bearish_count": sum(1 for term in bearish_terms if term in text_lower),
            "neutral_count": sum(1 for term in neutral_terms if term in text_lower)
        }
    
    def _is_crypto_related(self, text: str) -> bool:
        """Проверить связанность с криптовалютами."""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in self.crypto_keywords)
    
    async def health_check(self) -> Dict[str, Any]:
        """Проверка состояния коннектора."""
        try:
            # Тестовый запрос
            test_response = self._youtube.search().list(
                part='id',
                q='bitcoin',
                type='video',
                maxResults=1
            ).execute()
            
            return {
                "status": "healthy" if test_response else "unhealthy",
                "api_quota_exceeded": False,  # Можно добавить логику проверки квоты
                "circuit_breaker_state": str(self._circuit_breaker.current_state),
                "metrics": self.metrics.get_metrics()
            }
            
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "circuit_breaker_state": str(self._circuit_breaker.current_state)
            }