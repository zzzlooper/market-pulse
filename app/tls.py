"""HTTPS for opening the dashboard from other devices, with Market Pulse's own certificates.

A small local certificate authority (CA) is created once and signs a server certificate for
this computer's private addresses. You install the CA certificate on your phone once; after
that the phone trusts the dashboard, also when the server certificate is renewed or the
computer gets a new address.

Safety:
- The CA is name-constrained: it can only vouch for localhost and private network addresses
  (192.168.x.x, 10.x.x.x…). Even if its key leaked, it couldn't impersonate any public website
  on your phone.
- Both private keys stay in data/tls, readable by you only. Only the CA certificate, which is
  public, is ever served or shown.
- The server certificate names addresses only, not this computer's name: certificates are
  shown to anyone who connects, and computer names often contain a person's name.

    python -m app.tls            create or renew the certificates and say what to do next
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_DAYS = 3650
SERVER_DAYS = 397  # the longest phones accept for a server certificate
RENEW_DAYS = 30
PRIVATE_NETWORKS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "100.64.0.0/10",  # carrier-grade NAT, also used by VPNs such as Tailscale
    "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10",
)]


def is_private(ip: ipaddress._BaseAddress) -> bool:
    return any(ip in net for net in PRIVATE_NETWORKS)


def local_addresses() -> list[str]:
    """This computer's private IP addresses, loopback first. No network traffic: read from the
    system (`ip -j addr` on Linux, or what the host name resolves to)."""
    found: list[str] = ["127.0.0.1", "::1"]
    try:
        out = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=5, check=True).stdout
        for iface in json.loads(out):
            found += [a["local"] for a in iface.get("addr_info", []) if a.get("local")]
    except (OSError, ValueError, subprocess.SubprocessError):
        try:
            found += [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None)]
        except OSError:
            pass
    ips = []
    for text in found:
        try:
            ip = ipaddress.ip_address(text.split("%")[0])
        except ValueError:
            continue
        # Link-local addresses (fe80::…, 169.254.x.x) can't be typed into a phone's browser.
        if is_private(ip) and not ip.is_link_local and str(ip) not in ips:
            ips.append(str(ip))
    return ips


def _write(path: Path, data: bytes, private: bool) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(path, 0o600 if private else 0o644)


def _key_pem(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _make_ca(now: datetime):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Market Pulse local CA"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Market Pulse (this computer only)")])
    constraints = x509.NameConstraints(
        permitted_subtrees=[x509.DNSName("localhost"), *(x509.IPAddress(n) for n in PRIVATE_NETWORKS)],
        excluded_subtrees=None)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                         crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(constraints, critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    return key, cert


def _make_server(ca_key, ca_cert, addresses: list[str], now: datetime):
    key = ec.generate_private_key(ec.SECP256R1())
    sans = [x509.DNSName("localhost"), *(x509.IPAddress(ipaddress.ip_address(a)) for a in addresses)]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Market Pulse")]))
            .issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=SERVER_DAYS))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    return key, cert


def _covered(cert: x509.Certificate) -> list[str]:
    return [str(ip) for ip in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            .value.get_values_for_type(x509.IPAddress)]


def fingerprint(cert: x509.Certificate) -> str:
    """SHA-256 of the certificate, as phones show it: AB:CD:…"""
    digest = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def ensure(folder: Path, addresses: list[str] | None = None, now: datetime | None = None) -> dict:
    """Create the CA once, and (re)issue the server certificate when it's missing, due within
    RENEW_DAYS, or this computer's addresses changed. Returns what status() returns, plus
    whether anything was created."""
    now = now or datetime.now(UTC)
    addresses = addresses if addresses is not None else local_addresses()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    created = []
    ca_key_path, ca_path = folder / "ca.key", folder / "ca.crt"
    if not (ca_key_path.exists() and ca_path.exists()):
        ca_key, ca_cert = _make_ca(now)
        _write(ca_key_path, _key_pem(ca_key), private=True)
        _write(ca_path, ca_cert.public_bytes(serialization.Encoding.PEM), private=False)
        created.append("ca")
        (folder / "server.crt").unlink(missing_ok=True)  # a new CA needs a new server certificate
    ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
    ca_cert = x509.load_pem_x509_certificate(ca_path.read_bytes())

    server_path, server_key_path = folder / "server.crt", folder / "server.key"
    due = True
    if server_path.exists() and server_key_path.exists():
        current = x509.load_pem_x509_certificate(server_path.read_bytes())
        due = (current.not_valid_after_utc - now < timedelta(days=RENEW_DAYS)
               or sorted(_covered(current)) != sorted(addresses))
    if due:
        key, cert = _make_server(ca_key, ca_cert, addresses, now)
        _write(server_key_path, _key_pem(key), private=True)
        _write(server_path, cert.public_bytes(serialization.Encoding.PEM), private=False)
        created.append("server")
    return {**status(folder, now), "created": created}


def status(folder: Path, now: datetime | None = None) -> dict:
    """What Setup shows: public facts only, never a key."""
    now = now or datetime.now(UTC)
    ca_path, server_path = folder / "ca.crt", folder / "server.crt"
    if not (ca_path.exists() and server_path.exists()):
        return {"exists": False}
    ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
    server = x509.load_pem_x509_certificate(server_path.read_bytes())
    return {
        "exists": True,
        "fingerprint": fingerprint(ca),
        "addresses": [a for a in _covered(server) if a not in ("127.0.0.1", "::1")],
        "server_expires": server.not_valid_after_utc.date().isoformat(),
        "renew_soon": server.not_valid_after_utc - now < timedelta(days=RENEW_DAYS),
    }


def ca_der(folder: Path) -> bytes | None:
    """The CA certificate (public) in the form phones install, or None."""
    path = folder / "ca.crt"
    if not path.exists():
        return None
    return x509.load_pem_x509_certificate(path.read_bytes()).public_bytes(serialization.Encoding.DER)


def main(argv: list[str] | None = None) -> int:
    from .config import TLS_DIR

    parser = argparse.ArgumentParser(prog="python -m app.tls", description="Create or renew Market Pulse's HTTPS certificates.")
    parser.add_argument("--quiet", action="store_true", help="say nothing unless something was created")
    args = parser.parse_args(argv)
    folder = TLS_DIR
    info = ensure(folder)
    if args.quiet and not info["created"]:
        return 0
    port = os.environ.get("MP_PORT", "8000")
    print(("Created" if info["created"] else "Up to date") + f": certificates in {folder.name}/ (keys readable by you only)")
    print(f"Covers: localhost and {', '.join(info['addresses']) or 'no private network address'}; "
          f"renews before {info['server_expires']}")
    print(f"CA fingerprint (check it on your phone): {info['fingerprint']}")
    if not args.quiet:
        print(f"\nOn the phone, open https://<address above>:{port}/market-pulse-ca.crt, install the certificate,")
        print("and trust it (iPhone: Settings > General > About > Certificate Trust Settings). See docs/setup.md: HTTPS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
