"""Gán tọa độ cho tủ đã đăng ký (bản đồ dashboard): chạy lại register-locker với --lat --lon phải cập nhật, không phải bỏ qua."""
from app.db import query
from app.services import lockers


def row(locker_id="LOCKER-X"):
    return query("SELECT name, address, geo[0] AS lon, geo[1] AS lat FROM lockers WHERE locker_id = %s", (locker_id,))[0]


def test_registering_again_with_coordinates_updates_an_existing_locker():
    lockers.register_locker("LOCKER-X", "Tu X")
    assert row()["lat"] is None and row()["address"] is None                    # đăng ký ban đầu không có tọa độ
    lockers.register_locker("LOCKER-X", "Ten khac", address="Quan 1, TP.HCM", lat=10.7769, lon=106.7009)
    r = row()
    assert (r["lat"], r["lon"], r["address"]) == (10.7769, 106.7009, "Quan 1, TP.HCM")
    assert r["name"] == "Tu X"                                                  # tên không bị đổi


def test_registering_again_without_coordinates_keeps_the_old_ones_and_the_compartments():
    lockers.register_locker("LOCKER-X", "Tu X", address="Quan 7", lat=10.73, lon=106.72)
    lockers.register_locker("LOCKER-X", "Tu X")
    r = row()
    assert (r["lat"], r["lon"], r["address"]) == (10.73, 106.72, "Quan 7")
    assert query("SELECT count(*) AS n FROM compartments WHERE locker_id = 'LOCKER-X'")[0]["n"] == 8


def test_only_one_of_lat_lon_does_not_wipe_the_coordinates():
    lockers.register_locker("LOCKER-X", "Tu X", lat=10.73, lon=106.72)
    lockers.register_locker("LOCKER-X", "Tu X", lat=11.0)                       # thiếu lon: bỏ qua, giữ tọa độ cũ
    assert (row()["lat"], row()["lon"]) == (10.73, 106.72)
