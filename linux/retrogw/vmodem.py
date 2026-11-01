#!/usr/bin/env python3
"""vmodem.py - fake dial-up modem + PPP answering peer on a serial port.

The vintage PC "dials" with AT commands (Dial-Up Networking). We answer
OK to setup commands, reply CONNECT to ATD, then run pppd as the answering
peer on the same line. When pppd exits (hang-up), we re-arm and wait again.

Settings come from /etc/retrogw/retrogw.conf (see retrogw_config.py).
Runs as root (pppd needs it), normally via retrogw-gateway.service.
"""
import os, sys, time, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retrogw_config as C

try:
    import serial
except ImportError:
    sys.exit("pyserial missing: install python3-serial / python3-pyserial")

cfg = C.load()
problems = C.validate(cfg)
if problems:
    for p in problems: print("[vmodem] config error: " + p, flush=True)
    sys.exit(2)

PORT, BAUD = cfg["SERIAL_PORT"], int(cfg["BAUD"])
PPPD = next((p for p in ("/usr/sbin/pppd", "/sbin/pppd", "/usr/bin/pppd") if os.path.exists(p)), "pppd")

def log(msg): print("[vmodem] " + msg, flush=True)

def stty_modem():
    # raw, no flow control, and -hupcl so closing the fd does NOT drop DTR
    # (keeps the client's carrier-detect asserted across the hand-off to pppd).
    subprocess.call(["stty", "-F", PORT, str(BAUD), "cs8", "-cstopb", "-parenb", "raw",
                     "-echo", "-crtscts", "-ixon", "-ixoff", "clocal", "-hupcl"])

def pppd_args():
    return [PPPD, PORT, str(BAUD),
            "%s:%s" % (cfg["LOCAL_IP"], cfg["REMOTE_IP"]),
            "unit", cfg["PPP_UNIT"],
            "noauth", "local", "nodetach", "nocrtscts", "lock",
            "nodefaultroute", "noipv6", "asyncmap", "0",
            "ms-dns", cfg["DNS1"], "ms-dns", cfg["DNS2"],
            "lcp-echo-interval", "30", "lcp-echo-failure", "4"]

def wait_for_port():
    warned = False
    while not os.path.exists(PORT):
        if not warned:
            log("waiting for serial port %s to appear ..." % PORT); warned = True
        time.sleep(2)

def answer_loop():
    while True:
        wait_for_port()
        stty_modem()
        try:
            s = serial.Serial(PORT, BAUD, timeout=0.5)
        except serial.SerialException as e:
            log("cannot open %s: %s" % (PORT, e)); time.sleep(5); continue
        log("waiting for dial on %s @%d ..." % (PORT, BAUD))
        line = b""; dialed = False
        try:
            while not dialed:
                c = s.read(1)
                if not c:
                    if not os.path.exists(PORT): raise serial.SerialException("unplugged")
                    continue
                if c in (b"\r", b"\n"):
                    cmd = line.strip().upper().lstrip(b"+"); line = b""   # "+++ATH" = escape + hang up
                    if not cmd: continue
                    log("<- %s" % cmd.decode("latin-1"))
                    if cmd.startswith(b"ATD"):                 # dialing
                        s.write(b"\r\nCONNECT %d\r\n" % BAUD); s.flush()
                        log("-> CONNECT %d, starting PPP" % BAUD); dialed = True
                    elif cmd.startswith(b"ATH"):
                        s.write(b"\r\nOK\r\n"); s.flush()
                    elif cmd.startswith(b"AT"):                # any other setup cmd
                        s.write(b"\r\nOK\r\n"); s.flush()
                else:
                    line += c
                    if len(line) > 200: line = b""
        except serial.SerialException as e:
            log("serial error: %s" % e); s.close(); time.sleep(2); continue
        time.sleep(0.2); s.close()
        rc = subprocess.call(pppd_args())
        log("hang-up (pppd exited rc=%d); re-arming" % rc)
        time.sleep(1.0)

if __name__ == "__main__":
    try:
        answer_loop()
    except KeyboardInterrupt:
        log("stopped")
