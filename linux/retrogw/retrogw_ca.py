"""retrogw_ca.py - local certificate authority for the retro proxy's HTTPS.

The proxy accepts https:// from vintage browsers by acting as the site: it
presents a certificate for the requested host, signed by this CA. The CA is
created once and kept in the proxy's state directory; install its certificate
on the vintage PC (http://LOCAL_IP:PROXY_PORT/ca.crt) so the browser trusts it.

Choices made for 1990s clients:
  * SHA-1 signatures: Windows 95 CryptoAPI and Netscape 4 know nothing newer.
  * RSA 2048 CA, RSA 1024 host key (shared by all hosts; fast handshakes).
  * Valid 2000-01-01 .. 2049-12-31 (UTCTime range), so a PC with a wrong
    clock still accepts it.
  * Short serial numbers and plain X.509v3 extensions.
"""
import os, threading, datetime, ipaddress, hashlib, socket
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding

NOT_BEFORE = datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)
NOT_AFTER = datetime.datetime(2049, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc)

# sha256WithRSAEncryption / sha1WithRSAEncryption AlgorithmIdentifiers (same length)
_ALG256 = bytes.fromhex("300d06092a864886f70d01010b0500")
_ALG1 = bytes.fromhex("300d06092a864886f70d0101050500")

def _der_len(n):
    if n < 0x80: return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b

def _sign_sha1(builder, key):
    """Sign `builder` with SHA-1. The x509 builder refuses SHA-1, but plain
    RSA signing still allows it: build with SHA-256, switch the algorithm
    identifier inside the to-be-signed part, re-sign it, and reassemble."""
    tbs = builder.sign(key, hashes.SHA256()).tbs_certificate_bytes
    assert tbs.count(_ALG256) == 1
    tbs = tbs.replace(_ALG256, _ALG1)
    sig = key.sign(tbs, padding.PKCS1v15(), hashes.SHA1())
    bits = b"\x03" + _der_len(len(sig) + 1) + b"\x00" + sig
    body = tbs + _ALG1 + bits
    return x509.load_der_x509_certificate(b"\x30" + _der_len(len(body)) + body)

def _serial():
    return int.from_bytes(os.urandom(8), "big") >> 1 or 1

def _pem_key(k):
    return k.private_bytes(serialization.Encoding.PEM,
                           serialization.PrivateFormat.TraditionalOpenSSL,
                           serialization.NoEncryption())

class CA:
    def __init__(self, state_dir):
        self.dir = state_dir
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        self.ca_key, self.ca_cert = self._load_or_make_ca()
        self.host_key = self._load_or_make_key("host.key", 1024)
        self.host_key_pem = _pem_key(self.host_key).decode()
        self._cache = {}; self._lock = threading.Lock()

    # ---- persistent material ----
    def _write(self, name, data):
        p = os.path.join(self.dir, name)
        fd = os.open(p + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(p + ".tmp", p)

    def _load_or_make_key(self, name, bits):
        p = os.path.join(self.dir, name)
        if os.path.exists(p):
            return serialization.load_pem_private_key(open(p, "rb").read(), None)
        k = rsa.generate_private_key(public_exponent=65537, key_size=bits)
        self._write(name, _pem_key(k)); return k

    def _load_or_make_ca(self):
        key = self._load_or_make_key("ca.key", 2048)
        p = os.path.join(self.dir, "ca.crt")
        if os.path.exists(p):
            return key, x509.load_pem_x509_certificate(open(p, "rb").read())
        name = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "Retro Dial-Up Gateway CA (%s)" % socket.gethostname()[:20]),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Retro Dial-Up Gateway")])
        ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(_serial())
                .not_valid_before(NOT_BEFORE).not_valid_after(NOT_AFTER)
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False),
                               critical=True)
                .add_extension(ski, critical=False))
        cert = _sign_sha1(cert, key)
        self._write("ca.crt", cert.public_bytes(serialization.Encoding.PEM))
        return key, cert

    # ---- public ----
    def ca_der(self):
        return self.ca_cert.public_bytes(serialization.Encoding.DER)

    def ca_pem(self):
        return self.ca_cert.public_bytes(serialization.Encoding.PEM).decode()

    def fingerprint(self):
        h = hashlib.sha1(self.ca_der()).hexdigest().upper()
        return ":".join(h[i:i + 2] for i in range(0, len(h), 2))

    def host_cert_pem(self, host):
        """PEM certificate for `host` (cached), signed by the CA."""
        host = host.strip("[]").lower()
        with self._lock:
            pem = self._cache.get(host)
            if pem: return pem
        try:
            san = x509.IPAddress(ipaddress.ip_address(host))
        except ValueError:
            san = x509.DNSName(host)
        cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host[:64])]))
                .issuer_name(self.ca_cert.subject)
                .public_key(self.host_key.public_key()).serial_number(_serial())
                .not_valid_before(NOT_BEFORE).not_valid_after(NOT_AFTER)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False),
                               critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.SubjectAlternativeName([san]), critical=False))
        cert = _sign_sha1(cert, self.ca_key)
        pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        with self._lock:
            if len(self._cache) > 2000: self._cache.clear()
            self._cache[host] = pem
        return pem
