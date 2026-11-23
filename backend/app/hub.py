"""Đẩy cập nhật thời gian thực tới dashboard qua WebSocket (đề: "API REST/WebSocket -> giao diện").

Sự kiện nghiệp vụ chạy ở luồng của paho-mqtt hoặc của bộ lập lịch, còn WebSocket sống trong vòng lặp asyncio của
uvicorn, nên mỗi kết nối giữ (vòng lặp, hàng đợi) của riêng nó và `publish` chuyển tin qua call_soon_threadsafe.
Tin chỉ báo "có gì đó đổi" (loại + mã tủ); dashboard tự gọi lại REST để lấy dữ liệu đã được kiểm quyền.
"""
import asyncio
import threading
from dataclasses import dataclass


@dataclass(eq=False)
class _Conn:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue
    sub: str = ""                  # tài khoản sở hữu kết nối (để đóng khi thu hồi phiên)


class Hub:
    MAX_QUEUE = 100

    def __init__(self):
        self._conns: set[_Conn] = set()
        self._lock = threading.Lock()

    def add(self, sub: str = "") -> _Conn:
        conn = _Conn(asyncio.get_running_loop(), asyncio.Queue(self.MAX_QUEUE), sub)
        with self._lock:
            self._conns.add(conn)
        return conn

    def remove(self, conn: _Conn) -> None:
        with self._lock:
            self._conns.discard(conn)

    def count(self) -> int:
        with self._lock:
            return len(self._conns)

    def publish(self, message: dict) -> None:
        with self._lock:
            conns = list(self._conns)
        for conn in conns:
            try:
                conn.loop.call_soon_threadsafe(self._put, conn, message)
            except RuntimeError:     # vòng lặp đã đóng
                self.remove(conn)

    def close_user(self, sub: str) -> None:
        """Thu hồi phiên: bảo mọi kết nối của tài khoản này tự đóng (mã 4401), máy khách phải nối lại bằng token mới."""
        with self._lock:
            conns = [c for c in self._conns if c.sub == str(sub)]
        for conn in conns:
            try:
                conn.loop.call_soon_threadsafe(self._put, conn, {"type": "__close__"})
            except RuntimeError:
                self.remove(conn)

    @staticmethod
    def _put(conn: _Conn, message: dict) -> None:
        if conn.queue.full():        # client chậm: bỏ tin cũ nhất, dashboard sẽ tải lại toàn bộ
            conn.queue.get_nowait()
        conn.queue.put_nowait(message)


hub = Hub()
