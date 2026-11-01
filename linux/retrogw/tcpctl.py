#!/usr/bin/env python3
"""tcpctl.py - control SERSHTCP.EXE on the dialed-in vintage PC over TCP.

  retrogw-ctl                         interactive shell
  retrogw-ctl "dir c:\\"               run one command
  retrogw-ctl put LOCAL [C:\\NAME]     push a file
  retrogw-ctl get C:\\NAME [LOCAL]     pull a file
  options: -H host  -p port            (default REMOTE_IP:CONTROL_PORT from config)
"""
import os, sys, socket, time
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import retrogw_config as C

MARKER = b"__SERSHELL_DONE rc="

class Shell:
    def __init__(self, host, port):
        self.s = socket.create_connection((host, port), timeout=8)
        self.s.settimeout(0.5)
        try:                                    # swallow the banner
            while self.s.recv(4096): pass
        except socket.timeout: pass
        self.s.settimeout(2.0); self.buf = b""

    def _recv(self):
        d = self.s.recv(65536)
        if not d: raise ConnectionError("connection closed by PC")
        self.buf += d

    def _done(self, maxwait=120):
        t = time.time()
        while MARKER not in self.buf or b"__\r\n" not in self.buf[self.buf.find(MARKER) + len(MARKER):]:
            if time.time() - t > maxwait: raise TimeoutError("no reply from PC")
            try: self._recv()
            except socket.timeout: pass
        i = self.buf.find(MARKER); j = self.buf.find(b"__", i + len(MARKER))
        out, rc = self.buf[:i].rstrip(b"\r\n"), self.buf[i + len(MARKER):j].decode()
        out = out.lstrip(b"\r\n")
        if out.startswith(b"C> "): out = out[3:].lstrip(b"\r\n")   # late prompt
        k = self.buf.find(b"\n", j); self.buf = self.buf[k + 1:] if k >= 0 else b""
        return out.decode("latin-1", "replace"), rc

    def cmd(self, c):
        self.buf = b""; self.s.sendall(c.encode("latin-1", "replace") + b"\n")
        return self._done()

    def put(self, local, win):
        data = open(local, "rb").read(); self.buf = b""
        self.s.sendall((":put %s %d\n" % (win, len(data))).encode("latin-1"))
        self.s.sendall(data)
        return self._done()[1], len(data)

    def get(self, win, local):
        self.buf = b""; self.s.sendall((":get %s\n" % win).encode("latin-1"))
        t = time.time()
        while b"SIZE " not in self.buf or b"\n" not in self.buf[self.buf.find(b"SIZE "):]:
            if time.time() - t > 30: raise TimeoutError("no SIZE header")
            try: self._recv()
            except socket.timeout: pass
        line, self.buf = self.buf[self.buf.find(b"SIZE "):].split(b"\n", 1)
        size = int(line.split()[1])
        while len(self.buf) < size:
            try: self._recv()
            except socket.timeout: pass
        data, self.buf = self.buf[:size], self.buf[size:]
        rc = self._done()[1]
        if rc == "0":
            open(local, "wb").write(data)
        return rc, size

    def close(self):
        try: self.s.sendall(b":quit\n")
        except OSError: pass
        self.s.close()

def main(argv):
    cfg = C.load()
    host, port = cfg["REMOTE_IP"], int(cfg["CONTROL_PORT"])
    while argv and argv[0] in ("-H", "-p"):
        if argv[0] == "-H": host = argv[1]
        else: port = int(argv[1])
        argv = argv[2:]
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    try:
        sh = Shell(host, port)
    except OSError as e:
        print("cannot reach SERSHTCP on %s:%d: %s\n(is the PC dialed in and SERSHTCP.EXE running?)"
              % (host, port, e), file=sys.stderr); return 1
    try:
        if not argv:
            print("connected to %s:%d - DOS commands run on the PC; put/get/exit" % (host, port))
            while True:
                try: line = input("pc> ").strip()
                except EOFError: break
                if not line: continue
                if line in ("exit", "quit"): break
                run(sh, line.split()) if line.split()[0] in ("put", "get") else show(*sh.cmd(line))
        else:
            run(sh, argv)
    except (ConnectionError, TimeoutError) as e:
        print("error: %s (if this happens at once, SERSHTCP may be rejecting this host:"
              " see its /allow option)" % e, file=sys.stderr); return 1
    finally:
        sh.close()
    return 0

def show(out, rc):
    if out: print(out)
    print("[rc=%s]" % rc)

def run(sh, a):
    if a[0] == "put" and len(a) >= 2:
        win = a[2] if len(a) > 2 else "C:\\" + os.path.basename(a[1]).upper()[:12]
        rc, n = sh.put(a[1], win); print("put %s -> %s (%d bytes) rc=%s" % (a[1], win, n, rc))
    elif a[0] == "get" and len(a) >= 2:
        local = a[2] if len(a) > 2 else os.path.basename(a[1].replace("\\", "/"))
        rc, n = sh.get(a[1], local); print("get %s -> %s (%d bytes) rc=%s" % (a[1], local, n, rc))
    else:
        show(*sh.cmd(" ".join(a)))

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
