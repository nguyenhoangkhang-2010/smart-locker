"""Tính dung lượng pin dự phòng (Đề tài 67, Câu 2A.3). Mọi giả định là tham số: đổi là ra số mới.

    python tools/power_budget.py                        # giả định mặc định (xem dưới)
    python tools/power_budget.py --radio-idle-ma 0      # CHỈ các giả định ghi trong đề
    python tools/power_budget.py --openings 200 --backup-h 48
    python tools/power_budget.py --json                 # cho script khác đọc

Công thức của đề:

    P_avg = SUM(I_i * t_i * V_i) / (3600 * eta_buck) + P_solenoid        (W; t_i = giây mỗi giờ)
    C_min = P_avg * t_backup / (V_bat * DoD * k_aging)                   (Ah)

LƯU Ý QUAN TRỌNG: chỉ dùng các giả định ghi trong đề (ESP32 80 mA liên tục, TX 500 mA x 2 s/lần gửi,
TFT 60 mA khi thao tác, ...) thì ra khoảng 28 mA @12V, KHÔNG phải 52,2 mA như bảng của đề. Muốn ra đúng bảng
phải có thêm khoảng 74 mA cho Wi-Fi luôn nối (nghe/idle), con số này đề không nêu nhưng hợp lý với datasheet
ESP32. Nó nằm ở tham số --radio-idle-ma (mặc định 73.7). Thay bằng số ĐO THỰC TẾ khi có phần cứng.
"""
import argparse
import sys
import json
from dataclasses import asdict, dataclass

V_LOGIC = 3.3
V_SOLENOID = 12.0


@dataclass
class Assumptions:
    # --- ghi trong đề ---
    esp32_ma: float = 80.0            # ESP32 hoạt động
    tx_ma: float = 500.0              # Wi-Fi/4G phát
    tx_s_per_send: float = 2.0        # mỗi lần gửi
    tft_ma: float = 60.0              # đèn nền TFT
    parcels_per_day: float = 60.0     # 60 đơn = 120 lần mở (gửi + lấy)
    interaction_s: float = 20.0       # mỗi lần người dùng thao tác
    heartbeat_s: float = 300.0
    eta_buck: float = 0.85
    dod: float = 0.80
    aging: float = 0.80
    v_bat: float = 12.0
    backup_h: float = 24.0
    solenoid_a: float = 0.6           # dòng khi hút
    solenoid_pulse_s: float = 0.5
    sleep_ua: float = 10.0            # deep sleep ESP32
    # --- KHÔNG ghi trong đề (suy ra để khớp bảng của đề; thay bằng số đo) ---
    radio_idle_ma: float = 73.7       # Wi-Fi luôn kết nối ở chế độ "luôn bật"
    wake_connect_s: float = 3.0       # deep sleep: thời gian nối lại Wi-Fi mỗi lần thức
    sends_per_opening: float = 1.0    # số lần gửi cho mỗi lần mở cửa (một lô)
    # --- dòng đỉnh (đề: "khi solenoid mở đồng thời 4G phát khoảng 1.3 A tại 12V; adapter 12V-3A dư ~2.3 lần") ---
    modem_peak_a: float = 2.0         # module 4G phát (đề: dòng đỉnh ~2 A) trên đường 4 V riêng
    modem_v: float = 4.0
    adapter_a: float = 3.0

    @property
    def openings_per_day(self) -> float:
        return self.parcels_per_day * 2

    @property
    def sends_per_day(self) -> float:
        return self.openings_per_day * self.sends_per_opening + 86400 / self.heartbeat_s


def solenoid_wh_per_day(a: Assumptions) -> float:
    return a.openings_per_day * a.solenoid_pulse_s * V_SOLENOID * a.solenoid_a / 3600


def always_on_wh_per_day(a: Assumptions) -> float:
    cpu = (a.esp32_ma + a.radio_idle_ma) / 1000 * V_LOGIC * 24
    tx = a.tx_ma / 1000 * V_LOGIC * a.sends_per_day * a.tx_s_per_send / 3600
    tft = a.tft_ma / 1000 * V_LOGIC * a.openings_per_day * a.interaction_s / 3600
    return (cpu + tx + tft) / a.eta_buck + solenoid_wh_per_day(a)


def deep_sleep_wh_per_day(a: Assumptions) -> float:
    awake_ma = a.esp32_ma + a.radio_idle_ma
    sleep = a.sleep_ua / 1e6 * V_LOGIC * 24
    # mỗi heartbeat: thức, nối Wi-Fi, phát
    beat = 86400 / a.heartbeat_s * (awake_ma * a.wake_connect_s + a.tx_ma * a.tx_s_per_send) / 1000 * V_LOGIC / 3600
    # mỗi lần có người thao tác: thức suốt phiên (có TFT), rồi phát
    use = a.openings_per_day * ((awake_ma + a.tft_ma) * a.interaction_s + a.tx_ma * a.tx_s_per_send * a.sends_per_opening) / 1000 * V_LOGIC / 3600
    return (sleep + beat + use) / a.eta_buck + solenoid_wh_per_day(a)


def peak_current_a(a: Assumptions) -> float:
    """Dòng đỉnh lấy từ đường 12 V khi solenoid hút ĐỒNG THỜI với 4G phát (trường hợp xấu nhất của đề):
    solenoid + (4G + ESP32 hoạt động + TFT) qua buck hiệu suất eta_buck. Khi 4G phát thì Wi-Fi không phát
    cùng lúc nên không cộng dòng TX Wi-Fi."""
    logic_w = a.modem_peak_a * a.modem_v + (a.esp32_ma + a.tft_ma) / 1000 * V_LOGIC
    return a.solenoid_a + logic_w / a.eta_buck / V_SOLENOID


def c_min_ah(wh_per_day: float, a: Assumptions) -> float:
    """Dung lượng pin tối thiểu (Ah) để chạy backup_h giờ."""
    p_avg = wh_per_day / 24
    return p_avg * a.backup_h / (a.v_bat * a.dod * a.aging)


def usable_wh(capacity_ah: float, nominal_v: float, a: Assumptions) -> float:
    return capacity_ah * nominal_v * a.dod * a.aging


def report(a: Assumptions) -> dict:
    out = {}
    for name, wh in (("always_on", always_on_wh_per_day(a)), ("deep_sleep", deep_sleep_wh_per_day(a))):
        w = wh / 24
        out[name] = {
            "avg_current_ma_at_12v": round(w / 12 * 1000, 1),
            "avg_power_w": round(w, 3),
            "energy_24h_wh": round(wh, 2),
            "c_min_ah": round(c_min_ah(wh, a), 2),
        }
    peak = peak_current_a(a)
    out["peak"] = {"peak_current_a_at_12v": round(peak, 2), "adapter_a": a.adapter_a,
                   "margin_x": round(a.adapter_a / peak, 2), "adapter_ok": a.adapter_a >= 1.5 * peak}
    out["sleep_saving_factor"] = round(out["always_on"]["energy_24h_wh"] / out["deep_sleep"]["energy_24h_wh"], 1)
    lifepo4 = usable_wh(2.0, 12.8, a)
    out["lifepo4_2ah_12v8"] = {
        "usable_wh": round(lifepo4, 1),
        "days_in_deep_sleep": round(lifepo4 / out["deep_sleep"]["energy_24h_wh"], 1),
        "hours_always_on": round(lifepo4 / (out["always_on"]["energy_24h_wh"] / 24), 1),
    }
    return out


PDF_TABLE = {"always_on": {"avg_current_ma_at_12v": 52.2, "avg_power_w": 0.63, "energy_24h_wh": 15.0, "c_min_ah": 1.96},
             "deep_sleep": {"avg_current_ma_at_12v": 3.8, "avg_power_w": 0.045, "energy_24h_wh": 1.09, "c_min_ah": 0.14}}


def main() -> None:
    for stream in (sys.stdout, sys.stderr):   # console Windows hay dùng cp1252, làm sập khi in tiếng Việt
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for field, default in asdict(Assumptions()).items():
        parser.add_argument("--" + field.replace("_", "-"), type=float, default=default)
    parser.add_argument("--openings", type=float, help="số lần mở cửa mỗi ngày (thay cho 2 x parcels-per-day)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    values = {k: getattr(args, k) for k in asdict(Assumptions())}
    if args.openings:
        values["parcels_per_day"] = args.openings / 2
    a = Assumptions(**values)
    result = report(a)

    if args.json:
        print(json.dumps({"assumptions": asdict(a), "result": result, "pdf": PDF_TABLE}, indent=2))
        return

    print(f"{a.openings_per_day:.0f} lần mở/ngày, {a.sends_per_day:.0f} lần gửi/ngày, backup {a.backup_h:.0f} h, "
          f"pin {a.v_bat:.0f} V, DoD {a.dod:.0%}, lão hóa {a.aging}, buck {a.eta_buck:.0%}, radio idle {a.radio_idle_ma} mA\n")
    print(f"{'chế độ':12} {'dòng TB @12V':>14} {'công suất':>10} {'năng lượng 24h':>15} {'pin tối thiểu':>14}   (đề)")
    for mode in ("always_on", "deep_sleep"):
        r, p = result[mode], PDF_TABLE[mode]
        print(f"{mode:12} {r['avg_current_ma_at_12v']:>11.1f} mA {r['avg_power_w']:>8.3f} W {r['energy_24h_wh']:>12.2f} Wh "
              f"{r['c_min_ah']:>11.2f} Ah   {p['avg_current_ma_at_12v']} mA / {p['energy_24h_wh']} Wh / {p['c_min_ah']} Ah")
    print(f"\nDeep sleep tiết kiệm ~{result['sleep_saving_factor']}x (đề: ~14x)")
    pack = result["lifepo4_2ah_12v8"]
    print(f"LiFePO4 12,8 V 2 Ah: {pack['usable_wh']} Wh khả dụng = {pack['days_in_deep_sleep']} ngày deep sleep "
          f"hoặc {pack['hours_always_on']} giờ luôn bật (đề: 16,4 Wh, ~15 ngày)")
    pk = result["peak"]
    print(f"Dòng đỉnh (solenoid + 4G phát): {pk['peak_current_a_at_12v']} A @12V; adapter {pk['adapter_a']} A dư {pk['margin_x']}x "
          f"({'đạt' if pk['adapter_ok'] else 'KHÔNG ĐẠT, cần dư >= 1,5x'}) (đề: ~1,3 A, dư ~2,3x)")
    if a.radio_idle_ma == 0:
        print("\n(--radio-idle-ma 0: chỉ dùng giả định ghi trong đề; so với cột (đề) sẽ thấy lệch ~2 lần.)")


if __name__ == "__main__":
    main()
