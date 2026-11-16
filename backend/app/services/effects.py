"""Việc cần làm sau khi xử lý nghiệp vụ, tách khỏi tầng MQTT để dễ test.

Dịch vụ trả về danh sách Command; Bridge (app/bridge.py) mới là nơi gửi chúng xuống tủ.
"""
from dataclasses import dataclass, field


@dataclass
class Command:
    """Một RPC cloud -> tủ (docs/CONTRACT.md mục 5)."""

    locker_id: str
    method: str
    params: dict = field(default_factory=dict)
