#!/usr/bin/env python3
"""retrogw_gui.py - control panel for retro-dialup-gateway.

Start/stop the dial-in gateway and retro web proxy, watch dialing and link
statistics live, edit settings, and control the dialed-in PC (commands and
file transfer through SERSHTCP.EXE) at the same time as it browses.
"""
import os, sys, time, subprocess, glob
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import retrogw_config as C
import tcpctl
from PyQt5 import QtWidgets, QtCore, QtGui

VERSION = "1.0.0"
GW_UNIT, PX_UNIT = "retrogw-gateway.service", "retrogw-proxy.service"
REPO_URL = "https://github.com/dfrederick15/retro-dialup-gateway"

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

def hsize(n):
    for u in ("B", "K", "M", "G"):
        if n < 1024: return "%d%s" % (n, u)
        n //= 1024
    return "%dT" % n

# ---------- control-channel worker (own thread; talks to SERSHTCP.EXE) ----------
class Tcp(QtCore.QObject):
    out   = QtCore.pyqtSignal(str)
    done  = QtCore.pyqtSignal(str)
    state = QtCore.pyqtSignal(bool)

    def __init__(self):
        super().__init__(); self.sh = None

    @QtCore.pyqtSlot(str, int)
    def open_conn(self, host, port):
        try:
            self.sh = tcpctl.Shell(host, port)
            self.state.emit(True); self.out.emit("[connected to %s:%d]\n" % (host, port))
        except Exception as e:
            self.sh = None; self.state.emit(False)
            self.out.emit("[connect failed: %s - is the PC dialed in and SERSHTCP.EXE running?]\n" % e)

    @QtCore.pyqtSlot()
    def close_conn(self):
        if self.sh: self.sh.close()
        self.sh = None; self.state.emit(False); self.out.emit("[disconnected]\n")

    def _guard(self, fn):
        if not self.sh: self.out.emit("[not connected]\n"); return
        try: fn()
        except Exception as e:
            self.out.emit("[error: %s]\n" % e); self.close_conn()

    @QtCore.pyqtSlot(str)
    def command(self, cmd):
        def f():
            o, rc = self.sh.cmd(cmd)
            if o: self.out.emit(o + "\n")
            self.done.emit(rc)
        self._guard(f)

    @QtCore.pyqtSlot(str, str)
    def put(self, local, win):
        def f():
            rc, n = self.sh.put(local, win)
            self.out.emit("[pushed %s -> %s, %d bytes]\n" % (local, win, n)); self.done.emit(rc)
        self._guard(f)

    @QtCore.pyqtSlot(str, str)
    def get(self, win, local):
        def f():
            rc, n = self.sh.get(win, local)
            self.out.emit("[pulled %s -> %s, %d bytes]\n" % (win, local, n)); self.done.emit(rc)
        self._guard(f)

# ---------- settings dialog ----------
class Settings(QtWidgets.QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings"); self.cfg = dict(cfg)
        v = QtWidgets.QVBoxLayout(self)

        # serial
        g1 = QtWidgets.QGroupBox("Serial line"); f1 = QtWidgets.QFormLayout(g1); v.addWidget(g1)
        self.port = QtWidgets.QComboBox(); self.port.setEditable(True)
        self._fill_ports(); self.port.setCurrentText(cfg["SERIAL_PORT"])
        rescan = QtWidgets.QPushButton("Rescan"); rescan.clicked.connect(self._rescan)
        row = QtWidgets.QHBoxLayout(); row.addWidget(self.port, 1); row.addWidget(rescan)
        f1.addRow("Port:", row)
        self.baud = QtWidgets.QComboBox(); self.baud.addItems(C.BAUDS)
        self.baud.setCurrentText(cfg["BAUD"]); f1.addRow("Speed (bps):", self.baud)
        hint = QtWidgets.QLabel("Use a /dev/serial/by-id/… path so the port survives replugging. "
                                "Set the same speed on the vintage PC's modem.")
        hint.setWordWrap(True); hint.setStyleSheet("color:gray;"); f1.addRow(hint)

        # network
        g2 = QtWidgets.QGroupBox("Network"); f2 = QtWidgets.QFormLayout(g2); v.addWidget(g2)
        self.local = QtWidgets.QLineEdit(cfg["LOCAL_IP"]); f2.addRow("This machine (gateway) IP:", self.local)
        self.remote = QtWidgets.QLineEdit(cfg["REMOTE_IP"]); f2.addRow("Vintage PC IP:", self.remote)
        self.dns1 = QtWidgets.QLineEdit(cfg["DNS1"]); f2.addRow("DNS 1:", self.dns1)
        self.dns2 = QtWidgets.QLineEdit(cfg["DNS2"]); f2.addRow("DNS 2:", self.dns2)
        self.uplink = QtWidgets.QComboBox(); self.uplink.setEditable(True)
        self.uplink.addItem("(automatic: default route)", "")
        for n in sorted(os.listdir("/sys/class/net")):
            if n != "lo" and not n.startswith("ppp"): self.uplink.addItem(n, n)
        i = self.uplink.findData(cfg["UPLINK"])
        if i >= 0: self.uplink.setCurrentIndex(i)
        else: self.uplink.setCurrentText(cfg["UPLINK"])
        f2.addRow("Share internet from:", self.uplink)
        self.unit = QtWidgets.QSpinBox(); self.unit.setRange(0, 999); self.unit.setValue(int(cfg["PPP_UNIT"]))
        self.unit.setPrefix("ppp"); f2.addRow("PPP interface:", self.unit)

        # services
        g3 = QtWidgets.QGroupBox("Services"); f3 = QtWidgets.QFormLayout(g3); v.addWidget(g3)
        self.pport = QtWidgets.QSpinBox(); self.pport.setRange(1, 65535); self.pport.setValue(int(cfg["PROXY_PORT"]))
        f3.addRow("Web proxy port:", self.pport)
        self.strip = QtWidgets.QCheckBox("Strip scripts and styles from pages (faster over serial)")
        self.strip.setChecked(cfg["PROXY_STRIP_SCRIPTS"] == "1"); f3.addRow(self.strip)
        self.cport = QtWidgets.QSpinBox(); self.cport.setRange(1, 65535); self.cport.setValue(int(cfg["CONTROL_PORT"]))
        f3.addRow("SERSHTCP control port:", self.cport)

        self.restart = QtWidgets.QCheckBox("Restart running services to apply (drops an active call)")
        self.restart.setChecked(True); v.addWidget(self.restart)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
                                        | QtWidgets.QDialogButtonBox.RestoreDefaults)
        bb.accepted.connect(self._save); bb.rejected.connect(self.reject)
        bb.button(QtWidgets.QDialogButtonBox.RestoreDefaults).clicked.connect(self._defaults)
        v.addWidget(bb)

    def _fill_ports(self):
        self.port.clear()
        for p in sorted(glob.glob("/dev/serial/by-id/*")):
            self.port.addItem("%s  →  %s" % (p, os.path.basename(os.path.realpath(p))), p)
        for pat in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyS[0-3]"):
            for p in sorted(glob.glob(pat)): self.port.addItem(p, p)

    def _rescan(self):
        cur = self.value_port(); self._fill_ports(); self._set_port(cur)

    def _set_port(self, p):
        i = self.port.findData(p)
        if i >= 0: self.port.setCurrentIndex(i)
        else: self.port.setCurrentText(p)

    def value_port(self):
        t = self.port.currentText().strip()
        d = self.port.currentData()
        return d if d and t.startswith(d) else t.split("  →")[0].strip()

    def _defaults(self):
        d = C.DEFAULTS
        self._set_port(d["SERIAL_PORT"]); self.baud.setCurrentText(d["BAUD"])
        self.local.setText(d["LOCAL_IP"]); self.remote.setText(d["REMOTE_IP"])
        self.dns1.setText(d["DNS1"]); self.dns2.setText(d["DNS2"]); self.uplink.setCurrentIndex(0)
        self.unit.setValue(int(d["PPP_UNIT"])); self.pport.setValue(int(d["PROXY_PORT"]))
        self.strip.setChecked(True); self.cport.setValue(int(d["CONTROL_PORT"]))

    def _save(self):
        up = self.uplink.currentData() if self.uplink.currentIndex() >= 0 and \
            self.uplink.currentText() == self.uplink.itemText(self.uplink.currentIndex()) else self.uplink.currentText().strip()
        new = dict(self.cfg, SERIAL_PORT=self.value_port(), BAUD=self.baud.currentText(),
                   LOCAL_IP=self.local.text().strip(), REMOTE_IP=self.remote.text().strip(),
                   DNS1=self.dns1.text().strip(), DNS2=self.dns2.text().strip(), UPLINK=up or "",
                   PPP_UNIT=str(self.unit.value()), PROXY_PORT=str(self.pport.value()),
                   PROXY_STRIP_SCRIPTS="1" if self.strip.isChecked() else "0",
                   CONTROL_PORT=str(self.cport.value()))
        errs = C.validate(new)
        if errs:
            QtWidgets.QMessageBox.warning(self, "Settings", "\n".join(errs)); return
        text = C.dumps(new)
        try:
            os.makedirs(os.path.dirname(C.CONF_PATH), exist_ok=True)
            with open(C.CONF_PATH, "w") as f: f.write(text)
        except PermissionError:
            # not in the retrogw group yet (log out/in after install) - ask polkit
            r = subprocess.run(["pkexec", "tee", C.CONF_PATH], input=text, capture_output=True, text=True)
            if r.returncode != 0:
                QtWidgets.QMessageBox.critical(self, "Settings",
                    "Could not write %s:\n%s" % (C.CONF_PATH, r.stderr.strip() or "permission denied"))
                return
        self.cfg = new; self.accept()

# ---------- main window ----------
class Win(QtWidgets.QMainWindow):
    reqCmd  = QtCore.pyqtSignal(str)
    reqPut  = QtCore.pyqtSignal(str, str)
    reqGet  = QtCore.pyqtSignal(str, str)
    reqConn = QtCore.pyqtSignal(str, int)
    reqDisc = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self.cfg = C.load()
        self.setWindowTitle("Retro Dial-Up Gateway")
        self.setWindowIcon(QtGui.QIcon.fromTheme("network-modem"))
        self.resize(900, 720)
        self._menus(); self._build()
        self.thr = QtCore.QThread(self); self.tcp = Tcp(); self.tcp.moveToThread(self.thr); self.thr.start()
        self.tcp.out.connect(self._append); self.tcp.done.connect(lambda rc: self._append("[rc=%s]\n" % rc))
        self.tcp.state.connect(self._on_conn)
        self.reqCmd.connect(self.tcp.command); self.reqPut.connect(self.tcp.put)
        self.reqGet.connect(self.tcp.get); self.reqConn.connect(self.tcp.open_conn)
        self.reqDisc.connect(self.tcp.close_conn)
        self._start_journal()
        self.timer = QtCore.QTimer(self); self.timer.timeout.connect(self._poll)
        self.timer.start(2000); self._poll()
        if not os.path.exists(C.CONF_PATH):
            QtCore.QTimer.singleShot(300, lambda: self._append(
                "[no %s yet - using defaults; open Settings to create it]\n" % C.CONF_PATH))

    # ---- menus ----
    def _menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        a = m.addAction("&Quit"); a.setShortcut("Ctrl+Q"); a.triggered.connect(self.close)
        m = mb.addMenu("&Settings")
        a = m.addAction("&Settings…"); a.setShortcut("Ctrl+,"); a.triggered.connect(self.open_settings)
        m.addSeparator()
        m.addAction("Open config file", lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(C.CONF_PATH)))
        m.addAction("Reload config", self._reload_cfg)
        m = mb.addMenu("&Help")
        m.addAction("Vintage PC setup guide", lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl(REPO_URL + "#setting-up-the-vintage-pc")))
        m.addAction("About", lambda: QtWidgets.QMessageBox.about(self, "About",
            "<b>Retro Dial-Up Gateway</b> %s<br>Internet and remote control for Windows 95/98/ME/2000/XP "
            "over a serial cable.<br><a href='%s'>%s</a>" % (VERSION, REPO_URL, REPO_URL)))

    def open_settings(self):
        d = Settings(self.cfg, self)
        if d.exec_() != QtWidgets.QDialog.Accepted: return
        self.cfg = d.cfg
        self._append("[settings saved to %s]\n" % C.CONF_PATH)
        if d.restart.isChecked():
            for u in (GW_UNIT, PX_UNIT):
                if self._active(u): self._svc("restart", u)
        self.prev = None; self._start_journal()

    def _reload_cfg(self):
        self.cfg = C.load(); self._append("[config reloaded]\n")

    # ---- layout ----
    def _build(self):
        cw = QtWidgets.QWidget(); self.setCentralWidget(cw); v = QtWidgets.QVBoxLayout(cw)
        self.status = QtWidgets.QLabel("…"); self.status.setTextFormat(QtCore.Qt.RichText)
        f = self.status.font(); f.setPointSize(f.pointSize() + 1); self.status.setFont(f)
        v.addWidget(self.status)

        row = QtWidgets.QHBoxLayout(); v.addLayout(row)
        for label, verb, unit in (("Start Gateway", "start", GW_UNIT), ("Stop Gateway", "stop", GW_UNIT),
                                  ("Start Proxy", "start", PX_UNIT), ("Stop Proxy", "stop", PX_UNIT)):
            b = QtWidgets.QPushButton(label); row.addWidget(b)
            b.clicked.connect(lambda _, vb=verb, u=unit: self._svc(vb, u))

        gb = QtWidgets.QGroupBox("Network stats"); v.addWidget(gb)
        g = QtWidgets.QGridLayout(gb); g.setHorizontalSpacing(18); self.st = {}
        fields = [("Modem", "modem"), ("Last dial", "dial"), ("Connected", "up"),
                  ("Addresses", "addr"), ("Line", "line"), ("Latency", "lat"),
                  ("RX", "rx"), ("TX", "tx"), ("Utilization", "util"),
                  ("Errors", "err"), ("Peak", "peak"), ("Sessions", "sess")]
        for i, (lab, key) in enumerate(fields):
            r, c = divmod(i, 3)
            l = QtWidgets.QLabel(lab + ":"); l.setStyleSheet("color:gray;")
            val = QtWidgets.QLabel("–"); val.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            g.addWidget(l, r, c * 2); g.addWidget(val, r, c * 2 + 1); self.st[key] = val
        for c in (1, 3, 5): g.setColumnStretch(c, 1)
        self.prev = None; self.peak = [0.0, 0.0]; self.latproc = None
        self.modem_state = "unknown"; self.last_dial = "–"; self.sessions = 0

        tabs = QtWidgets.QTabWidget(); v.addWidget(tabs, 1)
        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.console = QtWidgets.QPlainTextEdit(); self.console.setReadOnly(True); self.console.setFont(mono)
        tabs.addTab(self.console, "Console (control the PC)")
        self.dlog = QtWidgets.QPlainTextEdit(); self.dlog.setReadOnly(True); self.dlog.setFont(mono)
        self.dlog.setMaximumBlockCount(5000); tabs.addTab(self.dlog, "Dialing log")

        row2 = QtWidgets.QHBoxLayout(); v.addLayout(row2)
        self.bConn = QtWidgets.QPushButton("Connect")
        self.input = QtWidgets.QLineEdit(); self.input.setPlaceholderText("DOS command, e.g.  dir c:\\")
        self.bSend = QtWidgets.QPushButton("Send"); self.bPush = QtWidgets.QPushButton("Push File…")
        self.bGet = QtWidgets.QPushButton("Get File…")
        for w in (self.bConn, self.input, self.bSend, self.bPush, self.bGet):
            row2.addWidget(w, 1 if w is self.input else 0)
        self.bConn.clicked.connect(self.toggle_conn)
        self.bSend.clicked.connect(self.send_cmd); self.input.returnPressed.connect(self.send_cmd)
        self.bPush.clicked.connect(self.push_file); self.bGet.clicked.connect(self.get_file)
        self.connected = False; self._set_conn_ui(False)

    # ---- services (polkit lets the retrogw group do this without a password) ----
    def _svc(self, verb, unit):
        r = run(["systemctl", "--no-ask-password", verb, unit])
        if r.returncode != 0:     # not authorized yet: let polkit prompt
            r = run(["systemctl", verb, unit])
        msg = (r.stderr or r.stdout).strip() or "ok"
        self._append("[%s %s] %s\n" % (verb, unit.split(".")[0], msg))
        self._poll()

    def _active(self, unit):
        return run(["systemctl", "is-active", unit]).stdout.strip() == "active"

    # ---- status + stats ----
    def _poll(self):
        ifc = C.iface(self.cfg); up = os.path.exists("/sys/class/net/%s" % ifc)
        self._stats(up, ifc)
        gw = "●" if self._active(GW_UNIT) else "○"; px = "●" if self._active(PX_UNIT) else "○"
        link = "<b style='color:#2a2'>PPP UP</b> (%s)" % ifc if up else "<b style='color:#a22'>PPP down</b>"
        self.status.setText("%s &nbsp; Gateway %s &nbsp; Proxy %s (%s:%s)"
                            % (link, gw, px, self.cfg["LOCAL_IP"], self.cfg["PROXY_PORT"]))

    def _stats(self, up, ifc):
        S = self.st; baud = int(self.cfg["BAUD"]); cap = baud / 10.0   # 8N1 = 10 bits/byte
        S["modem"].setText(self.modem_state); S["dial"].setText(self.last_dial)
        S["sess"].setText("%d this log" % self.sessions)
        S["line"].setText("%d bps (8N1, max %s/s)" % (baud, hsize(cap)))
        if not up:
            for k in ("up", "addr", "lat", "rx", "tx", "util", "err"): S[k].setText("–")
            self.prev = None; return
        base = "/sys/class/net/%s/statistics/" % ifc
        def rd(n):
            try: return int(open(base + n).read())
            except Exception: return 0
        now = time.time()
        cur = dict(rxb=rd("rx_bytes"), txb=rd("tx_bytes"), rxp=rd("rx_packets"), txp=rd("tx_packets"),
                   err=rd("rx_errors") + rd("tx_errors"), drop=rd("rx_dropped") + rd("tx_dropped"))
        rxr = txr = 0.0
        if self.prev:
            dt = max(now - self.prev[0], 0.001)
            rxr = (cur["rxb"] - self.prev[1]["rxb"]) / dt; txr = (cur["txb"] - self.prev[1]["txb"]) / dt
        self.prev = (now, cur); self.peak = [max(self.peak[0], rxr), max(self.peak[1], txr)]
        S["rx"].setText("%s  %d pkts  %s/s" % (hsize(cur["rxb"]), cur["rxp"], hsize(rxr)))
        S["tx"].setText("%s  %d pkts  %s/s" % (hsize(cur["txb"]), cur["txp"], hsize(txr)))
        S["util"].setText("↓ %d%%  ↑ %d%%" % (min(100, rxr * 100 / cap), min(100, txr * 100 / cap)))
        S["peak"].setText("↓ %s/s  ↑ %s/s" % (hsize(self.peak[0]), hsize(self.peak[1])))
        S["err"].setText("%d errors, %d dropped" % (cur["err"], cur["drop"]))
        a = run(["ip", "-br", "addr", "show", ifc]).stdout.split()
        peer = a[a.index("peer") + 1].split("/")[0] if "peer" in a else "?"
        S["addr"].setText("%s ↔ %s" % (a[2] if len(a) > 2 else "?", peer))
        pid = run(["pgrep", "-n", "-f", "pppd .*unit %s( |$)" % self.cfg["PPP_UNIT"]]).stdout.strip()
        et = run(["ps", "-o", "etimes=", "-p", pid]).stdout.split() if pid else []
        if et:
            s = int(et[0]); S["up"].setText("%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60))
        if self.latproc is None or self.latproc.state() == QtCore.QProcess.NotRunning:
            self.latproc = QtCore.QProcess(self); self.latproc.finished.connect(self._on_ping)
            self.latproc.start("ping", ["-c1", "-W2", self.cfg["REMOTE_IP"]])

    def _on_ping(self, *_):
        out = bytes(self.latproc.readAllStandardOutput()).decode(errors="replace")
        i = out.find("time=")
        self.st["lat"].setText(out[i + 5:].split()[0] + " ms" if i >= 0 else "no reply")

    # ---- dialing log: follow the gateway's journal ----
    def _start_journal(self):
        if getattr(self, "jproc", None):
            self.jproc.kill(); self.jproc.waitForFinished(500)
        self.dlog.clear(); self._lastline = None; self._jbuf = b""
        self.sessions = 0; self.last_dial = "–"; self.modem_state = "unknown"
        self.jproc = QtCore.QProcess(self)
        self.jproc.readyReadStandardOutput.connect(self._on_journal)
        self.jproc.readyReadStandardError.connect(lambda: self.dlog.appendPlainText(
            bytes(self.jproc.readAllStandardError()).decode(errors="replace").strip()
            + "\n(to see the log, add yourself to the systemd-journal group and log in again)"))
        self.jproc.start("journalctl", ["-u", GW_UNIT, "-f", "-n", "80", "-o", "short", "--no-hostname"])

    def _on_journal(self):
        self._jbuf += bytes(self.jproc.readAllStandardOutput())
        *lines, self._jbuf = self._jbuf.split(b"\n")
        for raw in lines:
            ln = raw.decode("utf-8", "replace"); msg = ln.split(": ", 1)[-1]
            if msg == self._lastline: continue
            self._lastline = msg; self._track(ln, msg); self.dlog.appendPlainText(ln)

    def _track(self, ln, msg):
        ts = " ".join(ln.split()[:3])
        if "waiting for dial" in msg: self.modem_state = "Idle – waiting for dial"
        elif "waiting for serial port" in msg: self.modem_state = "Serial port missing"
        elif "[vmodem] <- " in msg:
            cmd = msg.split("<- ", 1)[1].strip()
            if cmd.upper().startswith("ATD"):
                self.last_dial = "%s  (%s)" % (cmd, ts); self.modem_state = "Dialing"
            else: self.modem_state = "Modem init (%s)" % cmd
        elif "-> CONNECT" in msg: self.modem_state = "CONNECT – PPP negotiating"
        elif msg.startswith("Connect: ppp"):
            self.modem_state = "PPP negotiating"; self.sessions += 1
        elif "remote IP address" in msg: self.modem_state = "Online"
        elif "hang-up" in msg or "Connection terminated" in msg or "Modem hangup" in msg:
            self.modem_state = "Hung up"
        elif "config error" in msg: self.modem_state = "Config error (see log)"
        elif "Stopped " in msg and "retrogw" in msg.lower(): self.modem_state = "Gateway stopped"

    # ---- console ----
    def toggle_conn(self):
        if self.connected: self.reqDisc.emit()
        else:
            self._append("[connecting to %s:%s…]\n" % (self.cfg["REMOTE_IP"], self.cfg["CONTROL_PORT"]))
            self.reqConn.emit(self.cfg["REMOTE_IP"], int(self.cfg["CONTROL_PORT"]))
    def _on_conn(self, ok): self.connected = ok; self._set_conn_ui(ok)
    def _set_conn_ui(self, ok):
        self.bConn.setText("Disconnect" if ok else "Connect")
        for w in (self.input, self.bSend, self.bPush, self.bGet): w.setEnabled(ok)
    def send_cmd(self):
        c = self.input.text().strip()
        if not c: return
        self._append("C> " + c + "\n"); self.input.clear(); self.reqCmd.emit(c)
    def push_file(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Push file to the PC")
        if not p: return
        base = os.path.basename(p).upper()[:12]
        name, ok = QtWidgets.QInputDialog.getText(self, "Destination", "Path on the PC:", text="C:\\" + base)
        if ok and name: self.reqPut.emit(p, name)
    def get_file(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Get file", "Path on the PC:", text="C:\\")
        if not (ok and name): return
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save as", os.path.basename(name.replace("\\", "/")))
        if p: self.reqGet.emit(name, p)
    def _append(self, t):
        self.console.moveCursor(QtGui.QTextCursor.End); self.console.insertPlainText(t)
        self.console.moveCursor(QtGui.QTextCursor.End)

    def closeEvent(self, e):
        self.jproc.kill(); self.jproc.waitForFinished(500)
        if self.connected: self.reqDisc.emit()
        self.thr.quit(); self.thr.wait(1500); e.accept()

def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("retrogw"); app.setDesktopFileName("retrogw")
    w = Win(); w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
