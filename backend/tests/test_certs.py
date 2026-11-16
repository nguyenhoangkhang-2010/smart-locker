"""Chứng chỉ do tools/make_certs.py sinh ra phải chạy được trong một cuộc bắt tay mTLS thật.

Broker thật (Mosquitto) cần Docker nên chưa thử ở đây; test này dùng máy chủ TLS của Python với đúng
các tệp sinh ra, để chắc rằng chuỗi tin cậy, SAN, EKU và việc ép CN đều đúng.
"""
import importlib.util
import socket
import ssl
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("make_certs", ROOT / "tools" / "make_certs.py")
make_certs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_certs)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    d = tmp_path_factory.mktemp("pki")
    prints = make_certs.generate(d / "good", ["localhost", "127.0.0.1"], ["LOCKER-HCM-001", "LOCKER-HCM-002"])
    other = make_certs.generate(d / "evil", ["localhost"], ["LOCKER-HCM-001"])   # một CA khác, cùng tên tủ
    return d / "good", d / "evil", prints


class Server:
    """Giống broker: ép chứng chỉ máy khách, ghi lại CN của người vừa nối. crl=True: kiểm danh sách thu hồi
    (Mosquitto làm việc này với `crlfile`)."""

    def __init__(self, good, crl=False):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(good / "server.crt", good / "server.key")
        ctx.load_verify_locations(good / "ca.crt")
        if crl:
            ctx.load_verify_locations(good / "ca.crl")
            ctx.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self.ctx = ctx
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.seen = []
        self._stop = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                with self.ctx.wrap_socket(conn, server_side=True) as tls:
                    cert = tls.getpeercert()
                    cn = dict(x[0] for x in cert["subject"])["commonName"]
                    self.seen.append((cn, tls.version()))
                    tls.sendall(b"ok")
            except (ssl.SSLError, OSError):
                pass

    def close(self):
        self._stop = True
        self.sock.close()


def connect(server, ca, client=None, hostname="localhost"):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(ca / "ca.crt")
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if client:
        ctx.load_cert_chain(client[0], client[1])
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname=hostname) as tls:
            return tls.recv(2)


def refused(server, ca, client=None, hostname="localhost") -> bool:
    """Bị từ chối = lỗi TLS, hoặc kết nối bị đóng mà không nhận được "ok".
    Với TLS 1.3 client bắt tay xong trước khi server kiểm tra chứng chỉ của client, nên phía client có thể
    chỉ thấy kết nối đóng chứ không có ngoại lệ; cái đáng tin là server không ghi nhận người nối."""
    try:
        return connect(server, ca, client, hostname) != b"ok"
    except (ssl.SSLError, ConnectionError, OSError):
        return True


def pair(d, cn):
    return d / f"{cn}.crt", d / f"{cn}.key"


def test_a_device_with_a_valid_certificate_connects_and_is_identified_by_cn(pki):
    good, _, _ = pki
    server = Server(good)
    try:
        assert connect(server, good, pair(good, "LOCKER-HCM-001")) == b"ok"
        assert connect(server, good, pair(good, "locker-cloud")) == b"ok"
        assert [cn for cn, _ in server.seen] == ["LOCKER-HCM-001", "locker-cloud"]
        assert all(v in ("TLSv1.2", "TLSv1.3") for _, v in server.seen)
    finally:
        server.close()


def test_connection_without_a_client_certificate_is_refused(pki):
    good, _, _ = pki
    server = Server(good)
    try:
        assert refused(server, good, client=None)
        assert server.seen == []
    finally:
        server.close()


def test_certificate_from_another_ca_is_refused_even_with_the_same_device_name(pki):
    good, evil, _ = pki
    server = Server(good)
    try:
        assert refused(server, good, pair(evil, "LOCKER-HCM-001"))
        assert server.seen == []
    finally:
        server.close()


def test_a_client_checks_the_broker_name_and_the_ca(pki):
    good, evil, _ = pki
    server = Server(good)
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            connect(server, good, pair(good, "LOCKER-HCM-001"), hostname="broker.evil.example")   # SAN không khớp
        with pytest.raises(ssl.SSLCertVerificationError):
            connect(server, evil, pair(good, "LOCKER-HCM-001"))                                      # tủ chỉ tin CA của mình
    finally:
        server.close()


def test_fingerprints_are_unique_per_device_and_stable_format(pki):
    _, _, prints = pki
    assert set(prints) == {"locker-cloud", "LOCKER-HCM-001", "LOCKER-HCM-002"}
    assert len(set(prints.values())) == 3
    assert all(len(v) == 64 and set(v) <= set("0123456789abcdef") for v in prints.values())


def test_firmware_header_embeds_the_three_pems(tmp_path):
    header = tmp_path / "certs.h"
    make_certs.generate(tmp_path / "c", ["localhost"], ["LOCKER-HCM-001"], firmware_header=header)
    text = header.read_text()
    assert text.count("-----BEGIN CERTIFICATE-----") == 2 and "BEGIN PRIVATE KEY" in text
    assert "CA_PEM" in text and "CLIENT_CERT_PEM" in text and "CLIENT_KEY_PEM" in text


def test_a_revoked_device_is_refused_while_the_others_keep_working(tmp_path):
    """Tủ bị đánh cắp: thu hồi chứng chỉ thì không nối lại được, chứng chỉ khác không bị ảnh hưởng."""
    d = tmp_path / "pki"
    make_certs.generate(d, ["localhost", "127.0.0.1"], ["LOCKER-HCM-001", "LOCKER-HCM-002"])
    assert (d / "ca.crl").exists()

    server = Server(d, crl=True)                                   # CRL rỗng: ai cũng nối được
    try:
        assert connect(server, d, pair(d, "LOCKER-HCM-001")) == b"ok"
    finally:
        server.close()

    serial = make_certs.revoke(d, "LOCKER-HCM-001")
    assert make_certs.write_crl(d) == [serial]
    assert make_certs.revoke(d, "LOCKER-HCM-001") == serial        # thu hồi lần hai không nhân đôi
    assert (d / "revoked.txt").read_text().count("LOCKER-HCM-001") == 1

    server = Server(d, crl=True)                                   # broker nạp CRL mới
    try:
        assert refused(server, d, pair(d, "LOCKER-HCM-001"))
        assert connect(server, d, pair(d, "LOCKER-HCM-002")) == b"ok"
        assert connect(server, d, pair(d, "locker-cloud")) == b"ok"
        assert [cn for cn, _ in server.seen] == ["LOCKER-HCM-002", "locker-cloud"]
    finally:
        server.close()

    unrevoked = Server(d, crl=False)                               # đối chứng: không có CRL thì vẫn nối được
    try:
        assert connect(unrevoked, d, pair(d, "LOCKER-HCM-001")) == b"ok"
    finally:
        unrevoked.close()
