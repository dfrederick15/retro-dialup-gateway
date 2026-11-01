#!/usr/bin/env python3
"""serctl.py - drive SERSHELL.EXE on the vintage PC over the serial cable.

Use this when the PC is NOT dialed in (the cable carries PPP or this, not
both). Port/baud come from /etc/retrogw/retrogw.conf; override with
SERIAL_PORT=/dev/ttyUSB0 BAUD=115200 in the environment.

Commands run through command.com; files move via XMODEM-1K/CRC, with the
port hand-off to sx/rx done automatically (no HyperTerminal needed).

Usage:
  serctl.py                       interactive shell
  serctl.py "dir c:\\"             run one command
  serctl.py put LOCAL  [WINNAME]  push a file to the PC
  serctl.py get WINNAME [LOCAL]   pull a file from the PC
"""
import serial, sys, time, os, subprocess

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import retrogw_config as C
_cfg = C.load()
PORT, BAUD = _cfg["SERIAL_PORT"], int(_cfg["BAUD"])
MARKER = b"__SERSHELL_DONE rc="

def open_port():
    return serial.Serial(PORT, BAUD, bytesize=8, parity='N', stopbits=1,
                         timeout=0.3, rtscts=False, xonxoff=False)

def read_done(s, extra_timeout=20):
    """read until the DONE marker; return (text_before, rc)."""
    buf = b""; last = time.time()
    while True:
        d = s.read(4096)
        if d:
            buf += d; last = time.time()
            i = buf.find(MARKER)
            if i != -1:
                j = buf.find(b"__", i + len(MARKER))
                rc = buf[i+len(MARKER):j].decode('latin-1','replace') if j!=-1 else "?"
                return buf[:i].rstrip(b"\r\n").decode('latin-1','replace'), rc
        elif time.time() - last > extra_timeout:
            return buf.decode('latin-1','replace') + "\n[timeout: no DONE marker]", "?"

def run(s, cmd):
    s.reset_input_buffer()
    s.write(cmd.encode('latin-1','replace') + b"\n"); s.flush()
    return read_done(s)

def resync(timeout=8):
    """after a file transfer, confirm sershell is back at its prompt."""
    s = open_port()
    s.write(b"\r"); s.flush()
    buf = b""; t = time.time()
    while time.time() - t < timeout:
        d = s.read(256)
        if d:
            buf += d
            if b"C>" in buf: s.close(); return True
    s.close(); return False

def stty_raw():
    subprocess.call(["stty", "-F", PORT, str(BAUD), "cs8", "-cstopb", "-parenb", "raw",
                     "-echo", "-crtscts", "-ixon", "-ixoff", "clocal"])

def xfer(mode, localfile):
    """mode 'send' -> sx (push to win95); 'recv' -> rx (pull from win95)."""
    stty_raw()
    if mode == "send":
        cmd = ["sx", "-k", "-b", localfile]      # 1K blocks, binary
    else:
        cmd = ["rx", "-b", localfile]
    with open(PORT, "rb") as rf, open(PORT, "wb") as wf:
        p = subprocess.run(cmd, stdin=rf, stdout=wf,
                           stderr=subprocess.PIPE, timeout=1200)
    return p.returncode, p.stderr.decode('latin-1','replace')

def do_put(local, winname):
    size = os.path.getsize(local)
    s = open_port(); time.sleep(0.3); s.read(4096)
    s.write((":recv %s %d\n" % (winname, size)).encode()); s.flush()
    s.close()                                     # release port for sx
    time.sleep(0.3)
    rc, err = xfer("send", local)
    print("sx rc=%d %s" % (rc, err.strip().splitlines()[-1] if err.strip() else ""))
    ok = resync()
    print("win95 recv %s (%d bytes -> %s)%s"
          % ("OK" if rc==0 else "FAILED", size, winname, "" if ok else " [resync lost]"))

def do_get(winname, local):
    s = open_port(); time.sleep(0.3); s.read(4096)
    s.write((":send %s\n" % winname).encode()); s.flush()
    # read the "SIZE n" header line sershell prints before sending
    line = b""; t = time.time(); size = None
    while time.time() - t < 10:
        c = s.read(1)
        if not c: continue
        if c == b"\n":
            if line.startswith(b"SIZE"):
                size = int(line.split()[1]); break
            line = b""
        elif c != b"\r":
            line += c
    s.close()
    if size is None:
        print("no SIZE header from win95 (file missing?)"); return
    time.sleep(0.3)
    rc, err = xfer("recv", local)
    print("rx rc=%d %s" % (rc, err.strip().splitlines()[-1] if err.strip() else ""))
    resync()
    if rc == 0 and os.path.exists(local):
        with open(local, "r+b") as f:             # strip XMODEM padding
            f.truncate(size)
        print("pulled %s -> %s (%d bytes)" % (winname, local, size))
    else:
        print("pull FAILED (rx rc=%d)" % rc)

def interactive():
    s = open_port(); time.sleep(0.3); s.read(4096)
    print("Connected. commands run on the PC; 'put L [W]', 'get W [L]', 'exit'.")
    try:
        while True:
            try: line = input("pc> ").strip()
            except EOFError: break
            if not line: continue
            if line in ("exit","quit"): break
            a = line.split()
            if a[0]=="put":
                s.close(); do_put(a[1], a[2] if len(a)>2 else os.path.basename(a[1])); s=open_port()
            elif a[0]=="get":
                s.close(); do_get(a[1], a[2] if len(a)>2 else os.path.basename(a[1])); s=open_port()
            elif line==":quit":
                s.write(b":quit\n"); s.flush(); break
            else:
                out, rc = run(s, line)
                if out: print(out)
                print("[rc=%s]" % rc)
    finally:
        try: s.close()
        except Exception: pass

def main():
    a = sys.argv[1:]
    if not a: interactive(); return
    if a[0]=="put": do_put(a[1], a[2] if len(a)>2 else os.path.basename(a[1])); return
    if a[0]=="get": do_get(a[1], a[2] if len(a)>2 else os.path.basename(a[1])); return
    s = open_port(); time.sleep(0.3); s.read(4096)
    out, rc = run(s, " ".join(a)); s.close()
    print(out); print("[rc=%s]" % rc)

if __name__ == "__main__":
    main()
