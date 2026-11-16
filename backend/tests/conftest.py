"""Test chạy trên PostgreSQL thật (smart_locker_test), broker giả trong bộ nhớ."""
import psycopg2
import pytest

from app import db
from app.bridge import Bridge
from app.config import settings
from app.kv import MemoryKV
from app.services import lockers
from app.transport import FakeBus

TEST_DB = "smart_locker_test"   # cố định: test không bao giờ chạy trên database khác


class Clock:
    def __init__(self, start=1_800_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture(scope="session", autouse=True)
def database():
    settings.db_name = TEST_DB
    db.reset_pool()
    assert settings.db_name == TEST_DB
    conn = psycopg2.connect(host=settings.db_host, port=settings.db_port, dbname=TEST_DB,
                            user=settings.db_user, password=settings.db_password)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    conn.close()
    db.migrate()
    yield
    db.reset_pool()


@pytest.fixture(autouse=True)
def clean_tables(database):
    db.query(
        "TRUNCATE locker_events, event_seen, alerts, audit_log, sms_outbox, door_state, "
        "parcels, compartments, lockers, users RESTART IDENTITY CASCADE")


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def kv(clock):
    return MemoryKV(clock=clock)


@pytest.fixture
def bus():
    return FakeBus()


@pytest.fixture
def cloud(bus, kv):
    """Backend đã nối vào broker giả, có sẵn tủ LOCKER-HCM-001 với 8 ngăn."""
    lockers.register_locker("LOCKER-HCM-001", "Tu HCM 001")
    bridge = Bridge(bus, kv)
    bridge.start()
    return bridge
