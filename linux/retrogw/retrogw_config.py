"""Shared config loader for retro-dialup-gateway.

The config is a plain KEY=VALUE file (also used as a systemd EnvironmentFile):
    /etc/retrogw/retrogw.conf      (override with RETROGW_CONF=/path)
Process environment variables override file values.
"""
import os, re, ipaddress

CONF_PATH = os.environ.get("RETROGW_CONF", "/etc/retrogw/retrogw.conf")

DEFAULTS = {
    "SERIAL_PORT": "/dev/ttyUSB0",   # cable to the vintage PC (prefer /dev/serial/by-id/...)
    "BAUD": "115200",
    "LOCAL_IP": "192.168.95.1",      # this Linux box, on the PPP link
    "REMOTE_IP": "192.168.95.2",     # address handed to the vintage PC
    "DNS1": "1.1.1.1",
    "DNS2": "8.8.8.8",
    "UPLINK": "",                    # internet interface for NAT; empty = default route
    "PPP_UNIT": "95",                # pppd creates interface ppp<UNIT>
    "PROXY_PORT": "8080",
    "PROXY_STRIP_SCRIPTS": "1",
    "PROXY_TLS": "1",                # terminate the old browser's https:// (SSL 3.0 / TLS 1.0)
    "PROXY_TLS_TO_HTTP": "1",        # ...then send it to http:// so plain HTTP crosses the wire
    "PROXY_COMPRESS": "1",           # gzip pages for browsers that accept it
    "PROXY_IMAGE_QUALITY": "40",     # re-encode images as JPEG at this quality (0 = off)
    "PROXY_IMAGE_MAX_WIDTH": "640",  # and shrink wider images to this width (0 = no limit)
    "CONTROL_PORT": "2323",          # SERSHTCP.EXE on the vintage PC
}

BAUDS = ["9600", "19200", "38400", "57600", "115200"]

def load(path=None, env=True):
    cfg = dict(DEFAULTS)
    try:
        with open(path or CONF_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    if env:
        for k in DEFAULTS:
            if k in os.environ:
                cfg[k] = os.environ[k]
    return cfg

def iface(cfg):
    return "ppp" + cfg["PPP_UNIT"]

# ---- validation (values reach root-owned pppd / firewall commands) ----
_DEV = re.compile(r"^/dev/(serial/by-(id|path)/[\w.:+@-]+|tty(USB|ACM|S|AMA|SAC|THS|mxc)\d+|rfcomm\d+)$")
_IFN = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")

def validate(cfg):
    """Return a list of human-readable problems (empty = OK)."""
    err = []
    if not _DEV.match(cfg["SERIAL_PORT"]):
        err.append("Serial port must be a serial device such as /dev/ttyUSB0 or /dev/serial/by-id/...")
    if cfg["BAUD"] not in BAUDS:
        err.append("Baud must be one of " + ", ".join(BAUDS))
    for k in ("LOCAL_IP", "REMOTE_IP", "DNS1", "DNS2"):
        try:
            ipaddress.IPv4Address(cfg[k])
        except ValueError:
            err.append("%s is not an IPv4 address" % k)
    if cfg["LOCAL_IP"] == cfg["REMOTE_IP"]:
        err.append("Local and remote IP must differ")
    if cfg["UPLINK"] and not _IFN.match(cfg["UPLINK"]):
        err.append("Uplink must be an interface name (or empty for automatic)")
    for k, lo, hi in (("PPP_UNIT", 0, 999), ("PROXY_PORT", 1, 65535), ("CONTROL_PORT", 1, 65535)):
        if not cfg[k].isdigit() or not lo <= int(cfg[k]) <= hi:
            err.append("%s must be a number %d-%d" % (k, lo, hi))
    for k in ("PROXY_STRIP_SCRIPTS", "PROXY_TLS", "PROXY_TLS_TO_HTTP", "PROXY_COMPRESS"):
        if cfg[k] not in ("0", "1"):
            err.append("%s must be 0 or 1" % k)
    for k, lo, hi in (("PROXY_IMAGE_QUALITY", 0, 95), ("PROXY_IMAGE_MAX_WIDTH", 0, 4096)):
        if not cfg[k].isdigit() or not lo <= int(cfg[k]) <= hi:
            err.append("%s must be a number %d-%d" % (k, lo, hi))
    return err

HEADER = """\
# retro-dialup-gateway configuration (edit here or in the GUI: Settings menu).
# Read by retrogw-gateway.service and retrogw-proxy.service; restart them
# after changing it:  systemctl restart retrogw-gateway retrogw-proxy
"""

COMMENTS = {
    "SERIAL_PORT": "serial cable to the vintage PC; /dev/serial/by-id/... survives replugging",
    "BAUD": "must match the modem speed set on the vintage PC (115200 recommended)",
    "LOCAL_IP": "this Linux machine's address on the PPP link",
    "REMOTE_IP": "address given to the vintage PC",
    "DNS1": "DNS servers handed to the vintage PC",
    "DNS2": None,
    "UPLINK": "interface to share internet from; empty = the default-route interface",
    "PPP_UNIT": "PPP interface number (interface is ppp<N>)",
    "PROXY_PORT": "retro web proxy port (set the old browser's HTTP proxy to LOCAL_IP:PORT)",
    "PROXY_STRIP_SCRIPTS": "1 = remove <script>/<style> from pages to save bandwidth",
    "PROXY_TLS": "1 = accept https:// from the old browser (SSL 3.0/TLS 1.0, RC4/3DES); install the\n# proxy's certificate on the PC from http://LOCAL_IP:PROXY_PORT/ first",
    "PROXY_TLS_TO_HTTP": "1 = after accepting https://, redirect page loads to http:// so plain HTTP\n# crosses the serial line (the proxy still uses HTTPS to the site)",
    "PROXY_COMPRESS": "1 = gzip text for browsers that send Accept-Encoding: gzip",
    "PROXY_IMAGE_QUALITY": "JPEG quality for re-encoded images, 1-95 (0 = pass images through)",
    "PROXY_IMAGE_MAX_WIDTH": "shrink images wider than this many pixels (0 = no limit)",
    "CONTROL_PORT": "TCP port of SERSHTCP.EXE on the vintage PC",
}

def dumps(cfg):
    out = [HEADER]
    for k in DEFAULTS:
        if COMMENTS.get(k):
            out.append("\n# " + COMMENTS[k])
        out.append("\n%s=%s" % (k, cfg.get(k, DEFAULTS[k])))
    return "".join(out) + "\n"
