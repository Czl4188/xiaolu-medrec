"""限流：有 Redis 就跨实例共享计数，没有就退化为进程内。

为什么必须写清楚：进程内计数在多实例下等于「每人 N 倍额度」。
所以生产环境（≥2 实例）必须配 REDIS_URL，启动日志会明确提示。
"""

import threading
import time

from app.core.config import settings


class _MemoryWindow:
    def __init__(self) -> None:
        self._data: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def incr(self, key: str, window_seconds: int) -> int:
        now = time.time()
        with self._lock:
            count, expires = self._data.get(key, (0, 0.0))
            if expires < now:
                count, expires = 0, now + window_seconds
            count += 1
            self._data[key] = (count, expires)
            if len(self._data) > 20_000:  # 防止无界增长
                for k, (_, exp) in list(self._data.items()):
                    if exp < now:
                        self._data.pop(k, None)
            return count


_memory = _MemoryWindow()
_redis = None
_redis_checked = False


def _get_redis():
    global _redis, _redis_checked
    if _redis_checked:
        return _redis
    _redis_checked = True
    if not settings.redis_url:
        return None
    try:
        import redis  # 可选依赖

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        _redis = client
    except Exception:
        _redis = None
    return _redis


def hit(key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
    """返回 (是否放行, 当前计数)。"""
    client = _get_redis()
    if client is not None:
        bucket = f"rl:{key}:{int(time.time() // window_seconds)}"
        try:
            count = client.incr(bucket)
            if count == 1:
                client.expire(bucket, window_seconds + 1)
            return count <= limit, count
        except Exception:
            pass  # Redis 抖动不应该拖垮 API，降级到进程内
    count = _memory.incr(f"{key}:{int(time.time() // window_seconds)}", window_seconds)
    return count <= limit, count


def backend_name() -> str:
    return "redis" if _get_redis() is not None else "memory"
