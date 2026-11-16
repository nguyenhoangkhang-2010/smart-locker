"""Đối tượng dùng chung của tiến trình: kho KV và cầu nối MQTT. main.py gán lúc khởi động,
test gán bản giả."""
from typing import Optional

kv = None                       # MemoryKV | RedisKV
bridge: Optional[object] = None  # app.bridge.Bridge

listeners: list = []             # hàm nhận một dict sự kiện; dashboard WebSocket đăng ký ở đây


def notify(message: dict) -> None:
    """Báo cho các bên đang lắng nghe (WebSocket). Không bao giờ làm hỏng luồng xử lý chính."""
    for fn in list(listeners):
        try:
            fn(message)
        except Exception:  # noqa: BLE001
            pass
