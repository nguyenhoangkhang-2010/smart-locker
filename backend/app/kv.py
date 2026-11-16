"""Kho khóa-giá trị có TTL: bộ đếm sai OTP, khóa ngăn, phiên đăng nhập, refresh token.

RedisKV dùng khi có REDIS_URL; MemoryKV dùng khi phát triển/test (mất khi khởi động lại).
"""
import time
from typing import Callable, Optional


class MemoryKV:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._data: dict[str, tuple[str, Optional[float]]] = {}
        self._hashes: dict[str, dict[str, str]] = {}

    def _live(self, key: str) -> Optional[str]:
        item = self._data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and expires <= self._clock():
            del self._data[key]
            return None
        return value

    def get(self, key: str) -> Optional[str]:
        return self._live(key)

    def set(self, key: str, value: str, ttl: Optional[float] = None) -> None:
        self._data[key] = (value, None if ttl is None else self._clock() + ttl)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def exists(self, key: str) -> bool:
        return self._live(key) is not None

    def incr(self, key: str, ttl: Optional[float] = None) -> int:
        """Tăng bộ đếm; TTL đặt lại mỗi lần tăng."""
        count = int(self._live(key) or 0) + 1
        self.set(key, str(count), ttl)
        return count

    def ttl(self, key: str) -> Optional[float]:
        item = self._data.get(key)
        if item is None or self._live(key) is None:
            return None
        return None if item[1] is None else max(0.0, item[1] - self._clock())

    # ----- hash (Redis HSET/HGETALL): trạng thái từng tủ cho dashboard thời gian thực -----
    def hset(self, key: str, mapping: dict) -> None:
        h = self._hashes.setdefault(key, {})
        h.update({str(k): str(v) for k, v in mapping.items()})

    def hgetall(self, key: str) -> dict:
        return dict(self._hashes.get(key, {}))


class RedisKV:
    def __init__(self, url: str, client=None):
        import redis

        # client: cho test chèn Redis giả (fakeredis); chạy thật thì để trống
        self._r = client or redis.Redis.from_url(url, decode_responses=True)

    def get(self, key):
        return self._r.get(key)

    def set(self, key, value, ttl=None):
        self._r.set(key, value, ex=None if ttl is None else max(1, int(ttl)))

    def delete(self, key):
        self._r.delete(key)

    def exists(self, key):
        return bool(self._r.exists(key))

    def incr(self, key, ttl=None):
        pipe = self._r.pipeline()
        pipe.incr(key)
        if ttl is not None:
            pipe.expire(key, max(1, int(ttl)))
        return int(pipe.execute()[0])

    def ttl(self, key):
        value = self._r.ttl(key)
        return None if value < 0 else float(value)

    def hset(self, key, mapping):
        self._r.hset(key, mapping={str(k): str(v) for k, v in mapping.items()})

    def hgetall(self, key):
        return self._r.hgetall(key)


def make_kv(redis_url: str = ""):
    return RedisKV(redis_url) if redis_url else MemoryKV()
