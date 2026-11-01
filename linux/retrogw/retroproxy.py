#!/usr/bin/env python3
"""retroproxy.py - HTTP proxy that lets a vintage browser reach the modern web.

A Win95-era browser speaks HTTP/1.0 and, at best, SSL 3.0/TLS 1.0 with RC4 or
3DES; modern sites require TLS 1.2+ and send heavy pages. This proxy:

  * fetches every page over modern HTTPS when the site has it (verified),
    falling back to plain HTTP, and follows redirects;
  * terminates the old browser's own https:// (CONNECT) with SSL 3.0-TLS 1.2
    using a local CA (install it from http://LOCAL_IP:PROXY_PORT/), then - by default -
    redirects page loads to http:// so plain HTTP crosses the serial line
    instead of slow, CPU-heavy encryption on the vintage PC;
  * rewrites https:// links to http:// so browsing stays in the proxy, strips
    HSTS/CSP, and optionally removes <script>/<style>;
  * shrinks traffic: gzip for browsers that accept it, images re-encoded as
    small baseline JPEGs (which also makes WebP etc. viewable).

Point the old browser's HTTP proxy (and Security/SSL proxy) at
LOCAL_IP:PROXY_PORT (default 192.168.95.1:8080).

Settings: PROXY_* keys in /etc/retrogw/retrogw.conf.
"""
import os, sys, ssl, gzip, zlib, io, re, socket, time, threading, html
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingTCPServer
import urllib.request, urllib.error
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(HERE, "vendor"))     # bundled tlslite-ng, ecdsa, six
import retrogw_config as C

_cfg = C.load()
LISTEN = os.environ.get("LISTEN", "0.0.0.0")
PORT = int(_cfg["PROXY_PORT"])
STRIP_SCRIPTS = _cfg["PROXY_STRIP_SCRIPTS"] != "0"
TLS_ON = _cfg["PROXY_TLS"] != "0"
TLS_TO_HTTP = _cfg["PROXY_TLS_TO_HTTP"] != "0"
COMPRESS = _cfg["PROXY_COMPRESS"] != "0"
IMG_Q = int(_cfg["PROXY_IMAGE_QUALITY"])
IMG_W = int(_cfg["PROXY_IMAGE_MAX_WIDTH"])
STATE = os.environ.get("STATE_DIRECTORY") or os.path.expanduser("~/.local/state/retrogw-proxy")
UA = os.environ.get("UA",
    "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0")
SELF_HOSTS = {"retrogw", "retrogw.local", "retrogw.proxy"}
SELF_ADDRS = {"%s:%d" % (h, PORT) for h in (_cfg["LOCAL_IP"], "127.0.0.1", "localhost")}
INFO_URL = "http://%s:%d/" % (_cfg["LOCAL_IP"], PORT)

def log(msg):
    sys.stderr.write(msg + "\n")

# ---- optional pieces: report what's missing instead of failing ----
try:
    from PIL import Image
except ImportError:
    Image = None
    if IMG_Q: log("[proxy] Pillow not installed: images pass through unchanged")
CA = TLS = None
if TLS_ON:
    try:
        import retrogw_ca
        from tlslite.api import (TLSConnection, HandshakeSettings, X509, X509CertChain,
                                 parsePEMKey)
        CA = retrogw_ca.CA(STATE)
        TLS = HandshakeSettings()
        TLS.minVersion, TLS.maxVersion = (3, 0), (3, 3)     # SSL 3.0 .. TLS 1.2
        TLS.cipherNames = ["aes128", "aes256", "rc4", "3des",   # RC4 is cheapest for a 486
                           "rc4export"]   # 56-bit, for export-grade Netscape 4.x (last resort)
        TLS.macNames = ["sha", "sha256", "md5"]             # md5 for RC4-MD5
        TLS.keyExchangeNames = ["rsa"]                      # what 9x clients use
        TLS.useEncryptThenMAC = False
        HOST_KEY = parsePEMKey(CA.host_key_pem, private=True)
    except Exception as e:
        log("[proxy] HTTPS termination disabled: %s" % e); CA = TLS = None

# ---- upstream (modern) side ----
CTX_VERIFY = ssl.create_default_context()
CTX_LAX = ssl._create_unverified_context()
_https_ok = {}                       # host -> (bool, time): remember who has HTTPS
_https_lock = threading.Lock()

STRIP_HEADERS = {"strict-transport-security", "content-security-policy",
    "content-security-policy-report-only", "public-key-pins", "expect-ct",
    "content-encoding", "transfer-encoding", "connection", "upgrade",
    "alt-svc", "set-cookie", "content-length", "content-type", "keep-alive"}
TEXT_TYPES = ("text/", "application/javascript", "application/x-javascript",
              "application/json", "application/xml", "application/xhtml")

def transcode_html(body, charset):
    try:
        txt = body.decode(charset or "utf-8", "replace")
    except (LookupError, TypeError):
        txt = body.decode("utf-8", "replace")
    # keep links inside the proxy: downgrade scheme, browser re-requests via us
    txt = txt.replace("https://", "http://").replace("HTTPS://", "http://")
    txt = re.sub(r'(src|href|action)\s*=\s*(["\'])//', r'\1=\2http://', txt, flags=re.I)
    if STRIP_SCRIPTS:
        txt = re.sub(r'<script\b.*?</script>', '', txt, flags=re.I | re.S)
        txt = re.sub(r'<style\b.*?</style>', '', txt, flags=re.I | re.S)
        txt = re.sub(r'<link\b[^>]*stylesheet[^>]*>', '', txt, flags=re.I)
    # neutralize meta-refresh to https and CSP metas
    txt = re.sub(r'<meta\b[^>]*http-equiv=["\']?(refresh|content-security-policy)[^>]*>',
                 '', txt, flags=re.I)
    return txt.encode("utf-8", "replace")

def decompress(data, enc):
    enc = (enc or "").lower()
    try:
        if enc == "gzip":    return gzip.decompress(data)
        if enc == "deflate":
            try: return zlib.decompress(data)
            except zlib.error: return zlib.decompress(data, -15)
    except Exception:
        pass
    return data

def shrink_image(data, ctype):
    """Re-encode as a small baseline JPEG. Returns (data, ctype, changed)."""
    if not (Image and IMG_Q) or "svg" in ctype or "icon" in ctype:
        return data, ctype, False
    native = any(t in ctype for t in ("jpeg", "jpg", "gif", "png"))
    if native and len(data) < 1500:                 # tiny: not worth it
        return data, ctype, False
    try:
        im = Image.open(io.BytesIO(data))
        if getattr(im, "n_frames", 1) > 1 and native:   # keep animations
            return data, ctype, False
        im.load()
        if IMG_W and im.width > IMG_W:
            im = im.resize((IMG_W, max(1, im.height * IMG_W // im.width)), Image.LANCZOS)
        if im.mode in ("RGBA", "LA", "P", "PA"):
            im = im.convert("RGBA"); bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.getchannel("A")); im = bg
        elif im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, "JPEG", quality=IMG_Q, optimize=True, progressive=False)
        out = out.getvalue()
    except Exception:
        return data, ctype, False
    if native and len(out) >= len(data) * 0.9:
        return data, ctype, False
    return out, "image/jpeg", True

def _open(method, url, body, ctx, timeout=25):
    headers = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en",
               "Accept-Encoding": "gzip, deflate"}
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    try:
        return opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return e                                   # serve the error page too

def fetch(method, url, body, secure_only=False):
    """Fetch, preferring HTTPS. Returns (response, final_url, used_https)."""
    m = re.match(r"(https?)://([^/:?#]+)(.*)", url, re.I)
    if not m:
        raise ValueError("bad URL")
    host, rest = m.group(2).lower(), m.group(3)
    https_url, http_url = "https://%s%s" % (host, rest), "http://%s%s" % (host, rest)
    with _https_lock:
        known = _https_ok.get(host)
    if known and time.time() - known[1] > 3600: known = None
    attempts = []
    if known is None or known[0]:
        attempts.append((https_url, CTX_VERIFY, 12))       # real HTTPS, verified
    if not secure_only:
        attempts.append((http_url, None, 25))              # plain HTTP
    attempts.append((https_url, CTX_LAX, 20))              # HTTPS with a bad cert
    err = None
    for u, ctx, to in attempts:
        try:
            r = _open(method, u, body, ctx or CTX_LAX, to)
            if ctx is CTX_VERIFY:
                with _https_lock: _https_ok[host] = (True, time.time())
            return r, r.geturl() if hasattr(r, "geturl") else u, u.startswith("https")
        except Exception as e:
            err = e
            if ctx is CTX_VERIFY:
                with _https_lock: _https_ok[host] = (False, time.time())
    raise err

INFO_PAGE = """<html><head><title>Retro Dial-Up Gateway proxy</title></head>
<body bgcolor="#ffffff"><h2>Retro Dial-Up Gateway proxy</h2>
<p>You are browsing through the gateway's web proxy.</p>
<h3>HTTPS (secure sites)</h3>
%s
<h3>Settings</h3><ul>
<li>Strip scripts and styles: <b>%s</b></li>
<li>Compression (gzip): <b>%s</b></li>
<li>Image re-encoding: <b>%s</b></li></ul>
<p>Change these in the gateway's control panel (Settings menu).</p>
</body></html>"""

def info_html():
    if CA:
        tls = ("<p>The proxy can open https:// sites for this PC. First install its "
               "certificate: <a href=\"/ca.crt\"><b>download the certificate</b></a>, then "
               "choose to trust it for identifying web sites.</p>"
               "<p>Fingerprint (SHA-1): <tt>%s</tt></p>"
               "<p>In the browser's proxy settings, set the <b>Security</b> (SSL) proxy to "
               "the same address and port as the HTTP proxy.</p>%s"
               % (CA.fingerprint(), "<p>Secure pages are switched to http:// after the "
                  "handshake, so the serial line carries plain HTTP; the proxy still uses "
                  "HTTPS to the site.</p>" if TLS_TO_HTTP else ""))
    else:
        tls = "<p>HTTPS termination is off. Use http:// addresses; the proxy upgrades them.</p>"
    img = ("JPEG quality %d, max width %s" % (IMG_Q, "%d px" % IMG_W if IMG_W else "unlimited")
           if IMG_Q and Image else "off")
    return (INFO_PAGE % (tls, "on" if STRIP_SCRIPTS else "off",
                         "on" if COMPRESS else "off", img)).encode()

# names for the suites 1990s browsers offer (for handshake-failure logs)
SUITES = {0x0001: "NULL-MD5", 0x0002: "NULL-SHA", 0x0003: "EXP-RC4-MD5", 0x0004: "RC4-MD5",
          0x0005: "RC4-SHA", 0x0006: "EXP-RC2-MD5", 0x0008: "EXP-DES-SHA", 0x0009: "DES-SHA",
          0x000a: "3DES-SHA", 0x0062: "EXP1024-DES-SHA", 0x0064: "EXP1024-RC4-SHA",
          0x002f: "AES128-SHA", 0x003c: "AES128-SHA256", 0x003d: "AES256-SHA256",
          0x009c: "AES128-GCM-SHA256", 0x009d: "AES256-GCM-SHA384", 0x0035: "AES256-SHA", 0xfeff: "FIPS-3DES-SHA",
          0xfefe: "FIPS-DES-SHA", 0xffe0: "FIPS-RC4-SHA", 0xffe1: "FIPS-DES-SHA(old)",
          0x010080: "SSL2-RC4-MD5", 0x020080: "SSL2-EXP-RC4-MD5", 0x030080: "SSL2-RC2-MD5",
          0x040080: "SSL2-EXP-RC2-MD5", 0x060040: "SSL2-DES-MD5", 0x0700c0: "SSL2-3DES-MD5"}

def offered_suites(hello):
    """Cipher suites in a ClientHello (SSLv2 or SSLv3/TLS record format)."""
    try:
        if hello[0] & 0x80 and hello[2] == 1:            # SSLv2-format hello
            cl = int.from_bytes(hello[5:7], "big"); specs = hello[11:11 + cl]
            ids = [int.from_bytes(specs[i:i + 3], "big") for i in range(0, len(specs), 3)]
            ver = "SSLv2 hello for %d.%d" % (hello[3], hello[4])
        elif hello[0] == 0x16:                           # TLS record, ClientHello
            b = hello[5:]; ver = "hello for %d.%d" % (b[4], b[5]); i = 4 + 2 + 32
            i += 1 + b[i]; n = int.from_bytes(b[i:i + 2], "big")
            ids = [int.from_bytes(b[i + 2 + j:i + 4 + j], "big") for j in range(0, n, 2)]
        else:
            return "not an SSL/TLS hello"
        return "%s offering %s" % (ver, ", ".join(SUITES.get(x, "0x%04x" % x) for x in ids))
    except Exception:
        return "unparseable hello"

# ---- the proxy ----
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "retroproxy/2"
    tls_origin = None            # "host[:port]" when serving inside a terminated CONNECT
    timeout = 180                # drop clients idle this long (seconds)

    def log_message(self, fmt, *a):
        log("[proxy] %s - %s" % (self.client_address[0], fmt % a))
    def log_request(self, code="-", size="-"): pass
    def log_error(self, fmt, *a): pass

    # One machine-readable line per request (the control panel parses these):
    #   [proxy] REQ <client> <method> <status> in=<bytes> out=<bytes> ms=<n> flags=<f,...> <url> [(note)]
    # in = size as fetched (decompressed); out = bytes sent to the old PC.
    # flags: https (fetched over HTTPS), tls (inside a terminated SSL/TLS
    # connection), gz (gzip to the PC), img (image re-encoded), local, redir.
    def _log(self, method, code, url, nin=0, nout=0, flags=(), note=None):
        f = list(flags) + (["tls"] if self.tls_origin else [])
        log("[proxy] REQ %s %s %s in=%d out=%d ms=%d flags=%s %s%s" % (
            self.client_address[0], method, code, nin, nout,
            (time.time() - self._t0) * 1000, ",".join(f) or "-", url or "-",
            " (%s)" % note if note else ""))

    def _target(self):
        p = self.path
        if re.match(r"https?://", p, re.I):
            return p
        if self.tls_origin:
            host = self.tls_origin[:-4] if self.tls_origin.endswith(":443") else self.tls_origin
            return "https://%s%s" % (host, p)
        return None                        # a direct (non-proxy) request to us

    def _reply(self, code, ctype, data, extra=(), reason=None):
        self.send_response(code, reason)
        for k, v in extra: self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(data)

    def _local(self, method, path):
        if path.split("?")[0].lower() in ("/ca.crt", "/ca.cer", "/cacert.crt") and CA:
            data = CA.ca_der()
            self._reply(200, "application/x-x509-ca-cert", data,
                        [("Content-Disposition", "inline; filename=retrogw-ca.crt")])
        else:
            data = info_html(); self._reply(200, "text/html", data)
        self._log(method, 200, "http://retrogw" + path, 0, len(data), ["local"])

    def _serve(self, method):
        self._t0 = time.time()
        url = self._target()
        if not url:
            return self._local(method, self.path)
        m = re.match(r"https?://([^/:?#]+)(:\d+)?", url, re.I)
        if m and (m.group(1).lower() in SELF_HOSTS
                  or (m.group(1) + (m.group(2) or ":80")).lower() in SELF_ADDRS):
            return self._local(method, url[m.end():].split("#")[0] or "/")
        # Terminated https:// page load: bounce to http:// so plain HTTP flows
        # over the serial line (POST stays inside TLS so form data isn't lost).
        if self.tls_origin and TLS_TO_HTTP and method in ("GET", "HEAD"):
            loc = "http://" + url[len("https://"):]
            body = ('<a href="%s">%s</a>' % (html.escape(loc), html.escape(loc))).encode()
            self._reply(302, "text/html", body, [("Location", loc)])
            return self._log(method, 302, url, 0, len(body), ["redir"])

        clen = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(clen) if clen else None
        try:
            resp, final, used_https = fetch(method, url, body, secure_only=bool(self.tls_origin))
        except Exception as e:
            msg = ("<html><body><h2>Can't reach %s</h2><p>%s</p></body></html>"
                   % (html.escape(url), html.escape(str(e)))).encode()
            self._reply(502, "text/html", msg, reason="Bad Gateway")
            return self._log(method, 502, url, 0, len(msg), note=str(e))

        data = resp.read()
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
        data = decompress(data, hdrs.get("content-encoding"))
        nin = len(data); flags = ["https"] if used_https else []
        ctype = hdrs.get("content-type", "application/octet-stream")
        lct = ctype.lower()
        if "text/html" in lct:
            mm = re.search(r"charset=([\w-]+)", ctype, re.I)
            data = transcode_html(data, mm.group(1) if mm else None)
            ctype = "text/html; charset=utf-8"
        elif lct.startswith("image/"):
            data, ctype, changed = shrink_image(data, lct)
            if changed: flags.append("img")
        extra = []
        for k, v in hdrs.items():
            if k in STRIP_HEADERS: continue
            if k == "location": v = re.sub(r"^https://", "http://", v, flags=re.I)
            extra.append((k, v))
        if (COMPRESS and len(data) > 512 and lct.startswith(TEXT_TYPES)
                and "gzip" in self.headers.get("Accept-Encoding", "").lower()):
            z = gzip.compress(data, 9)
            if len(z) < len(data):
                data = z; extra.append(("Content-Encoding", "gzip")); flags.append("gz")
        code = getattr(resp, "status", None) or getattr(resp, "code", 200)
        try:
            self._reply(code, ctype, data, extra)
        except OSError as e:
            return self._log(method, code, url, nin, 0, flags, "client gone: %s" % e)
        self._log(method, code, url, nin, 0 if method == "HEAD" else len(data), flags)

    def do_GET(self):  self._serve("GET")
    def do_POST(self): self._serve("POST")
    def do_HEAD(self): self._serve("HEAD")

    def do_CONNECT(self):
        self._t0 = time.time()
        host, _, port = self.path.rpartition(":")
        if not (CA and TLS) or self.tls_origin or not host or port not in ("443", "8443", "563"):
            why = ("https termination is off; use http:// URLs" if not CA
                   else "only https ports are supported")
            self.send_error(501, why)
            return self._log("CONNECT", 501, self.path, note=why)
        self.send_response(200, "Connection established"); self.end_headers()
        self.wfile.flush()
        try:
            self.connection.settimeout(30)
            hello = self.connection.recv(2048, socket.MSG_PEEK)
        except OSError:
            hello = b""
        conn = TLSConnection(self.connection)
        try:
            chain = X509CertChain([X509().parse(CA.host_cert_pem(host))])
            conn.handshakeServer(certChain=chain, privateKey=HOST_KEY, settings=TLS)
        except Exception as e:
            self.close_connection = True
            return self._log("CONNECT", 525, self.path,
                             note="SSL/TLS handshake failed: %s; %s" % (
                                 str(e) or type(e).__name__, offered_suites(hello)))
        ver = {(3, 0): "SSL 3.0", (3, 1): "TLS 1.0", (3, 2): "TLS 1.1", (3, 3): "TLS 1.2"}.get(
            conn.version, str(conn.version))
        self._log("CONNECT", 200, self.path, flags=["tls"],
                  note="%s %s" % (ver, SUITES.get(conn.session.cipherSuite, conn.getCipherName())))
        try:
            inner = TLSHandler.__new__(TLSHandler)
            inner.tls_origin = self.path
            TLSHandler.__init__(inner, conn, self.client_address, self.server)
        except Exception:
            pass
        finally:
            try: conn.close()
            except Exception: pass
        self.close_connection = True

class TLSHandler(Handler):
    """Serves HTTP requests that arrive inside a terminated CONNECT."""
    def finish(self):
        try: self.wfile.flush()
        except Exception: pass

class Server(ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

if __name__ == "__main__":
    srv = Server((LISTEN, PORT), Handler)
    print("retroproxy on %s:%d (strip_scripts=%s https=%s compress=%s images=%s)" % (
        LISTEN, PORT, STRIP_SCRIPTS,
        ("terminate" + ("->http" if TLS_TO_HTTP else "")) if CA else "off",
        COMPRESS, ("q%d/%dpx" % (IMG_Q, IMG_W)) if IMG_Q and Image else "off"), flush=True)
    if CA:
        print("CA certificate SHA-1 %s (download: %sca.crt)" % (CA.fingerprint(), INFO_URL),
              flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
