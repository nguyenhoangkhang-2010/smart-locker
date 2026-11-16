"""Sinh diagram.json cho Wokwi với 8 ngăn (Đề tài 67), giữ nguyên ESP32, keypad, TFT và chiết áp pin.

    python tools/make_wokwi_diagram.py

Wokwi không có PCF8574 (chip mở rộng I/O của đề) nên bản mô phỏng dùng:
  - 74HC595 (đầu ra): Q0..Q7 -> điện trở 220 Ω -> LED = xung mở khóa của ngăn 1..8;
  - 74HC165 (đầu vào): D0..D7 <- công tắc trượt + điện trở kéo lên 10 kΩ = cửa ngăn 1..8
    (gạt sang trái = nối GND = cửa ĐÓNG; gạt sang phải = cửa MỞ).
Chỉ tốn 6 chân ESP32:  595: DS=D21, SHCP=D22, STCP=D19   165: PL=RX2(16), CP=TX2(17), Q7=D34.
Firmware phải bật SECRET_SHIFT_IO 1 (tools/wokwi_demo_setup.py làm giúp). Tủ thật dùng PCF8574, không dùng hai chip này.
Tên chân lấy từ tài liệu Wokwi (docs.wokwi.com/parts/wokwi-74hc595, wokwi-74hc165); chưa chạy trong Wokwi.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "diagram.json"
KEEP = {"esp", "keypad", "tft", "pot"}
N = 8


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    diagram = json.loads(TARGET.read_text(encoding="utf-8"))
    parts = [p for p in diagram["parts"] if p["id"] in KEEP]
    conns = [c for c in diagram["connections"] if not any(
        c[i].split(":")[0] not in KEEP and not c[i].startswith("$") for i in (0, 1))]

    def part(id_, type_, top, left, **attrs):
        entry = {"type": type_, "id": id_, "top": top, "left": left, "attrs": {k: v for k, v in attrs.items() if k != "rotate"}}
        if "rotate" in attrs:
            entry["rotate"] = attrs["rotate"]
        parts.append(entry)

    def wire(a, b, color):
        conns.append([a, b, color, []])

    # Hàng LED (xung khóa) ở trên, hàng công tắc cửa ở dưới; hai chip ở giữa
    part("sr595", "wokwi-74hc595", 330, 330)
    part("sr165", "wokwi-74hc165", 330, 560)
    for k in range(1, N + 1):
        x = 330 + 70 * (k - 1)
        part(f"led{k}", "wokwi-led", 130, x, color="green")
        part(f"rl{k}", "wokwi-resistor", 215, x - 25, value="220", rotate=90)
        part(f"door{k}", "wokwi-slide-switch", 480, x)
        part(f"rpu{k}", "wokwi-resistor", 560, x - 25, value="10000", rotate=90)

    # nguồn cho hai chip
    for chip in ("sr595", "sr165"):
        wire(f"{chip}:VCC", "esp:3V3", "red")
    wire("sr595:GND", "esp:GND.1", "black")
    wire("sr165:GND", "esp:GND.2", "black")
    wire("sr595:OE", "esp:GND.1", "black")      # OE tích cực thấp: luôn bật đầu ra
    wire("sr595:MR", "esp:3V3", "red")          # MR tích cực thấp: không bao giờ xóa
    wire("sr165:CE", "esp:GND.2", "black")      # CE tích cực thấp: luôn cho dịch
    # tín hiệu điều khiển
    wire("esp:D21", "sr595:DS", "green")
    wire("esp:D22", "sr595:SHCP", "green")
    wire("esp:D19", "sr595:STCP", "green")
    wire("esp:RX2", "sr165:PL", "orange")
    wire("esp:TX2", "sr165:CP", "orange")
    wire("esp:D34", "sr165:Q7", "orange")
    for k in range(1, N + 1):
        wire(f"sr595:Q{k - 1}", f"rl{k}:1", "green")
        wire(f"rl{k}:2", f"led{k}:A", "green")
        wire(f"led{k}:C", "esp:GND.1", "black")
        wire(f"door{k}:2", f"sr165:D{k - 1}", "yellow")
        wire(f"door{k}:1", "esp:GND.2", "black")
        wire(f"rpu{k}:1", "esp:3V3", "red")
        wire(f"rpu{k}:2", f"sr165:D{k - 1}", "yellow")

    diagram["parts"], diagram["connections"] = parts, conns
    TARGET.write_text(json.dumps(diagram, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"đã ghi {TARGET.name}: {len(parts)} linh kiện, {len(conns)} dây, {N} ngăn")


if __name__ == "__main__":
    main()
