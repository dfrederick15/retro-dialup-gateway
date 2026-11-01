#!/usr/bin/env python3
"""retroproxy.py - HTTP proxy that lets a vintage browser reach the modern web.

A Win95/Netscape client speaks plain HTTP/1.0 and old SSL; modern sites force
TLS 1.2+ and heavy HTML. This proxy accepts the old browser's HTTP proxy
requests, fetches the target with modern TLS (upgrading http->https and
following redirects), strips HSTS/CSP, rewrites https:// links back to http://
so navigation stays in the proxy, and optionally removes <script>/<style> to
keep pages light over the 115200 serial link.

Point the old browser's HTTP proxy at  <LOCAL_IP>:<PROXY_PORT>  (default
192.168.95.1:8080) and browse http:// URLs.

Settings: PROXY_PORT, PROXY_STRIP_SCRIPTS in /etc/retrogw/retrogw.conf.
"""
import os, sys, ssl, gzip, zlib, io, re, socket
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingTCPServer
import urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retrogw_config as C

_cfg = C.load()
LISTEN = os.environ.get("LISTEN", "0.0.0.0")
PORT   = int(_cfg["PROXY_PORT"])
STRIP_SCRIPTS = _cfg["PROXY_STRIP_SCRIPTS"] != "0"
UA = os.environ.get("UA",
    "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0")

CTX = ssl._create_unverified_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

STRIP_HEADERS = {"strict-transport-security", "content-security-policy",
    "content-security-policy-report-only", "public-key-pins", "expect-ct",
    "content-encoding", "transfer-encoding", "connection", "upgrade",
    "alt-svc", "set-cookie"}  # cookies dropped: keeps it simple & stateless-ish

def transcode_html(body, charset):
    try:
        txt = body.decode(charset or "utf-8", "replace")
    except (LookupError, TypeError):
        txt = body.decode("utf-8", "replace")
    # keep links inside the proxy: downgrade scheme, browser re-requests via us
    txt = txt.replace("https://", "http://").replace("HTTPS://", "http://")
    txt = re.sub(r'(src|href)\s*=\s*(["\'])//', r'\1=\2http://', txt, flags=re.I)
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
        if enc == "deflate": return zlib.decompress(data)
    except Exception:
        pass
    return data

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "retroproxy/1"

    def log_message(self, fmt, *a):
        sys.stderr.write("[proxy] %s - %s\n" % (self.address_string(), fmt % a))

    def _fetch(self, method, url, body=None):
        headers = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en"}
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        return urllib.request.urlopen(req, timeout=25, context=CTX)

    def _target(self):
        p = self.path
        if p.startswith("http://") or p.startswith("https://"):
            return p
        host = self.headers.get("Host")
        return "http://%s%s" % (host, p) if host else None

    def _serve(self, method):
        url = self._target()
        if not url:
            self.send_error(400, "no absolute URL"); return
        clen = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(clen) if clen else None
        # try as-requested; if plain http is refused, retry https
        attempts = [url]
        if url.startswith("http://"):
            attempts.append("https://" + url[len("http://"):])
        resp = None; err = None
        for u in attempts:
            try:
                resp = self._fetch(method, u, body); break
            except urllib.error.HTTPError as e:
                resp = e; break                      # serve the error body too
            except Exception as e:
                err = e; continue
        if resp is None:
            self.send_error(502, "fetch failed: %s" % err); return

        data = resp.read()
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
        data = decompress(data, hdrs.get("content-encoding"))
        ctype = hdrs.get("content-type", "application/octet-stream")
        if "text/html" in ctype.lower():
            m = re.search(r"charset=([\w-]+)", ctype, re.I)
            data = transcode_html(data, m.group(1) if m else None)
            ctype = "text/html; charset=utf-8"
        code = getattr(resp, "status", None) or getattr(resp, "code", 200)
        self.send_response(code)
        for k, v in hdrs.items():
            if k in STRIP_HEADERS or k == "content-length" or k == "content-type":
                continue
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(data)

    def do_GET(self):  self._serve("GET")
    def do_POST(self): self._serve("POST")
    def do_HEAD(self): self._serve("HEAD")
    def do_CONNECT(self):
        # Old browser can't do modern TLS inside a tunnel; tell it to use http://
        self.send_error(501, "Use http:// URLs; this proxy upgrades to TLS for you")

class Server(ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

if __name__ == "__main__":
    srv = Server((LISTEN, PORT), Handler)
    print("retroproxy on %s:%d (strip_scripts=%s)" % (LISTEN, PORT, STRIP_SCRIPTS),
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
