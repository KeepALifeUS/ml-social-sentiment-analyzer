"""
Enterprise Social Media Sentiment Analyzer for Crypto Markets

Этот модуль предоставляет полнофункциональную систему анализа настроений
в социальных сетях для криптовалютных рынков with
enterprise-паттернов.

Основные компоненты:
- Коннекторы к социальным платформам (Twitter, Reddit, Telegram и др.)
- Система потоковой обработки данных в реальном времени
- ML-модели для анализа настроений с поддержкой многих языков
- Агрегация данных с различных источников
- API для интеграции с торговыми системами
- Система мониторинга и оповещений
"""

from typing import Dict, Any
import structlog

__version__ = "1.0.0"
__author__ = "ML-Framework ML Team"
__email__ = "ml-team@ml-framework.io"

# Настройка логирования standard
logger = structlog.get_logger(__name__)

# Экспорт основных компонентов
from .api.rest_api import SocialSentimentAPI
from .analysis.realtime_analyzer import RealtimeSentimentAnalyzer
from .streaming.twitter_stream import TwitterStreamProcessor
from .aggregation.sentiment_aggregator import SentimentAggregator
from .trends.trending_topics import TrendingTopicsDetector
from .monitoring.dashboard_backend import MonitoringDashboard

__all__ = [
    "SocialSentimentAPI",
    "RealtimeSentimentAnalyzer", 
    "TwitterStreamProcessor",
    "SentimentAggregator",
    "TrendingTopicsDetector",
    "MonitoringDashboard",
    "__version__",
]

def get_version() -> str:
    """Получить версию пакета."""
    return __version__

def get_system_info() -> Dict[str, Any]:
    """Получить информацию о системе."""
    import sys
    import platform
    
    return {
        "version": __version__,
        "python_version": sys.version,
        "platform": platform.platform(),
        "architecture": platform.architecture(),
        "machine": platform.machine(),
    }

# Health Check
def health_check() -> Dict[str, Any]:
    """Проверка состояния системы."""
    try:
        import torch
        import transformers
        import fastapi
        
        return {
            "status": "healthy",
            "components": {
                "torch": torch.__version__,
                "transformers": transformers.__version__,
                "fastapi": fastapi.__version__,
            },
            "system_info": get_system_info(),
        }
    except Exception as e:
        logger.error("Health check failed", error=str(e))
        return {
            "status": "unhealthy",
            "error": str(e),
            "system_info": get_system_info(),
        }

logger.info("Social Sentiment Analyzer initialized", version=__version__)