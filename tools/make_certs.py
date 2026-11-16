"""Tạo chứng chỉ X.509 cho mTLS (Đề tài 67, Câu 2C.2): CA riêng, chứng chỉ broker, backend và từng tủ.

    cd backend
    .venv/Scripts/python.exe ../tools/make_certs.py --host localhost --host 192.168.1.50 --device LOCKER-HCM-001

Ghi vào infra/certs/ (đã nằm trong .gitignore): ca.crt, server.crt/.key, cloud.crt/.key, <device>.crt/.key,
và include/certs.h cho firmware. CN của chứng chỉ máy khách = tên người dùng MQTT = device_id, nên ACL của
Mosquitto (infra/mosquitto/acl) ép mỗi tủ chỉ dùng topic của chính nó.

Khóa riêng của CA không bao giờ rời khỏi thư mục này: dùng nó để cấp thêm tủ, cất ngoài máy chủ khi triển khai.

Thu hồi chứng chỉ của tủ bị đánh cắp (đề: "thu hồi được khi tủ bị đánh cắp"):

    python tools/make_certs.py --revoke LOCKER-HCM-001

ghi serial của chứng chỉ đó vào infra/certs/revoked.txt và sinh lại danh sách thu hồi ca.crl; Mosquitto đọc
`crlfile /mosquitto/certs/ca.crl` nên từ lần bắt tay sau tủ đó bị từ chối (nạp lại broker: docker compose restart mosquitto).
Song song, `python -m app.cli revoke-locker --id LOCKER-HCM-001` cho backend bỏ qua mọi dữ liệu của tủ.
"""
import argparse
import sys
import datetime
import hashlib
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parent.parent


def new_key():
    return ec.generate_private_key(ec.SECP256R1())     # nhỏ, nhanh, ESP32 (mbedTLS) hỗ trợ tốt


def pem_key(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def pem_cert(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Smart Locker"),
                      x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def issue(subject_cn, key, ca_cert, ca_key, days, *, server=False, sans=(), is_ca=False):
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (x509.CertificateBuilder()
               .subject_name(name(subject_cn))
               .issuer_name(ca_cert.subject if ca_cert else name(subject_cn))
               .public_key(key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(now - datetime.timedelta(minutes=5))
               .not_valid_after(now + datetime.timedelta(days=days))
               .add_extension(x509.BasicConstraints(ca=is_ca, path_length=0 if is_ca else None), critical=True))
    if is_ca:
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            encipher_only=False, decipher_only=False), critical=True)
    else:
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=True, key_encipherment=False, content_commitment=False,
            data_encipherment=False, key_agreement=True, key_cert_sign=False, crl_sign=False,
            encipher_only=False, decipher_only=False), critical=True)
        usage = ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH
        builder = builder.add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName(list(sans)), critical=False)
    return builder.sign(ca_key if ca_key else key, hashes.SHA256())


def san_for(hosts):
    out = []
    for h in hosts:
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            out.append(x509.DNSName(h))
    return out


def fingerprint(cert) -> str:
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def write_crl(out_dir: Path) -> list[int]:
    """Dựng lại ca.crl từ revoked.txt (mỗi dòng một serial dạng hex). Trả về danh sách serial bị thu hồi."""
    ca_cert = x509.load_pem_x509_certificate((out_dir / "ca.crt").read_bytes())
    ca_key = serialization.load_pem_private_key((out_dir / "ca.key").read_bytes(), password=None)
    listing = out_dir / "revoked.txt"
    serials = [int(line.split()[0], 16) for line in listing.read_text().splitlines() if line.strip()] if listing.exists() else []
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (x509.CertificateRevocationListBuilder()
               .issuer_name(ca_cert.subject)
               .last_update(now - datetime.timedelta(minutes=5))
               .next_update(now + datetime.timedelta(days=3650)))
    for serial in serials:
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder().serial_number(serial).revocation_date(now - datetime.timedelta(minutes=1)).build())
    crl = builder.sign(ca_key, hashes.SHA256())
    (out_dir / "ca.crl").write_bytes(crl.public_bytes(serialization.Encoding.PEM))
    return serials


def revoke(out_dir: Path, device: str) -> int:
    """Thêm chứng chỉ của `device` vào danh sách thu hồi và sinh lại CRL. Trả về serial đã thu hồi."""
    cert = x509.load_pem_x509_certificate((out_dir / f"{device}.crt").read_bytes())
    listing = out_dir / "revoked.txt"
    existing = listing.read_text() if listing.exists() else ""
    if f"{cert.serial_number:x}" not in existing.split():
        listing.write_text(existing + f"{cert.serial_number:x} {device}\n")
    write_crl(out_dir)
    return cert.serial_number


def generate(out_dir: Path, hosts, devices, days=825, firmware_header=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    ca_key = new_key()
    ca_cert = issue("Smart Locker CA", ca_key, None, None, 3650, is_ca=True)
    (out_dir / "ca.crt").write_bytes(pem_cert(ca_cert))
    (out_dir / "ca.key").write_bytes(pem_key(ca_key))

    server_key = new_key()
    server = issue(hosts[0], server_key, ca_cert, ca_key, days, server=True, sans=san_for(hosts))
    (out_dir / "server.crt").write_bytes(pem_cert(server))
    (out_dir / "server.key").write_bytes(pem_key(server_key))

    prints = {}
    for cn in ["locker-cloud", *devices]:
        key = new_key()
        cert = issue(cn, key, ca_cert, ca_key, days)
        (out_dir / f"{cn}.crt").write_bytes(pem_cert(cert))
        (out_dir / f"{cn}.key").write_bytes(pem_key(key))
        prints[cn] = fingerprint(cert)
    # tên cố định mà backend/.env trỏ tới
    for ext in ("crt", "key"):
        (out_dir / f"cloud.{ext}").write_bytes((out_dir / f"locker-cloud.{ext}").read_bytes())

    (out_dir / "revoked.txt").write_text("")
    write_crl(out_dir)                                   # CRL rỗng ngay từ đầu để broker luôn bật được crlfile

    if firmware_header and devices:
        dev = devices[0]
        text = ("// TỰ SINH bởi tools/make_certs.py. Chứa KHÓA RIÊNG của tủ: không commit (đã nằm trong .gitignore).\n"
                "#pragma once\n\n"
                f'static const char CA_PEM[] = R"PEM({(out_dir / "ca.crt").read_text()})PEM";\n'
                f'static const char CLIENT_CERT_PEM[] = R"PEM({(out_dir / (dev + ".crt")).read_text()})PEM";\n'
                f'static const char CLIENT_KEY_PEM[] = R"PEM({(out_dir / (dev + ".key")).read_text()})PEM";\n')
        firmware_header.write_text(text, encoding="utf-8")
    return prints


def main():
    for stream in (sys.stdout, sys.stderr):   # console Windows hay dùng cp1252, làm sập khi in tiếng Việt
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", action="append",
                        help="tên DNS hoặc IP mà tủ dùng để nối broker (lặp lại được); phải KHỚP SECRET_MQTT_HOST")
    parser.add_argument("--device", action="append", default=[], help="device_id của tủ (lặp lại được)")
    parser.add_argument("--out", type=Path, default=ROOT / "infra" / "certs")
    parser.add_argument("--days", type=int, default=825)
    parser.add_argument("--no-firmware-header", action="store_true", help="không ghi include/certs.h")
    parser.add_argument("--revoke", metavar="DEVICE", help="thu hồi chứng chỉ của tủ này (không sinh chứng chỉ mới)")
    args = parser.parse_args()

    if args.revoke:
        serial = revoke(args.out, args.revoke)
        print(f"đã thu hồi chứng chỉ của {args.revoke} (serial {serial:x}); CRL mới: {args.out / 'ca.crl'}")
        print("nạp lại broker để có hiệu lực:  docker compose restart mosquitto")
        return

    if not args.host:
        parser.error("cần --host (hoặc --revoke)")
    header = None if args.no_firmware_header else ROOT / "include" / "certs.h"
    prints = generate(args.out, args.host, args.device, args.days, header)
    print(f"đã ghi chứng chỉ vào {args.out}")
    for cn, fp in prints.items():
        print(f"  {cn:20} sha256 {fp}")
    if header and args.device:
        print(f"đã ghi {header} cho {args.device[0]}; trong include/secrets.h đặt SECRET_MQTT_TLS 1 và SECRET_MQTT_HOST khớp --host")
    print("lưu ý: ca.key là khóa gốc, cất ngoài máy chủ khi triển khai thật")


if __name__ == "__main__":
    main()
