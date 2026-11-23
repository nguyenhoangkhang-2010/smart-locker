"""Bản đồ nền thật (Leaflet + OpenStreetMap): thư viện được đóng gói sẵn trong dự án và phục vụ cục bộ (không phụ thuộc CDN),
nguyên vẹn so với bản phát hành 1.9.4 đã kiểm, và API trả tọa độ để vẽ chấm."""
import hashlib
import re
from pathlib import Path

import pytest

from app.db import query
from app.services import lockers
from tests.test_api import api, headers, keypair  # noqa: F401 (fixture)

VENDOR = Path(__file__).resolve().parent.parent / "app" / "static" / "vendor" / "leaflet"
# SHA-256 của leaflet.js và leaflet.css trong gói npm leaflet@1.9.4 (đã đối chiếu với integrity sha512 của npm registry)
PINNED = {"leaflet.js": "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a",
          "leaflet.css": "a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6"}


def test_the_vendored_files_are_exactly_the_audited_release():
    for name, digest in PINNED.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, f"{name} bị sửa hoặc đổi phiên bản"
    assert (VENDOR / "LICENSE").read_text(encoding="utf-8").startswith("BSD 2-Clause License")     # giữ giấy phép đi kèm
    assert "Leaflet 1.9.4" in (VENDOR / "leaflet.js").read_text(encoding="utf-8")[:200]


def test_the_server_serves_the_library_and_its_images_locally(api):
    js = api.get("/vendor/leaflet/leaflet.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"] and "Leaflet 1.9.4" in js.text[:200]
    css = api.get("/vendor/leaflet/leaflet.css")
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]
    assert api.get("/vendor/leaflet/images/marker-icon.png").status_code == 200
    assert api.get("/vendor/leaflet/nope.js").status_code == 404


@pytest.mark.parametrize("path", ["/vendor/../main.py", "/vendor/%2e%2e/main.py", "/vendor/..%2fmain.py", "/vendor/leaflet/../../main.py",
                                  "/vendor/%2e%2e%2f%2e%2e%2fconfig.py"])
def test_the_static_route_cannot_be_used_to_read_other_files(api, path):
    res = api.get(path)
    assert res.status_code in (400, 404) and "settings" not in res.text and "class Settings" not in res.text


def test_the_dashboard_loads_leaflet_from_the_project_not_from_a_cdn(api):
    page = api.get("/").text
    assert '/vendor/leaflet/leaflet.js' in page and '/vendor/leaflet/leaflet.css' in page
    external = re.findall(r'<script[^>]+src="(https?:)?//[^"]+"|<link[^>]+href="https?://[^"]+"', page)
    assert external == []                                                                           # không nạp mã từ máy chủ ngoài
    assert "tile.openstreetmap.org" in page and "openstreetmap.org/copyright" in page               # nền bản đồ và ghi nguồn bắt buộc của OSM


def test_the_locker_list_gives_coordinates_for_the_map(api):
    lockers.register_locker("LOCKER-GEO", "Tu co toa do", lat=10.7769, lon=106.7009)
    lockers.register_locker("LOCKER-NOGEO", "Tu chua co toa do")
    admin = headers(api, "ADMIN", "0900000001")
    by_id = {l["locker_id"]: l for l in api.get("/lockers", headers=admin).json()}
    assert (by_id["LOCKER-GEO"]["lat"], by_id["LOCKER-GEO"]["lon"]) == (10.7769, 106.7009)
    assert by_id["LOCKER-NOGEO"]["lat"] is None and by_id["LOCKER-NOGEO"]["lon"] is None
