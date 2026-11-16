"""power_budget.py: công thức của đề và việc đối chiếu bảng số liệu trong đề."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("power_budget", ROOT / "tools" / "power_budget.py")
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)


def close(value, expected, tol):
    return abs(value - expected) <= tol * expected


def test_always_on_row_matches_the_assignment_table():
    r = pb.report(pb.Assumptions())["always_on"]
    assert close(r["avg_current_ma_at_12v"], 52.2, 0.01)
    assert close(r["energy_24h_wh"], 15.0, 0.01)
    assert close(r["c_min_ah"], 1.96, 0.01)


def test_only_the_assumptions_written_in_the_pdf_give_about_half_of_the_table():
    """Đề nêu ESP32 80 mA nhưng bảng của đề cần ~154 mA liên tục: con số này tài liệu hóa lệch đó."""
    r = pb.report(pb.Assumptions(radio_idle_ma=0.0))["always_on"]
    assert close(r["avg_current_ma_at_12v"], 28.4, 0.02)
    assert r["energy_24h_wh"] < 0.6 * 15.0


def test_deep_sleep_is_in_the_same_range_as_the_table_but_not_identical():
    r = pb.report(pb.Assumptions())["deep_sleep"]
    assert 1.0 < r["energy_24h_wh"] < 1.4            # đề: 1.09 Wh
    assert pb.report(pb.Assumptions())["sleep_saving_factor"] > 8


def test_c_min_formula_and_battery_arithmetic():
    a = pb.Assumptions()
    assert close(pb.c_min_ah(15.03, a), 1.96, 0.005)               # C = E / (Vbat * DoD * aging)
    assert close(pb.c_min_ah(1.09, a), 0.142, 0.01)
    assert close(pb.usable_wh(2.0, 12.8, a), 16.4, 0.005)          # LiFePO4 12,8 V 2 Ah
    assert close(16.4 / 1.09, 15.0, 0.01)                          # "~15 ngày" của đề (tính từ số của đề)


def test_more_traffic_or_longer_backup_needs_more_battery():
    base = pb.Assumptions()
    busy = pb.Assumptions(parcels_per_day=200)
    long_backup = pb.Assumptions(backup_h=48)
    assert pb.always_on_wh_per_day(busy) > pb.always_on_wh_per_day(base)
    assert pb.deep_sleep_wh_per_day(busy) > pb.deep_sleep_wh_per_day(base)
    assert close(pb.c_min_ah(pb.deep_sleep_wh_per_day(base), long_backup),
                 2 * pb.c_min_ah(pb.deep_sleep_wh_per_day(base), base), 0.001)


def test_peak_current_with_solenoid_and_4g_together_is_covered_by_the_adapter():
    """Đề: dòng đỉnh ~1,3 A tại 12V, adapter 3 A dư ~2,3 lần. Tính lại từ các thành phần cho ra ~1,4 A (dư ~2,1 lần)."""
    a = pb.Assumptions()
    peak = pb.peak_current_a(a)
    assert 1.2 < peak < 1.6
    assert pb.report(a)["peak"]["adapter_ok"] and pb.report(a)["peak"]["margin_x"] >= 2.0
    # solenoid một mình 0,6 A; thêm 4G phát phải làm dòng tăng rõ rệt
    assert peak > a.solenoid_a + 0.5


def test_a_weaker_adapter_is_flagged_and_a_bigger_modem_raises_the_peak():
    weak = pb.Assumptions(adapter_a=1.5)
    assert pb.report(weak)["peak"]["adapter_ok"] is False
    assert pb.peak_current_a(pb.Assumptions(modem_peak_a=3.0)) > pb.peak_current_a(pb.Assumptions())
    assert pb.peak_current_a(pb.Assumptions(solenoid_a=1.2)) > pb.peak_current_a(pb.Assumptions())
