"""Giao diện: nút bị khóa phải trông khác nút bấm được (lỗi đã gặp: "Xác minh tôi" bị khóa nhưng trông như dùng được)."""
import re

from tests.test_api import api, keypair  # noqa: F401 (fixture)


def test_disabled_buttons_look_disabled_and_do_not_react_to_hover(api):
    page = api.get("/").text
    rule = re.search(r"\.btn:disabled\s*\{([^}]*)\}", page)
    assert rule, "thiếu kiểu cho .btn:disabled"
    assert re.search(r"(^|[\s;])opacity\s*:\s*0?\.[0-6]\d*\s*[;}]?", rule.group(1)) and "cursor: not-allowed" in rule.group(1)   # mờ rõ rệt
    assert ".btn:disabled:hover" in page and ".btn.primary:disabled:hover" in page          # rê chuột không làm nút khóa sáng lên
    assert re.search(r'<button class="btn" id="face-verify" disabled>', page)               # khóa cho tới khi bật camera
