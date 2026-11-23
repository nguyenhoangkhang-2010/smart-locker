"""ADMIN thêm tủ mới và sửa tên/địa chỉ/vị trí ngay trên dashboard (thay cho dòng lệnh register-locker):
phân quyền, nhập lại mật khẩu, kiểm tra dữ liệu, không ghi đè tủ có sẵn, có vết audit, tủ mới nhận được dữ liệu từ firmware."""
import pytest

from app.db import query
from app.services import lockers
from tests.test_api import PASSWORD, api, headers, keypair, make_user  # noqa: F401 (fixture)

CP = {"confirm_password": PASSWORD}
NEW = {"locker_id": "LOCKER-HCM-003", "name": "Tu HCM 003", "address": "Quan 3, TP.HCM", "lat": 10.784, "lon": 106.683, **CP}


@pytest.fixture
def admin(api):
    return headers(api, "ADMIN", "0900000001")


def by_id(api, admin):
    return {l["locker_id"]: l for l in api.get("/lockers", headers=admin).json()}


def audit(action):
    return query("SELECT user_id, locker_id, detail FROM audit_log WHERE action = %s ORDER BY id", (action,))


# ===================================================================== thêm tủ
def test_an_admin_adds_a_locker_with_its_compartments_and_location(api, admin):
    res = api.post("/lockers", headers=admin, json=NEW)
    assert res.status_code == 201 and res.json() == {"locker_id": "LOCKER-HCM-003", "name": "Tu HCM 003", "compartments": 8}
    row = by_id(api, admin)["LOCKER-HCM-003"]
    assert (row["name"], row["address"], row["lat"], row["lon"], row["compartments"]) == ("Tu HCM 003", "Quan 3, TP.HCM", 10.784, 106.683, 8)
    sizes = "".join(r["size_code"] for r in query("SELECT size_code FROM compartments WHERE locker_id = %s ORDER BY comp_no", ("LOCKER-HCM-003",)))
    assert sizes == "SSSMMMLL"
    log = audit("create_locker")
    assert len(log) == 1 and log[0]["locker_id"] == "LOCKER-HCM-003"


def test_the_compartment_layout_can_be_chosen_and_must_be_valid(api, admin):
    assert api.post("/lockers", headers=admin, json={**NEW, "sizes": "SML"}).json()["compartments"] == 3
    for bad in ("", "SMX", "sml", "S" * 17, "S M"):
        res = api.post("/lockers", headers=admin, json={**NEW, "locker_id": "LOCKER-BAD-1", "sizes": bad})
        assert res.status_code == 422, bad
    assert query("SELECT 1 FROM lockers WHERE locker_id = 'LOCKER-BAD-1'") == []


def test_a_locker_without_address_or_location_is_allowed(api, admin):
    body = {"locker_id": "LOCKER-MIN", "name": "Tu toi thieu", **CP}
    assert api.post("/lockers", headers=admin, json=body).status_code == 201
    row = by_id(api, admin)["LOCKER-MIN"]
    assert row["address"] is None and row["lat"] is None and row["lon"] is None


def test_an_existing_locker_is_never_overwritten(api, admin):
    lockers.register_locker("LOCKER-OLD", "Ten cu", address="Dia chi cu", lat=1.0, lon=2.0)
    res = api.post("/lockers", headers=admin, json={**NEW, "locker_id": "LOCKER-OLD", "name": "Ten moi"})
    assert res.status_code == 409
    row = by_id(api, admin)["LOCKER-OLD"]
    assert (row["name"], row["address"], row["lat"], row["lon"]) == ("Ten cu", "Dia chi cu", 1.0, 2.0)
    assert audit("create_locker") == []


@pytest.mark.parametrize("locker_id", ["ab", "LOCKER 3", "LOCKER/3", "LOCKER+", "LOCKER#", "-LOCKER", "", "x" * 65, "TỦ-1"])
def test_the_id_must_be_safe_to_use_in_an_mqtt_topic(api, admin, locker_id):
    assert api.post("/lockers", headers=admin, json={**NEW, "locker_id": locker_id}).status_code == 422
    assert query("SELECT count(*) AS n FROM lockers WHERE locker_id = %s", (locker_id,))[0]["n"] == 0


@pytest.mark.parametrize("patch", [{"name": ""}, {"name": "   "}, {"name": "x" * 81}, {"address": "x" * 201},
                                   {"lat": 91, "lon": 0}, {"lat": 0, "lon": 181}, {"lat": -91, "lon": 0},
                                   {"lat": 10.0, "lon": None}, {"lat": None, "lon": 106.0}])
def test_invalid_fields_are_rejected_and_nothing_is_created(api, admin, patch):
    assert api.post("/lockers", headers=admin, json={**NEW, **patch}).status_code == 422
    assert "LOCKER-HCM-003" not in by_id(api, admin)


def test_leading_and_trailing_spaces_are_trimmed(api, admin):
    api.post("/lockers", headers=admin, json={**NEW, "locker_id": "  LOCKER-HCM-003  ", "name": "  Tu 3  ", "address": "  Q3  "})
    row = by_id(api, admin)["LOCKER-HCM-003"]
    assert (row["name"], row["address"]) == ("Tu 3", "Q3")


# ===================================================================== sửa tủ có sẵn
def test_the_address_and_location_of_an_existing_locker_can_be_set(api, admin):
    lockers.register_locker("LOCKER-OLD", "Ten cu")
    res = api.patch("/lockers/LOCKER-OLD", headers=admin, json={"address": "Quan 1", "lat": 10.7769, "lon": 106.7009, **CP})
    assert res.status_code == 200 and res.json() == {"updated": ["address", "location"]}
    row = by_id(api, admin)["LOCKER-OLD"]
    assert (row["name"], row["address"], row["lat"], row["lon"]) == ("Ten cu", "Quan 1", 10.7769, 106.7009)
    assert audit("edit_locker")[0]["detail"] == "address, location"


def test_fields_not_sent_are_left_alone_and_null_clears(api, admin):
    lockers.register_locker("LOCKER-OLD", "Ten cu", address="Dia chi cu", lat=10.0, lon=106.0)
    api.patch("/lockers/LOCKER-OLD", headers=admin, json={"name": "Ten moi", **CP})
    row = by_id(api, admin)["LOCKER-OLD"]
    assert (row["name"], row["address"], row["lat"], row["lon"]) == ("Ten moi", "Dia chi cu", 10.0, 106.0)      # địa chỉ và vị trí còn nguyên
    api.patch("/lockers/LOCKER-OLD", headers=admin, json={"address": None, "lat": None, "lon": None, **CP})
    row = by_id(api, admin)["LOCKER-OLD"]
    assert (row["name"], row["address"], row["lat"], row["lon"]) == ("Ten moi", None, None, None)             # gỡ khỏi bản đồ


def test_editing_never_touches_the_compartments_or_the_id(api, admin):
    lockers.register_locker("LOCKER-OLD", "Ten cu")
    before = query("SELECT comp_no, size_code, status FROM compartments WHERE locker_id = 'LOCKER-OLD' ORDER BY comp_no")
    api.patch("/lockers/LOCKER-OLD", headers=admin, json={"name": "Ten moi", "locker_id": "LOCKER-KHAC", "sizes": "LLLLLLLL", **CP})
    assert query("SELECT comp_no, size_code, status FROM compartments WHERE locker_id = 'LOCKER-OLD' ORDER BY comp_no") == before
    assert "LOCKER-KHAC" not in by_id(api, admin)


@pytest.mark.parametrize("patch", [{}, {"name": ""}, {"name": "x" * 81}, {"lat": 10.0}, {"lon": 106.0}, {"lat": 95.0, "lon": 0.0},
                                   {"address": "x" * 201}, {"lat": None}, {"lon": None}])    # một nửa tọa độ, kể cả null: không được vô tình gỡ khỏi bản đồ
def test_bad_edits_are_rejected_without_changing_anything(api, admin, patch):
    lockers.register_locker("LOCKER-OLD", "Ten cu", address="Dia chi cu", lat=10.0, lon=106.0)
    assert api.patch("/lockers/LOCKER-OLD", headers=admin, json={**patch, **CP}).status_code == 422
    row = by_id(api, admin)["LOCKER-OLD"]
    assert (row["name"], row["address"], row["lat"], row["lon"]) == ("Ten cu", "Dia chi cu", 10.0, 106.0)
    assert audit("edit_locker") == []


def test_editing_a_missing_locker_is_a_404(api, admin):
    assert api.patch("/lockers/NOPE-001", headers=admin, json={"name": "x", **CP}).status_code == 404


# ===================================================================== quyền và mật khẩu
@pytest.mark.parametrize("role", ["OPERATOR", "SHIPPER", "CUSTOMER"])
def test_only_an_admin_may_add_or_edit_lockers(api, role):
    lockers.register_locker("LOCKER-OLD", "Ten cu")
    who = headers(api, role, {"OPERATOR": "0900000002", "SHIPPER": "0900000003", "CUSTOMER": "0900000004"}[role])
    assert api.post("/lockers", headers=who, json=NEW).status_code == 403
    assert api.patch("/lockers/LOCKER-OLD", headers=who, json={"name": "Hack", **CP}).status_code == 403
    assert query("SELECT name FROM lockers WHERE locker_id = 'LOCKER-OLD'")[0]["name"] == "Ten cu"
    assert query("SELECT 1 FROM lockers WHERE locker_id = 'LOCKER-HCM-003'") == []


def test_anonymous_callers_are_refused(api):
    assert api.post("/lockers", json=NEW).status_code == 401
    assert api.patch("/lockers/LOCKER-OLD", json={"name": "x", **CP}).status_code == 401


def test_the_admin_must_retype_the_password(api, admin):
    lockers.register_locker("LOCKER-OLD", "Ten cu")
    wrong = {**NEW, "confirm_password": "sai"}
    assert api.post("/lockers", headers=admin, json=wrong).status_code == 403
    assert api.patch("/lockers/LOCKER-OLD", headers=admin, json={"name": "Hack", "confirm_password": "sai"}).status_code == 403
    assert api.post("/lockers", headers=admin, json={k: v for k, v in NEW.items() if k != "confirm_password"}).status_code == 422
    assert query("SELECT 1 FROM lockers WHERE locker_id = 'LOCKER-HCM-003'") == []
    assert query("SELECT name FROM lockers WHERE locker_id = 'LOCKER-OLD'")[0]["name"] == "Ten cu"
    assert audit("create_locker") == [] and audit("edit_locker") == []


# ===================================================================== giao diện
def test_the_dashboard_has_the_add_and_edit_forms_wired_to_the_api(api):
    page = api.get("/").text
    for needle in ('id="btn-add-locker"', 'id="btn-edit-locker"', 'id="locker-dialog"', 'id="ld-pass"',      # nút, hộp thoại, ô nhập lại mật khẩu
                   'api("/lockers", { method: "POST"', 'method: "PATCH"', "confirm_password"):
        assert needle in page, needle
    assert '$("#btn-add-locker").hidden = !can("admin:manage")' in page                                         # chỉ ADMIN thấy nút
    assert 'err.body.detail === "locker not found"' in page and "khởi động lại backend" in page                 # 404 do backend cũ không bị nhầm là "tủ không tồn tại"


# ===================================================================== tủ thêm từ dashboard dùng được thật
def test_a_locker_added_from_the_dashboard_is_accepted_by_the_bridge_and_listed_for_shippers(api, admin):
    api.post("/lockers", headers=admin, json=NEW)
    assert lockers.exists("LOCKER-HCM-003")                       # bridge chỉ nhận tin của tủ đã đăng ký
    shipper = headers(api, "SHIPPER", "0900000003")
    assert "LOCKER-HCM-003" in [l["locker_id"] for l in api.get("/lockers/available", headers=shipper).json()]
