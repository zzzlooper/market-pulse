"""HTTPS with Market Pulse's own certificates (app/tls.py, app/serve.py)."""
import base64
import hashlib
import ipaddress
import json
import shutil
import stat
import subprocess
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from browser_kit import CHROMIUM, Browser, ReplayServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from cryptography.x509.verification import PolicyBuilder, Store, VerificationError
from scenario_kit import WIRE, Day

from app import tls
from app.tls import ca_der, ensure, fingerprint, local_addresses, status

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
HOME = ["192.168.1.20"]
TOKEN = "mp-https-test-token-7Kq2"


def load(path):
    return x509.load_pem_x509_certificate(path.read_bytes())


# --- the certificates ----------------------------------------------------------------------------------

def test_keys_are_private_certificates_public_and_nothing_is_redone_needlessly(tmp_path):
    folder = tmp_path / "tls"
    first = ensure(folder, HOME, NOW)
    assert first["created"] == ["ca", "server"] and first["addresses"] == HOME
    mode = {p.name: stat.S_IMODE(p.stat().st_mode) for p in folder.iterdir()}
    assert mode == {"ca.key": 0o600, "server.key": 0o600, "ca.crt": 0o644, "server.crt": 0o644}
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert ensure(folder, HOME, NOW + timedelta(days=100))["created"] == []
    server = load(folder / "server.crt")
    assert server.not_valid_after_utc - server.not_valid_before_utc <= timedelta(days=398)  # phones' limit
    assert x509.ExtendedKeyUsageOID.SERVER_AUTH in server.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value


def test_the_server_certificate_is_renewed_but_the_ca_your_phone_trusts_is_kept(tmp_path):
    folder = tmp_path / "tls"
    ensure(folder, HOME, NOW)
    ca_before = (folder / "ca.crt").read_bytes()
    moved = ensure(folder, ["192.168.1.77"], NOW + timedelta(days=1))  # the computer got a new address
    assert moved["created"] == ["server"] and moved["addresses"] == ["192.168.1.77"]
    due = ensure(folder, ["192.168.1.77"], NOW + timedelta(days=380))  # within 30 days of expiry
    assert due["created"] == ["server"]
    assert (folder / "ca.crt").read_bytes() == ca_before  # no need to reinstall anything on the phone


def test_the_ca_can_vouch_for_private_addresses_only(tmp_path):
    """Name constraints: even with the CA key, no certificate for a public site is trusted."""
    folder = tmp_path / "tls"
    ensure(folder, HOME, NOW)
    ca = load(folder / "ca.crt")
    ca_key = serialization.load_pem_private_key((folder / "ca.key").read_bytes(), None)

    def forged(name):
        key = ec.generate_private_key(ec.SECP256R1())
        return (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "x")]))
                .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(NOW - timedelta(minutes=1)).not_valid_after(NOW + timedelta(days=30))
                .add_extension(x509.SubjectAlternativeName([name]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .sign(ca_key, hashes.SHA256()))

    def trusted(cert, name) -> bool:
        try:
            PolicyBuilder().store(Store([ca])).time(NOW).build_server_verifier(name).verify(cert, [])
            return True
        except VerificationError as err:
            assert "name constraints" in str(err)
            return False

    bank, public_ip = x509.DNSName("bank.example"), x509.IPAddress(ipaddress.ip_address("8.8.8.8"))
    lan = x509.IPAddress(ipaddress.ip_address("192.168.1.20"))
    assert not trusted(forged(bank), bank) and not trusted(forged(public_ip), public_ip)
    assert trusted(load(folder / "server.crt"), lan)

    if shutil.which("openssl"):  # what most TLS clients build on agrees
        (tmp_path / "forged.crt").write_bytes(forged(bank).public_bytes(serialization.Encoding.PEM))
        result = subprocess.run(["openssl", "verify", "-attime", str(int(NOW.timestamp())),
                                 "-CAfile", str(folder / "ca.crt"), str(tmp_path / "forged.crt")],
                                capture_output=True, text=True, check=False)
        assert result.returncode != 0 and "permitted subtree violation" in result.stdout + result.stderr


def test_what_is_shown_or_served_is_public_only(tmp_path):
    folder = tmp_path / "tls"
    ensure(folder, HOME, NOW)
    info = status(folder, NOW)
    keys = [(folder / k).read_text() for k in ("ca.key", "server.key")]
    text = json.dumps(info)
    assert "PRIVATE KEY" not in text and not any(k.strip().splitlines()[1] in text for k in keys)
    assert info["fingerprint"] == fingerprint(load(folder / "ca.crt")) and len(info["fingerprint"]) == 95
    der = ca_der(folder)
    assert x509.load_der_x509_certificate(der).subject == load(folder / "ca.crt").subject
    assert ca_der(tmp_path / "missing") is None and status(tmp_path / "missing") == {"exists": False}


def test_only_typeable_private_addresses_go_into_the_certificate(monkeypatch):
    ip_output = json.dumps([
        {"addr_info": [{"local": "127.0.0.1"}, {"local": "::1"}]},
        {"addr_info": [{"local": "192.168.1.20"}, {"local": "fe80::1"}, {"local": "2001:db8::5"}]},
        {"addr_info": [{"local": "203.0.113.9"}, {"local": "100.101.102.103"}, {"local": "169.254.3.4"}]},
    ])
    monkeypatch.setattr(tls.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, ip_output, ""))
    assert local_addresses() == ["127.0.0.1", "::1", "192.168.1.20", "100.101.102.103"]


# --- served over HTTPS --------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def https_server(tmp_path_factory):
    folder = tmp_path_factory.mktemp("https")
    day = Day(folder / "day", start="04:00")
    day.quotes("09:30", BOLT=2.0)
    recording = day.build(["BOLT"])
    config = f'access_token = "{TOKEN}"\nticker_news = false\n[feeds]\n"Wire" = "{WIRE}"\n'
    server = ReplayServer(recording, folder / "server", start="09:40", config=config, https=True)
    yield server
    server.stop()


def test_the_dashboard_is_served_over_https_trusted_only_through_its_own_ca(https_server):
    assert https_server.get("/").status_code == 200
    with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
        httpx.get(https_server.url + "/", timeout=5)  # the public trust store doesn't know it
    with pytest.raises(httpx.HTTPError):
        httpx.get(https_server.url.replace("https:", "http:") + "/", timeout=5)  # no plain HTTP on that port
    ca = https_server.get("/market-pulse-ca.crt")
    assert ca.status_code == 200 and ca.headers["content-type"] == "application/x-x509-ca-cert"
    served = x509.load_der_x509_certificate(ca.content)
    assert fingerprint(served) == status(https_server.data / "tls")["fingerprint"]


def test_over_https_the_sign_in_cookie_is_https_only_and_setup_says_encrypted(https_server):
    login = httpx.post(https_server.url + "/api/login", json={"token": TOKEN}, verify=https_server.verify, timeout=5)
    cookie = login.headers["set-cookie"].lower()
    assert login.status_code == 204 and "secure" in cookie and "httponly" in cookie
    headers = {"authorization": f"Bearer {TOKEN}"}
    items = {i["id"]: i for i in https_server.get("/api/setup", headers=headers).json()["items"]}
    assert items["https"]["state"] == "ok" and "renews before" in items["https"]["detail"]
    info = https_server.get("/api/tls", headers=headers).json()
    assert info["https"] is True and info["exists"] is True
    stores = {s["id"]: s for s in https_server.get("/api/data", headers=headers).json()["stores"]}
    assert stores["tls"]["detail"].startswith("Market Pulse's own CA")


@pytest.mark.skipif(CHROMIUM is None, reason="needs Chromium or Chrome")
def test_a_browser_shows_the_encrypted_connection_and_how_to_install_the_ca(https_server, tmp_path):
    # Chromium is told to accept this server's key only, standing in for a phone that has the CA installed.
    server_cert = load(https_server.data / "tls" / "server.crt")
    spki = server_cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    pin = base64.b64encode(hashlib.sha256(spki).digest()).decode()
    browser = Browser(tmp_path, [f"--ignore-certificate-errors-spki-list={pin}"])
    page = browser.page()
    try:
        page.goto(https_server.url + "/")
        page.wait("document.querySelector('#login').open")
        page.eval(f"document.querySelector('#login input').value = {json.dumps(TOKEN)};"
                  " document.querySelector('#login form').requestSubmit()")
        page.wait("document.querySelectorAll('#watchlist tr:not(.empty)').length === 1")
        page.click("#setup-btn")
        page.wait("!!document.querySelector('.https-box')")
        box = page.text(".https-box")
        assert "this connection is encrypted" in box and status(https_server.data / "tls")["fingerprint"] in box
        html = page.eval("document.documentElement.outerHTML")
        assert "PRIVATE KEY" not in html and TOKEN not in html
        assert [e for e in page.errors if "401" not in e] == []
    finally:
        page.close()
        browser.stop()
