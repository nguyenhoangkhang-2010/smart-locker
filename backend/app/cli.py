"""Lệnh quản trị:  python -m app.cli <lệnh>

  migrate                          áp dụng migrations/*.sql
  gen-jwt-keys                     tạo cặp khóa ES256 trong keys/
  create-user --phone --role ...   tạo người dùng (mật khẩu hỏi lúc chạy, không nằm trong lịch sử shell)
  register-locker --id --name      thêm tủ + 8 ngăn (3 nhỏ, 3 vừa, 2 lớn); --lat/--lon để hiện trên bản đồ
  revoke-locker --id               thu hồi tủ bị đánh cắp (backend ngừng nhận dữ liệu của nó)
  seed-demo                        dữ liệu mẫu để xem dashboard (4 người dùng dùng chung một mật khẩu)
"""
import argparse
import getpass
import sys

from .config import BASE_DIR, settings


def gen_jwt_keys() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private = ec.generate_private_key(ec.SECP256R1())
    settings.jwt_private_key_file.parent.mkdir(parents=True, exist_ok=True)
    settings.jwt_private_key_file.write_bytes(private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    settings.jwt_public_key_file.write_bytes(private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    print(f"đã tạo {settings.jwt_private_key_file} và {settings.jwt_public_key_file}")
    print("khóa riêng KHÔNG được commit (keys/ đã nằm trong .gitignore)")


def _utf8_output() -> None:
    """Console Windows hay dùng cp1252 và làm sập chương trình khi in tiếng Việt."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main() -> None:
    _utf8_output()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    sub.add_parser("gen-jwt-keys")
    user = sub.add_parser("create-user")
    user.add_argument("--phone", required=True)
    user.add_argument("--role", required=True, choices=["ADMIN", "OPERATOR", "SHIPPER", "CUSTOMER"])
    user.add_argument("--name")
    demo = sub.add_parser("seed-demo")
    demo.add_argument("--reset", action="store_true", help="xóa đơn/sự kiện/cảnh báo cũ trước khi tạo")
    locker = sub.add_parser("register-locker")
    locker.add_argument("--id", required=True)
    locker.add_argument("--name", required=True)
    locker.add_argument("--address")
    locker.add_argument("--lat", type=float, help="vĩ độ (bản đồ dashboard)")
    locker.add_argument("--lon", type=float, help="kinh độ")
    locker.add_argument("--fingerprint", help="SHA-256 của chứng chỉ mTLS của tủ (tools/make_certs.py in ra)")
    revoke = sub.add_parser("revoke-locker")
    revoke.add_argument("--id", required=True)
    args = parser.parse_args()

    if args.cmd == "gen-jwt-keys":
        gen_jwt_keys()
    elif args.cmd == "migrate":
        from .db import migrate

        done = migrate()
        print("đã áp dụng:", ", ".join(done) if done else "không có gì mới")
    elif args.cmd == "create-user":
        from . import security
        from .db import query

        password = getpass.getpass("Mật khẩu (>= 8 ký tự): ")
        if len(password) < 8:
            sys.exit("mật khẩu quá ngắn")
        query("INSERT INTO users (phone, name, pw_hash, role) VALUES (%s, %s, %s, %s)",
              (args.phone, args.name, security.hash_password(password), args.role))
        print(f"đã tạo {args.role} {args.phone}")
    elif args.cmd == "seed-demo":
        import os

        from . import demo

        password = os.environ.get("DEMO_PASSWORD") or getpass.getpass("Mật khẩu cho 4 người dùng mẫu (>= 8 ký tự): ")
        if len(password) < 8:
            sys.exit("mật khẩu quá ngắn")
        print("đã tạo:", demo.seed(password, reset=args.reset))
        print("đăng nhập bằng 0900000001 (ADMIN), ...02 (OPERATOR), ...03 (SHIPPER), ...04 (CUSTOMER)")
    elif args.cmd == "register-locker":
        from .services import lockers

        lockers.register_locker(args.id, args.name, address=args.address, lat=args.lat, lon=args.lon)
        if args.fingerprint:
            lockers.set_fingerprint(args.id, args.fingerprint)
        print(f"đã đăng ký tủ {args.id}")
    elif args.cmd == "revoke-locker":
        from .services import lockers

        if not lockers.revoke(args.id):
            sys.exit("không có tủ này hoặc đã thu hồi rồi")
        print(f"đã thu hồi {args.id}: backend bỏ qua mọi tin của nó.")
        print(f"để broker cũng chặn ở tầng TLS: python tools/make_certs.py --revoke {args.id}, rồi nạp lại Mosquitto")


if __name__ == "__main__":
    main()
