"""Chế độ demo cho Wokwi (KHÔNG cần gói trả phí): firmware và backend cùng nối một broker công cộng.

    cd backend
    .venv/Scripts/python.exe ../tools/wokwi_demo_setup.py            # bật chế độ demo
    .venv/Scripts/python.exe ../tools/wokwi_demo_setup.py --restore  # trả cấu hình về như trước

Wokwi chỉ ra được internet công cộng (không thấy localhost của máy bạn), nên không nối được Mosquitto cục bộ
nếu không có Private Gateway (trả phí). Cách thay thế là broker công cộng, nhưng khi đó KHÔNG có mTLS và ACL:
bảo vệ duy nhất là `device_id` dài, ngẫu nhiên nằm trong tên topic. Vì vậy:
  - script sinh device_id mới dạng LOCKER-DEMO-<16 hex> (không đoán được) và KHÔNG in ra màn hình;
  - backend chỉ nghe đúng thiết bị đó (MQTT_ONLY_DEVICE), không dùng "+" trên broker của người lạ;
  - chỉ dùng cho demo, đừng đưa dữ liệu thật qua đường này.

Script sửa include/secrets.h và backend/.env (đều nằm ngoài git) và đăng ký tủ trong database.
Bản sao lưu trước khi sửa: *.bak-before-demo.
"""
import argparse
import re
import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "include" / "secrets.h"
ENV = ROOT / "backend" / ".env"


def backup(path: Path) -> None:
    bak = path.with_name(path.name + ".bak-before-demo")
    if not bak.exists():
        shutil.copy2(path, bak)


def set_define(text: str, name: str, value: str) -> str:
    line = f"#define {name} {value}"
    pattern = rf"^#define {name}\b.*$"
    if re.search(pattern, text, re.M):
        return re.sub(pattern, line, text, flags=re.M)
    return text.replace("\n#endif", f"\n{line}\n\n#endif", 1)


def set_env(lines: list[str], updates: dict) -> list[str]:
    keys = set(updates)
    kept = [l for l in lines if l.split("=", 1)[0] not in keys]
    return kept + [f"{k}={v}" for k, v in updates.items()]


def main() -> None:
    for stream in (sys.stdout, sys.stderr):   # console Windows hay dùng cp1252, làm sập khi in tiếng Việt
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--broker", default="broker.hivemq.com")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--new-id", action="store_true", help="đổi sang device_id mới (khi nghi bị lộ)")
    args = parser.parse_args()

    if args.restore:
        for path in (SECRETS, ENV):
            bak = path.with_name(path.name + ".bak-before-demo")
            if bak.exists():
                shutil.copy2(bak, path)
                print(f"đã khôi phục {path.relative_to(ROOT)}")
            else:
                print(f"không có bản sao lưu của {path.relative_to(ROOT)}")
        return

    for path in (SECRETS, ENV):
        if not path.exists():
            sys.exit(f"thiếu {path}: xem README gốc, mục 'Chạy lần đầu'")
        backup(path)

    secrets_text = SECRETS.read_text(encoding="utf-8")
    existing = re.search(r'^#define SECRET_DEVICE_ID "(LOCKER-DEMO-[0-9a-f]{16})"', secrets_text, re.M)
    device_id = existing.group(1) if existing and not args.new_id else "LOCKER-DEMO-" + secrets.token_hex(8)

    secrets_text = set_define(secrets_text, "SECRET_MQTT_HOST", f'"{args.broker}"')
    secrets_text = set_define(secrets_text, "SECRET_MQTT_TLS", "0")
    # Demo: không ngủ sâu, nếu không sau 30 giây tủ ngủ và backend thấy tủ "offline" suốt buổi thử
    secrets_text = set_define(secrets_text, "SECRET_DEEP_SLEEP", "0")
    # Sơ đồ Wokwi chỉ đấu 3 ngăn: firmware và backend phải cùng biết, nếu không tủ offline có thể chọn ngăn không có thật
    secrets_text = set_define(secrets_text, "SECRET_NUM_COMPARTMENTS", "3")
    secrets_text = set_define(secrets_text, "SECRET_COMP_SIZES", '"MMM"')
    secrets_text = set_define(secrets_text, "SECRET_DEVICE_ID", f'"{device_id}"')
    SECRETS.write_text(secrets_text, encoding="utf-8")

    env_lines = ENV.read_text(encoding="utf-8").splitlines()
    env_lines = set_env(env_lines, {
        "MQTT_ENABLED": "true",
        "MQTT_HOST": args.broker,
        "MQTT_PORT": str(args.port),
        "MQTT_TLS": "false",
        "MQTT_ONLY_DEVICE": device_id,
        "MQTT_CLIENT_ID": "locker-cloud-" + secrets.token_hex(6),   # client id trùng trên broker công cộng sẽ đá nhau
    })
    ENV.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    sys.path.insert(0, str(ROOT / "backend"))
    from app.db import migrate
    from app.services import lockers

    migrate()
    # sơ đồ Wokwi chỉ có 3 ngăn (LED + công tắc cửa ở GPIO tương ứng): chỉ đăng ký 3 ngăn cỡ vừa để đơn nào
    # cũng rơi vào ngăn có thật. Tủ thật dùng 8 ngăn (3 nhỏ, 3 vừa, 2 lớn).
    lockers.register_locker(device_id, "Tu demo Wokwi", sizes="MMM", address="Wokwi")

    print(f"đã bật chế độ demo qua {args.broker}:{args.port} (không mã hóa)")
    print("  include/secrets.h  : SECRET_MQTT_HOST, SECRET_MQTT_TLS 0, SECRET_DEVICE_ID")
    print("  backend/.env       : MQTT_HOST/PORT/TLS, MQTT_ONLY_DEVICE, MQTT_CLIENT_ID")
    print("  database           : đã đăng ký tủ demo với 3 ngăn cỡ vừa (đúng 3 ngăn có trong sơ đồ Wokwi)")
    print("device_id đã lưu trong hai file trên; hãy giữ kín (trên broker công cộng nó là thứ duy nhất chặn người lạ).")
    print("tiếp theo: pio run -e esp32dev, rồi mở Wokwi. Xem README gốc, mục 'Chạy trong Wokwi'.")


if __name__ == "__main__":
    main()
