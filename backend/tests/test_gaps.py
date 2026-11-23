"""Các yêu cầu của đề bổ sung sau khi rà soát (docs/COVERAGE.md): gửi hàng offline, tủ ngủ sâu, BUFFER_CRITICAL,
cổng SMS có thử lại, Redis, thu hồi tủ, đơn của tôi, OTA, WebSocket, HSTS, giới hạn tốc độ."""
import http.server
import json
import re
import threading
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

from app import otp as otp_lib
from app import state
from app.config import settings
from app.db import query
from app.hub import hub
from app.kv import MemoryKV, RedisKV
from app.proto import locker_pb2 as pb
from app.services import alerts, lockers, parcels, rules, sms
from tests.fake_locker import FakeLocker
from tests.test_api import PASSWORD, api, headers, keypair, login, make_user  # noqa: F401 (fixture)
from tests.test_flow import PARCEL, count, sms_code

CP = {"confirm_password": PASSWORD}                  # ADMIN nhập lại mật khẩu cho thao tác nguy hiểm

LOCKER = "LOCKER-HCM-001"


def alert_types():
    return [r["type"] for r in query("SELECT type FROM alerts ORDER BY id")]


# ===================================================================== gửi hàng khi mất mạng (Câu 1.2)

def test_cloud_pushes_waiting_parcels_when_the_locker_comes_online(cloud, bus):
    parcels.create_parcel(PARCEL, "0901234567", "M", LOCKER)
    parcels.create_parcel("999", "0901234567", "S", None)           # chưa gán tủ: không đẩy xuống tủ nào
    locker = FakeLocker(bus)
    locker.attributes(online=True, fw="2.1.0")
    assert len(locker.parcels) == 1
    entry = next(iter(locker.parcels.values()))
    assert entry["size"] == "M" and otp_lib.hash_parcel(PARCEL, entry["salt"]) in locker.parcels
    assert PARCEL not in json.dumps(locker.parcels)                  # mã đơn thô không đi xuống tủ
    assert locker.occupied == set()


def test_deposit_while_the_locker_is_offline_then_everything_syncs(cloud, bus):
    parcels.create_parcel(PARCEL, "0901234567", "M", LOCKER)
    locker = FakeLocker(bus)
    locker.attributes(online=True)                                   # tủ nhận danh sách đơn

    locker.go_offline()                                              # rút mạng
    comp = locker.deposit_offline(PARCEL)
    assert comp == 4                                                 # ngăn M nhỏ nhất còn trống
    assert query("SELECT status FROM parcels")[0]["status"] == "CREATED"      # cloud chưa biết gì
    assert len(locker.ring) == 3

    locker.go_online()                                               # có mạng lại: đồng bộ
    assert locker.ring == []
    row = query("SELECT status, comp_no, locker_id FROM parcels")[0]
    assert (row["status"], row["comp_no"], row["locker_id"]) == ("DEPOSITED", 4, LOCKER)
    assert query("SELECT status FROM compartments WHERE comp_no = 4")[0]["status"] == "OCCUPIED"
    assert count("sms_outbox") == 1                                  # OTP sinh SAU khi đồng bộ, đúng một lần
    assert locker.otps[4][0] == otp_lib.hash_otp(sms_code(), locker.otps[4][1], 4)
    assert locker.parcels == {}                                      # đơn bị thu hồi khỏi danh sách offline
    assert alert_types() == []
    assert locker.check_otp_offline(4, sms_code())                   # người nhận lấy được kể cả khi tủ offline lại


def test_offline_deposit_into_a_compartment_the_cloud_thinks_is_taken_raises_a_conflict(cloud, bus):
    parcels.create_parcel("111", "0900000001", "M", LOCKER)
    parcels.create_parcel("222", "0900000002", "M", LOCKER)
    locker = FakeLocker(bus)
    locker.attributes(online=True)
    locker.go_offline()
    comp = locker.deposit_offline("111")
    locker.occupied.discard(comp)                                    # tủ "quên" ngăn 4 đã có hàng (bộ nhớ hỏng...)
    query("UPDATE compartments SET status = 'OCCUPIED' WHERE comp_no = %s", (comp,))   # nhưng cloud biết
    locker.go_online()
    assert query("SELECT status FROM parcels WHERE parcel_id = '111'")[0]["status"] == "CREATED"
    assert alert_types() == ["DEPOSIT_CONFLICT"]
    assert count("sms_outbox") == 0                                  # không sinh OTP cho đơn không có chỗ chắc chắn


def test_offline_deposit_takes_the_slot_reserved_by_another_parcel(cloud, bus):
    """Cloud đã giữ ngăn 4 cho đơn B (tủ online hỏi trước); tủ offline lại bỏ đơn A vào ngăn 4: A thắng, B được cấp lại."""
    parcels.create_parcel("111", "0900000001", "M", LOCKER)          # A
    parcels.create_parcel("222", "0900000002", "M", LOCKER)          # B
    locker = FakeLocker(bus)
    locker.attributes(online=True)
    assert parcels.request_deposit(LOCKER, "222") == 4               # B giữ ngăn 4
    locker.go_offline()
    assert locker.deposit_offline("111") == 4
    locker.go_online()
    assert query("SELECT comp_no, status FROM parcels WHERE parcel_id = '111'")[0] == {"comp_no": 4, "status": "DEPOSITED"}
    assert parcels.request_deposit(LOCKER, "222") == 5               # B được cấp ngăn khác
    assert alert_types() == []


def test_deposit_event_replayed_after_a_lost_ack_does_not_create_a_second_otp(cloud, bus):
    parcels.create_parcel(PARCEL, "0901234567", "M", LOCKER)
    locker = FakeLocker(bus)
    locker.attributes(online=True)
    locker.go_offline()
    locker.deposit_offline(PARCEL)
    locker.drop_next_ack = True
    locker.go_online()                                               # ack bị mất: lô gửi lại
    locker.awaiting_ack = False
    locker.flush()
    assert count("sms_outbox") == 1 and count("locker_events") == 3


# ===================================================================== tủ ngủ sâu, BUFFER_CRITICAL, thu hồi

def test_a_sleeping_locker_is_not_an_incident_but_a_dropped_one_is(cloud, bus):
    locker = FakeLocker(bus)
    locker.attributes(online=True)
    locker.attributes(online=False, sleep=True)                      # tủ báo "đi ngủ" rồi mới tắt Wi-Fi
    row = query("SELECT online, sleeping FROM lockers")[0]
    assert (row["online"], row["sleeping"]) == (False, True)
    assert alert_types() == []

    locker.attributes(online=True, sleep=False)                      # thức dậy
    assert query("SELECT online, sleeping FROM lockers")[0] == {"online": True, "sleeping": False}

    locker.attributes(online=False)                                  # LWT: rớt đột ngột
    assert alert_types() == ["DEVICE_OFFLINE"]


def test_a_locker_that_never_wakes_up_is_reported_offline(cloud, bus, kv):
    locker = FakeLocker(bus)
    locker.attributes(online=True)
    locker.attributes(online=False, sleep=True)
    later = datetime.now(timezone.utc) + timedelta(seconds=settings.offline_after_heartbeats * settings.heartbeat_sec + 60)
    assert rules.sweep(kv, later)["DEVICE_OFFLINE"] == 1
    assert query("SELECT sleeping FROM lockers")[0]["sleeping"] is False


def test_buffer_critical_from_the_locker_becomes_one_alert(cloud, bus):
    locker = FakeLocker(bus)
    locker.attributes(online=True, buffer_critical=False)
    assert alert_types() == []
    locker.attributes(online=True, buffer_critical=True)
    locker.attributes(online=True, buffer_critical=True)             # lặp lại: không báo thêm
    assert alert_types() == ["BUFFER_CRITICAL"]
    assert query("SELECT buffer_critical FROM lockers")[0]["buffer_critical"] is True
    locker.attributes(online=True, buffer_critical=False)
    assert query("SELECT buffer_critical FROM lockers")[0]["buffer_critical"] is False


def test_a_revoked_locker_is_ignored_entirely(cloud, bus):
    locker = FakeLocker(bus)
    locker.event(pb.HEARTBEAT)
    assert count("locker_events") == 1
    assert lockers.revoke(LOCKER) is True
    assert lockers.revoke(LOCKER) is False
    before = len(bus.published)
    locker.awaiting_ack = False
    locker.event(pb.TAMPER, 3)                                       # tủ bị đánh cắp vẫn gửi
    assert count("locker_events") == 1 and alert_types() == []
    assert not [t for t, _ in bus.published[before:] if "rpc/request" in t]     # không ack, không lệnh nào
    locker.attributes(online=True)
    assert query("SELECT online FROM lockers")[0]["online"] is False


# ===================================================================== cổng SMS/Zalo

def soon():
    """Giờ "hiện tại" của bộ gửi tin: đệm 2 giây vì đồng hồ Windows thô (~15 ms) có thể chạy chậm hơn now() của
    PostgreSQL, làm tin vừa tạo bị coi là chưa đến hạn."""
    return datetime.now(timezone.utc) + timedelta(seconds=2)


class Gateway:
    """Cổng SMS giả trên localhost: trả lần lượt các mã trạng thái đã định, ghi lại những gì nhận được."""

    def __init__(self, codes):
        self.codes, self.requests = list(codes), []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.headers.get("Authorization"), body))
                self.send_response(outer.codes.pop(0) if outer.codes else 200)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/sms"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def gateway(monkeypatch):
    made = []

    def make(codes):
        gw = Gateway(codes)
        made.append(gw)
        monkeypatch.setattr(settings, "sms_gateway_url", gw.url)
        monkeypatch.setattr(settings, "sms_gateway_token", "secret-token")
        return gw

    yield make
    for gw in made:
        gw.close()


def queue_sms(cloud, bus):
    locker = FakeLocker(bus)
    parcels.create_parcel(PARCEL, "0901234567", "M")
    comp = locker.ask("deposit_request", parcel_id=PARCEL)["comp"]
    locker.event(pb.DEPOSIT, comp, PARCEL)
    return sms_code()


def test_sms_without_a_gateway_stays_visible_in_the_outbox(cloud, bus, kv):
    code = queue_sms(cloud, bus)
    assert sms.process_outbox(kv, soon()) == {"sent": 0, "retry": 0, "failed": 0}
    row = query("SELECT status, body FROM sms_outbox")[0]
    assert row["status"] == "QUEUED" and code in row["body"]


def test_sms_is_sent_through_the_gateway_with_auth_and_then_the_code_is_masked(cloud, bus, kv, gateway):
    gw = gateway([200])
    code = queue_sms(cloud, bus)
    assert sms.process_outbox(kv, soon())["sent"] == 1
    auth, body = gw.requests[0]
    assert auth == "Bearer secret-token"
    assert body["to"] == "0901234567" and code in body["text"]       # cổng nhận đủ mã để gửi đi
    row = query("SELECT status, body, attempts, sent_at FROM sms_outbox")[0]
    assert row["status"] == "SENT" and row["attempts"] == 1 and row["sent_at"] is not None
    assert code not in row["body"] and "******" in row["body"]       # nhưng cloud không giữ lại mã thô


def test_sms_failures_are_retried_with_backoff_then_succeed(cloud, bus, kv, gateway):
    gw = gateway([500, 503, 200])
    queue_sms(cloud, bus)
    now = soon()
    assert sms.process_outbox(kv, now) == {"sent": 0, "retry": 1, "failed": 0}
    assert sms.process_outbox(kv, now + timedelta(seconds=5)) == {"sent": 0, "retry": 0, "failed": 0}   # chưa đến hạn
    assert len(gw.requests) == 1
    assert sms.process_outbox(kv, now + timedelta(seconds=31)) == {"sent": 0, "retry": 1, "failed": 0}
    assert sms.process_outbox(kv, now + timedelta(seconds=31 + 59)) == {"sent": 0, "retry": 0, "failed": 0}  # lần 2 cách 60 s
    assert sms.process_outbox(kv, now + timedelta(seconds=31 + 61))["sent"] == 1
    assert query("SELECT status, attempts FROM sms_outbox")[0] == {"status": "SENT", "attempts": 3}
    assert alert_types() == []


def test_sms_that_keeps_failing_is_marked_failed_and_alerts_the_operator(cloud, bus, kv, gateway, monkeypatch):
    gateway([500] * 10)
    monkeypatch.setattr(settings, "sms_max_attempts", 3)
    queue_sms(cloud, bus)
    now = soon()
    for step in range(5):
        sms.process_outbox(kv, now + timedelta(hours=step))
    row = query("SELECT status, attempts, last_error FROM sms_outbox")[0]
    assert (row["status"], row["attempts"], row["last_error"]) == ("FAILED", 3, "HTTP 500")
    assert alert_types() == ["SMS_FAILED"]


def test_sms_gateway_down_is_retried_not_lost(cloud, bus, kv, monkeypatch):
    monkeypatch.setattr(settings, "sms_gateway_url", "http://127.0.0.1:1/sms")      # không ai nghe
    queue_sms(cloud, bus)
    assert sms.process_outbox(kv, soon())["retry"] == 1
    assert query("SELECT status FROM sms_outbox")[0]["status"] == "RETRY"


# ===================================================================== Redis: trạng thái tủ và OTP băm có TTL

@pytest.fixture
def redis_kv():
    return RedisKV("redis://unused", client=fakeredis.FakeRedis(decode_responses=True))


def test_redis_kv_basics_and_hash(redis_kv):
    redis_kv.set("a", "1", ttl=30)
    assert redis_kv.get("a") == "1" and 0 < redis_kv.ttl("a") <= 30
    assert redis_kv.incr("n", 10) == 1 and redis_kv.incr("n", 10) == 2
    redis_kv.hset("locker:X:status", {"fill_rate": 0.25, "online": 1})
    assert redis_kv.hgetall("locker:X:status") == {"fill_rate": "0.25", "online": "1"}
    redis_kv.delete("a")
    assert not redis_kv.exists("a")


@pytest.mark.parametrize("store", ["memory", "redis"])
def test_locker_status_hash_follows_the_parcel_lifecycle(store, cloud, bus, redis_kv, kv):
    use = redis_kv if store == "redis" else kv
    cloud.kv = use
    locker = FakeLocker(bus)
    locker.attributes(online=True)
    assert use.hgetall(lockers.status_key(LOCKER))["occupied"] == "0"
    parcels.create_parcel(PARCEL, "0901234567", "M")
    comp = locker.ask("deposit_request", parcel_id=PARCEL)["comp"]
    locker.event(pb.DEPOSIT, comp, PARCEL)
    status = use.hgetall(lockers.status_key(LOCKER))
    assert (status["occupied"], status["compartments"], status["fill_rate"], status["online"]) == ("1", "8", "0.125", "1")
    code = sms_code()
    assert locker.ask("pickup_request", comp=comp, otp=code) == {"ok": True}
    locker.event(pb.PICKUP, comp)
    assert use.hgetall(lockers.status_key(LOCKER))["occupied"] == "0"


def test_otp_hash_is_cached_in_redis_with_a_ttl_and_dropped_after_pickup(cloud, bus, redis_kv):
    cloud.kv = redis_kv
    locker = FakeLocker(bus)
    parcels.create_parcel(PARCEL, "0901234567", "M")
    comp = locker.ask("deposit_request", parcel_id=PARCEL)["comp"]
    locker.event(pb.DEPOSIT, comp, PARCEL)
    key = parcels.OTP_KEY.format(locker=LOCKER, comp=comp)
    cached = json.loads(redis_kv.get(key))
    assert set(cached) == {"hash", "salt", "exp", "rf"}                    # chỉ bản băm, không có OTP thô
    assert 0 < redis_kv.ttl(key) <= settings.otp_ttl_hours * 3600
    code = sms_code()
    assert code not in redis_kv.get(key)
    assert parcels.verify_pickup(redis_kv, LOCKER, comp, code).ok
    locker.event(pb.PICKUP, comp)
    assert redis_kv.get(key) is None


def test_verify_pickup_refills_redis_from_postgres_after_a_cache_loss(cloud, bus, redis_kv):
    cloud.kv = redis_kv
    locker = FakeLocker(bus)
    parcels.create_parcel(PARCEL, "0901234567", "M")
    comp = locker.ask("deposit_request", parcel_id=PARCEL)["comp"]
    locker.event(pb.DEPOSIT, comp, PARCEL)
    code = sms_code()
    key = parcels.OTP_KEY.format(locker=LOCKER, comp=comp)
    redis_kv.delete(key)                                             # Redis khởi động lại, mất cache
    assert parcels.verify_pickup(redis_kv, LOCKER, comp, "000000" if code != "000000" else "111111").error == "WRONG_OTP"
    assert redis_kv.get(key) is not None                             # nạp lại từ PostgreSQL
    assert parcels.verify_pickup(redis_kv, LOCKER, comp, code).ok


def test_an_expired_otp_is_refused_even_when_cached(cloud, bus, kv):
    locker = FakeLocker(bus)
    parcels.create_parcel(PARCEL, "0901234567", "M")
    comp = locker.ask("deposit_request", parcel_id=PARCEL)["comp"]
    locker.event(pb.DEPOSIT, comp, PARCEL)
    code = sms_code()
    key = parcels.OTP_KEY.format(locker=LOCKER, comp=comp)
    rec = json.loads(kv.get(key))
    rec["exp"] = int(datetime.now(timezone.utc).timestamp()) - 5
    kv.set(key, json.dumps(rec))
    assert parcels.verify_pickup(kv, LOCKER, comp, code).error == "EXPIRED"


# ===================================================================== giữ dữ liệu 180 ngày (dự phòng cho Timescale)

def test_old_events_are_purged_after_the_retention_period(cloud, bus, kv):
    locker = FakeLocker(bus)
    now = datetime.now(timezone.utc)
    old = int((now - timedelta(days=settings.retention_days + 1)).timestamp())
    locker.event(pb.HEARTBEAT, ts=old)
    locker.event(pb.HEARTBEAT, ts=int(now.timestamp()))
    assert count("locker_events") == 2
    assert rules.purge_old_events(kv, now) == 1
    assert count("locker_events") == 1
    assert rules.purge_old_events(kv, now) == 0                      # chỉ chạy mỗi 6 giờ


def test_timescale_migration_is_split_into_statements_and_skipped_without_the_extension():
    from app import db

    text = (db.MIGRATIONS / "003_timescale.timescale.sql").read_text(encoding="utf-8")
    statements = db._statements(text)
    assert statements[0].startswith("CREATE EXTENSION") and len(statements) >= 6
    assert all(not s.startswith("--") for s in statements)
    assert any("add_retention_policy('locker_events', INTERVAL '180 days')" in s for s in statements)
    assert any("timescaledb.continuous" in s for s in statements)
    # máy phát triển không có TimescaleDB: migrate() vẫn thành công và không ghi nhận file này
    assert "003_timescale.timescale.sql" not in db.migrate()


# ===================================================================== API: đơn của tôi, OTA, thu hồi, WebSocket, HSTS, rate limit

def test_my_parcels_shows_each_user_only_their_own(api, cloud, bus):
    shipper, customer = headers(api, "SHIPPER", "0900000003"), headers(api, "CUSTOMER", "0900000004")
    other_shipper = headers(api, "SHIPPER", "0900000013")
    mine = api.post("/parcels", json={"parcel_id": "111", "recipient_phone": "0900000004", "locker_id": LOCKER}, headers=shipper)
    assert mine.status_code == 201
    api.post("/parcels", json={"parcel_id": "222", "recipient_phone": "0911111111", "locker_id": LOCKER}, headers=other_shipper)
    api.post("/parcels", json={"parcel_id": "333", "recipient_phone": "0900000004"}, headers=other_shipper)

    assert [p["parcel_id"] for p in api.get("/my/parcels", headers=shipper).json()] == ["111"]
    assert [p["parcel_id"] for p in api.get("/my/parcels", headers=other_shipper).json()] == ["333", "222"]
    got = api.get("/my/parcels", headers=customer).json()
    assert sorted(p["parcel_id"] for p in got) == ["111", "333"]
    assert got[0]["otp_expires_at"] is None and "otp_hash" not in got[0]
    assert api.get("/my/parcels").status_code == 401
    operator = headers(api, "OPERATOR", "0900000002")
    assert api.get("/my/parcels", headers=operator).status_code == 403


def test_creating_a_parcel_for_a_locker_announces_it_to_that_locker(api, cloud, bus):
    locker = FakeLocker(bus)
    shipper = headers(api, "SHIPPER", "0900000003")
    api.post("/parcels", json={"parcel_id": PARCEL, "recipient_phone": "0900000004", "locker_id": LOCKER, "size_code": "L"},
             headers=shipper)
    assert len(locker.parcels) == 1 and next(iter(locker.parcels.values()))["size"] == "L"


def test_ota_update_is_admin_only_https_only_and_validated(api, cloud, bus):
    locker = FakeLocker(bus)
    admin, operator = headers(api, "ADMIN", "0900000001"), headers(api, "OPERATOR", "0900000002")
    body = {"url": "https://updates.example.com/fw-2.2.0.bin", "sha256": "A" * 64, "confirm_password": PASSWORD}
    assert api.post(f"/lockers/{LOCKER}/ota", json=body, headers=operator).status_code == 403
    assert api.post(f"/lockers/{LOCKER}/ota", json={**body, "url": "http://plain.example/fw.bin"}, headers=admin).status_code == 422
    assert api.post(f"/lockers/{LOCKER}/ota", json={**body, "sha256": "xyz"}, headers=admin).status_code == 422
    assert api.post("/lockers/NOPE/ota", json=body, headers=admin).status_code == 404
    assert api.post(f"/lockers/{LOCKER}/ota", json=body, headers=admin).status_code == 200
    assert locker.ota == {"url": body["url"], "sha256": "a" * 64}
    assert query("SELECT action FROM audit_log WHERE action = 'ota_update'")


def test_revoking_a_locker_over_the_api(api, cloud, bus):
    locker = FakeLocker(bus)
    admin, operator = headers(api, "ADMIN", "0900000001"), headers(api, "OPERATOR", "0900000002")
    assert api.post(f"/lockers/{LOCKER}/revoke", json=CP, headers=operator).status_code == 403
    res = api.post(f"/lockers/{LOCKER}/revoke", json=CP, headers=admin)
    assert res.status_code == 200 and LOCKER in res.json()["next_step"]
    assert api.post(f"/lockers/{LOCKER}/revoke", json=CP, headers=admin).status_code == 404
    locker.event(pb.HEARTBEAT)
    assert count("locker_events") == 0


def test_locker_list_exposes_map_and_sleep_fields_and_status_comes_from_redis(api, cloud, bus, kv):
    lockers.register_locker("LOCKER-MAP", "Co toa do", lat=10.77, lon=106.70)
    op = headers(api, "OPERATOR", "0900000002")
    rows = {r["locker_id"]: r for r in api.get("/lockers", headers=op).json()}
    assert (rows["LOCKER-MAP"]["lat"], rows["LOCKER-MAP"]["lon"]) == (10.77, 106.70)
    assert rows[LOCKER]["lat"] is None and rows[LOCKER]["sleeping"] is False
    status = api.get(f"/lockers/{LOCKER}/status", headers=op).json()
    assert status["compartments"] == 8 and status["occupied"] == 0 and status["fill_rate"] == 0.0
    assert api.get(f"/lockers/{LOCKER}/status", headers=op).json() == status      # lần sau đọc từ Redis, cùng kiểu
    assert state.kv.hgetall(lockers.status_key(LOCKER))              # thật sự nằm trong KV
    assert api.get("/lockers/NOPE/status", headers=op).status_code == 404


def test_compartment_grid_reports_dwell_time_and_hourly_has_rssi(api, cloud, bus):
    locker = FakeLocker(bus)
    parcels.create_parcel(PARCEL, "0901234567", "M")
    comp = locker.ask("deposit_request", parcel_id=PARCEL)["comp"]
    locker.event(pb.DEPOSIT, comp, PARCEL, ts=int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()))
    locker.event(pb.HEARTBEAT)
    op = headers(api, "OPERATOR", "0900000002")
    grid = {c["comp_no"]: c for c in api.get(f"/lockers/{LOCKER}/compartments", headers=op).json()}
    assert 7100 < grid[comp]["dwell_seconds"] < 7400 and grid[1]["dwell_seconds"] is None
    hourly = api.get(f"/stats/hourly?hours=6&locker_id={LOCKER}", headers=op).json()
    assert hourly and hourly[-1]["avg_rssi_dbm"] == -67 and hourly[-1]["avg_battery_mv"] == 12100


def test_websocket_requires_a_valid_token_and_pushes_changes(api, cloud, bus):
    state.listeners.append(hub.publish)
    try:
        op = headers(api, "OPERATOR", "0900000002")["Authorization"].split()[1]
        customer = headers(api, "CUSTOMER", "0900000004")["Authorization"].split()[1]
        for bad in ({"token": "garbage"}, {"token": customer}, {"nothing": 1}):
            with api.websocket_connect("/ws") as ws:
                ws.send_json(bad)
                with pytest.raises(Exception):                         # server đóng 4401
                    ws.receive_json()
        with api.websocket_connect("/ws") as ws:
            ws.send_json({"token": op})
            assert ws.receive_json() == {"type": "hello"}
            locker = FakeLocker(bus)
            locker.event(pb.TAMPER, 3)
            seen = []
            for _ in range(3):
                seen.append(ws.receive_json())
                if any(m["type"] == "alert" for m in seen) and any(m["type"] == "events" for m in seen):
                    break
            assert {"alert", "events"} <= {m["type"] for m in seen}
            assert hub.count() == 1
        assert hub.count() == 0                                         # ngắt kết nối thì được dọn
    finally:
        state.listeners.remove(hub.publish)


def test_hsts_only_on_https_responses(api, cloud, monkeypatch):
    assert "strict-transport-security" not in api.get("/health").headers
    secure = api.get("/health", headers={"x-forwarded-proto": "https"})
    assert secure.headers["strict-transport-security"].startswith("max-age=31536000")
    monkeypatch.setattr(settings, "hsts", False)
    assert "strict-transport-security" not in api.get("/health", headers={"x-forwarded-proto": "https"}).headers


def test_api_rate_limit_per_ip(api, cloud, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_min", 5)
    codes = [api.get("/health").status_code for _ in range(8)]
    assert codes[:5] == [200] * 5 and set(codes[5:]) == {429}
    assert api.get("/health").headers.get("retry-after") == "30"


def test_jwt_keys_can_come_from_the_environment(monkeypatch, keypair):
    from app import security

    security._keys.cache_clear()
    monkeypatch.setenv("JWT_PRIVATE_KEY", "PRIV-PEM-FROM-ENV")
    monkeypatch.setenv("JWT_PUBLIC_KEY", "PUB-PEM-FROM-ENV")
    try:
        assert security._keys() == ("PRIV-PEM-FROM-ENV", "PUB-PEM-FROM-ENV")
    finally:
        security._keys.cache_clear()


def test_certificate_fingerprint_is_recorded_per_locker_and_cannot_be_shared(cloud):
    import psycopg2.errors

    lockers.register_locker("LOCKER-HCM-002", "Tu 2")
    fp = "ab" * 32
    lockers.set_fingerprint(LOCKER, fp.upper())
    assert query("SELECT cert_fingerprint FROM lockers WHERE locker_id = %s", (LOCKER,))[0]["cert_fingerprint"] == fp
    with pytest.raises(psycopg2.errors.UniqueViolation):
        lockers.set_fingerprint("LOCKER-HCM-002", fp)                 # một chứng chỉ không thể thuộc hai tủ
    for bad in ("", "xyz", "ab" * 31):
        with pytest.raises(ValueError):
            lockers.set_fingerprint(LOCKER, bad)


def test_a_newly_registered_locker_has_eight_compartments():
    lockers.register_locker("LOCKER-NEW", "Moi")
    rows = query("SELECT comp_no, size_code FROM compartments WHERE locker_id = 'LOCKER-NEW' ORDER BY comp_no")
    assert [r["size_code"] for r in rows] == list("SSSMMMLL") and [r["comp_no"] for r in rows] == list(range(1, 9))
