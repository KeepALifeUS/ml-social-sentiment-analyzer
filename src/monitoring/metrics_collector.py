"""
Сборщик метрик с Context7 Enterprise паттернами

Высокопроизводительный сбор метрик для мониторинга работы
системы анализа настроений в реальном времени.
"""

import time
import threading
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict, deque
import structlog
from prometheus_client import Counter, Histogram, Gauge, Info, CollectorRegistry, start_http_server
from prometheus_client.exposition import generate_latest
import json

logger = structlog.get_logger(__name__)

@dataclass
class MetricValue:
    """Значение метрики с временной меткой."""
    value: Union[int, float]
    timestamp: datetime
    labels: Dict[str, str] = field(default_factory=dict)

class MetricsCollector:
    """
    Enterprise сборщик метрик с Context7 паттернами
    
    Features:
    - Prometheus интеграция
    - High-performance метрики
    - Автоматическое экспонирование
    - Batch обновления
    - Thread-safe операции
    - Retention policy
    """
    
    def __init__(self, component_name: str):
        self.component_name = component_name
        self.logger = logger.bind(component="metrics_collector", name=component_name)
        
        # Thread safety
        self._lock = threading.RLock()
        
        # Prometheus registry для изоляции компонента
        self.registry = CollectorRegistry()
        
        # Инициализация Prometheus метрик
        self._init_prometheus_metrics()
        
        # Внутренние метрики
        self._counters: Dict[str, int] = defaultdict(int)
        self._gauges: Dict[str, float] = defaultdict(float)
        self._histograms: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self._timers: Dict[str, List[float]] = defaultdict(list)
        
        # Конфигурация retention
        self.retention_period = timedelta(hours=24)
        self.max_metrics_per_type = 10000
        
        # Статистика
        self._start_time = time.time()
        self._last_cleanup = time.time()
        
        self.logger.info("Metrics collector initialized", component=component_name)
    
    def _init_prometheus_metrics(self) -> None:
        """Инициализация Prometheus метрик."""
        
        # Префикс для всех метрик
        prefix = f"social_sentiment_{self.component_name}_"
        
        # Основные метрики
        self.prom_messages_total = Counter(
            f"{prefix}messages_total",
            "Total number of messages processed",
            labelnames=["platform", "status"],
            registry=self.registry
        )
        
        self.prom_processing_duration = Histogram(
            f"{prefix}processing_duration_seconds",
            "Time spent processing messages",
            labelnames=["operation", "platform"],
            registry=self.registry
        )
        
        self.prom_queue_size = Gauge(
            f"{prefix}queue_size",
            "Current queue size",
            labelnames=["queue_type"],
            registry=self.registry
        )
        
        self.prom_errors_total = Counter(
            f"{prefix}errors_total",
            "Total number of errors",
            labelnames=["error_type", "platform"],
            registry=self.registry
        )
        
        self.prom_cache_operations = Counter(
            f"{prefix}cache_operations_total",
            "Cache operations",
            labelnames=["operation", "result"],
            registry=self.registry
        )
        
        self.prom_api_requests = Counter(
            f"{prefix}api_requests_total",
            "API requests",
            labelnames=["endpoint", "method", "status"],
            registry=self.registry
        )
        
        self.prom_model_predictions = Counter(
            f"{prefix}model_predictions_total",
            "ML model predictions",
            labelnames=["model", "sentiment"],
            registry=self.registry
        )
        
        self.prom_sentiment_confidence = Histogram(
            f"{prefix}sentiment_confidence",
            "Sentiment analysis confidence scores",
            labelnames=["sentiment", "platform"],
            registry=self.registry
        )
        
        # System info
        self.prom_info = Info(
            f"{prefix}info",
            "Component information",
            registry=self.registry
        )
        
        self.prom_info.info({
            "component": self.component_name,
            "version": "1.0.0",
            "start_time": str(datetime.fromtimestamp(self._start_time))
        })
    
    def increment(self, metric_name: str, value: int = 1, labels: Optional[Dict[str, str]] = None) -> None:
        """Увеличить счетчик."""
        
        with self._lock:
            self._counters[metric_name] += value
            
            # Обновление Prometheus метрик
            if labels:
                if metric_name.endswith("_processed") or metric_name.endswith("_fetched"):
                    self.prom_messages_total.labels(
                        platform=labels.get("platform", "unknown"),
                        status="success"
                    ).inc(value)
                elif "error" in metric_name:
                    self.prom_errors_total.labels(
                        error_type=metric_name,
                        platform=labels.get("platform", "unknown")
                    ).inc(value)
    
    def gauge(self, metric_name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Установить значение gauge метрики."""
        
        with self._lock:
            self._gauges[metric_name] = value
            
            # Обновление Prometheus gauge
            if "queue_size" in metric_name:
                self.prom_queue_size.labels(
                    queue_type=labels.get("type", "default") if labels else "default"
                ).set(value)
    
    def histogram(self, metric_name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Добавить значение в гистограмму."""
        
        with self._lock:
            self._histograms[metric_name].append(value)
            
            # Обновление Prometheus histogram
            if "processing_time" in metric_name:
                self.prom_processing_duration.labels(
                    operation=labels.get("operation", "unknown") if labels else "unknown",
                    platform=labels.get("platform", "unknown") if labels else "unknown"
                ).observe(value / 1000.0)  # Конвертация ms в секунды
            elif "confidence" in metric_name:
                self.prom_sentiment_confidence.labels(
                    sentiment=labels.get("sentiment", "unknown") if labels else "unknown",
                    platform=labels.get("platform", "unknown") if labels else "unknown"
                ).observe(value)
    
    def timer_start(self, timer_name: str) -> str:
        """Запустить таймер."""
        
        timer_id = f"{timer_name}_{time.time()}_{id(threading.current_thread())}"
        self._timers[timer_id] = [time.time()]
        return timer_id
    
    def timer_end(
        self,
        timer_id: str,
        labels: Optional[Dict[str, str]] = None
    ) -> float:
        """Завершить таймер и записать время."""
        
        if timer_id not in self._timers:
            self.logger.warning("Timer not found", timer_id=timer_id)
            return 0.0
        
        start_time = self._timers[timer_id][0]
        duration = (time.time() - start_time) * 1000  # milliseconds
        
        # Извлечение имени таймера
        timer_name = timer_id.split("_")[0]
        
        # Записать в гистограмму
        self.histogram(f"{timer_name}_duration_ms", duration, labels)
        
        # Очистка
        del self._timers[timer_id]
        
        return duration
    
    def record_api_request(
        self,
        endpoint: str,
        method: str,
        status_code: int,
        duration_ms: float
    ) -> None:
        """Записать API запрос."""
        
        # Prometheus
        self.prom_api_requests.labels(
            endpoint=endpoint,
            method=method,
            status=str(status_code)
        ).inc()
        
        # Внутренние метрики
        self.histogram(f"api_request_duration_ms", duration_ms, {
            "endpoint": endpoint,
            "method": method,
            "status": str(status_code)
        })
    
    def record_model_prediction(
        self,
        model_name: str,
        sentiment: str,
        confidence: float,
        processing_time_ms: float
    ) -> None:
        """Записать предсказание модели."""
        
        # Prometheus
        self.prom_model_predictions.labels(
            model=model_name,
            sentiment=sentiment
        ).inc()
        
        self.prom_sentiment_confidence.labels(
            sentiment=sentiment,
            platform="unknown"  # Можно добавить если нужно
        ).observe(confidence)
        
        # Внутренние метрики
        self.histogram("model_processing_time_ms", processing_time_ms, {
            "model": model_name,
            "sentiment": sentiment
        })
    
    def record_cache_operation(
        self,
        operation: str,  # get, set, delete, hit, miss
        result: str      # success, error
    ) -> None:
        """Записать операцию с кэшем."""
        
        self.prom_cache_operations.labels(
            operation=operation,
            result=result
        ).inc()
        
        self.increment(f"cache_{operation}_{result}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Получить все метрики."""
        
        with self._lock:
            current_time = time.time()
            uptime = current_time - self._start_time
            
            # Базовые счетчики
            counters = dict(self._counters)
            
            # Gauge значения
            gauges = dict(self._gauges)
            
            # Статистика по гистограммам
            histograms = {}
            for name, values in self._histograms.items():
                if values:
                    histograms[name] = {
                        "count": len(values),
                        "min": min(values),
                        "max": max(values),
                        "avg": sum(values) / len(values),
                        "p95": self._percentile(values, 95),
                        "p99": self._percentile(values, 99)
                    }
            
            return {
                "component": self.component_name,
                "uptime_seconds": uptime,
                "counters": counters,
                "gauges": gauges,
                "histograms": histograms,
                "active_timers": len(self._timers),
                "last_cleanup": datetime.fromtimestamp(self._last_cleanup).isoformat()
            }
    
    def get_prometheus_metrics(self) -> str:
        """Получить метрики в формате Prometheus."""
        return generate_latest(self.registry).decode()
    
    def _percentile(self, values: deque, percentile: float) -> float:
        """Вычислить перцентиль."""
        
        if not values:
            return 0.0
        
        sorted_values = sorted(values)
        index = int((percentile / 100) * len(sorted_values))
        index = min(index, len(sorted_values) - 1)
        
        return sorted_values[index]
    
    def cleanup_old_metrics(self) -> None:
        """Очистка старых метрик."""
        
        current_time = time.time()
        
        with self._lock:
            # Очистка истории гистограмм (уже ограничена deque maxlen)
            
            # Очистка старых таймеров
            expired_timers = []
            cutoff_time = current_time - 300  # 5 минут
            
            for timer_id, timer_data in self._timers.items():
                if timer_data[0] < cutoff_time:
                    expired_timers.append(timer_id)
            
            for timer_id in expired_timers:
                del self._timers[timer_id]
                self.logger.debug("Expired timer cleaned up", timer_id=timer_id)
            
            self._last_cleanup = current_time
            
            if expired_timers:
                self.logger.info("Metrics cleanup completed", 
                               expired_timers=len(expired_timers))
    
    def health_check(self) -> Dict[str, Any]:
        """Проверка состояния сборщика метрик."""
        
        try:
            metrics = self.get_metrics()
            
            return {
                "status": "healthy",
                "component": self.component_name,
                "metrics_count": {
                    "counters": len(self._counters),
                    "gauges": len(self._gauges),
                    "histograms": len(self._histograms),
                    "active_timers": len(self._timers)
                },
                "uptime_seconds": metrics["uptime_seconds"],
                "prometheus_registry": len(list(self.registry._collector_to_names.keys()))
            }
            
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "component": self.component_name
            }
    
    def export_metrics(self, format: str = "json") -> str:
        """Экспорт метрик в различных форматах."""
        
        if format.lower() == "json":
            metrics = self.get_metrics()
            return json.dumps(metrics, indent=2)
        elif format.lower() == "prometheus":
            return self.get_prometheus_metrics()
        else:
            raise ValueError(f"Unsupported format: {format}")
    
    def reset_metrics(self) -> None:
        """Сброс всех метрик (осторожно!)."""
        
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._timers.clear()
            
            self.logger.warning("All metrics have been reset")

# Глобальный registry для всех компонентов
GLOBAL_METRICS_REGISTRY: Dict[str, MetricsCollector] = {}
GLOBAL_REGISTRY_LOCK = threading.Lock()

def get_metrics_collector(component_name: str) -> MetricsCollector:
    """Получить или создать сборщик метрик для компонента."""
    
    with GLOBAL_REGISTRY_LOCK:
        if component_name not in GLOBAL_METRICS_REGISTRY:
            GLOBAL_METRICS_REGISTRY[component_name] = MetricsCollector(component_name)
        
        return GLOBAL_METRICS_REGISTRY[component_name]

def start_prometheus_server(port: int = 9090) -> None:
    """Запуск Prometheus HTTP сервера."""
    
    try:
        start_http_server(port)
        logger.info("Prometheus metrics server started", port=port)
    except Exception as e:
        logger.error("Failed to start Prometheus server", port=port, error=str(e))
        raise

def get_all_metrics() -> Dict[str, Dict[str, Any]]:
    """Получить метрики всех компонентов."""
    
    with GLOBAL_REGISTRY_LOCK:
        return {
            name: collector.get_metrics()
            for name, collector in GLOBAL_METRICS_REGISTRY.items()
        }