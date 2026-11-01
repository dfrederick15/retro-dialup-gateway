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

        # web proxy
        g3 = QtWidgets.QGroupBox("Web proxy"); f3 = QtWidgets.QFormLayout(g3); v.addWidget(g3)
        self.pport = QtWidgets.QSpinBox(); self.pport.setRange(1, 65535); self.pport.setValue(int(cfg["PROXY_PORT"]))
        f3.addRow("Port:", self.pport)
        self.strip = QtWidgets.QCheckBox("Strip scripts and styles from pages")
        self.strip.setChecked(cfg["PROXY_STRIP_SCRIPTS"] == "1"); f3.addRow(self.strip)
        self.tls = QtWidgets.QCheckBox("Accept https:// from the old browser (SSL 3.0 / TLS 1.0)")
        self.tls.setChecked(cfg["PROXY_TLS"] == "1"); f3.addRow(self.tls)
        self.tls2http = QtWidgets.QCheckBox("…then switch pages to http:// so plain HTTP crosses the serial line")
        self.tls2http.setChecked(cfg["PROXY_TLS_TO_HTTP"] == "1"); f3.addRow(self.tls2http)
        self.tls.toggled.connect(self.tls2http.setEnabled); self.tls2http.setEnabled(self.tls.isChecked())
        self.gz = QtWidgets.QCheckBox("Compress pages with gzip (browsers that support it)")
        self.gz.setChecked(cfg["PROXY_COMPRESS"] == "1"); f3.addRow(self.gz)
        self.imgq = QtWidgets.QSpinBox(); self.imgq.setRange(0, 95); self.imgq.setValue(int(cfg["PROXY_IMAGE_QUALITY"]))
        self.imgq.setSpecialValueText("off (pass images through)")
        f3.addRow("Image JPEG quality:", self.imgq)
        self.imgw = QtWidgets.QSpinBox(); self.imgw.setRange(0, 4096); self.imgw.setSingleStep(32)
        self.imgw.setValue(int(cfg["PROXY_IMAGE_MAX_WIDTH"])); self.imgw.setSuffix(" px")
        self.imgw.setSpecialValueText("no limit"); f3.addRow("Shrink images wider than:", self.imgw)
        hint = QtWidgets.QLabel("For https://: install the certificate from <b>http://%s:%s/</b> on the PC, "
                                "and set its Security proxy like the HTTP proxy." % (cfg["LOCAL_IP"], cfg["PROXY_PORT"]))
        hint.setStyleSheet("color:gray;"); f3.addRow(hint)

        g4 = QtWidgets.QGroupBox("Remote control"); f4 = QtWidgets.QFormLayout(g4); v.addWidget(g4)
        self.cport = QtWidgets.QSpinBox(); self.cport.setRange(1, 65535); self.cport.setValue(int(cfg["CONTROL_PORT"]))
        f4.addRow("SERSHTCP control port:", self.cport)

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
        self.tls.setChecked(True); self.tls2http.setChecked(True); self.gz.setChecked(True)
        self.imgq.setValue(int(d["PROXY_IMAGE_QUALITY"])); self.imgw.setValue(int(d["PROXY_IMAGE_MAX_WIDTH"]))

    def _save(self):
        up = self.uplink.currentData() if self.uplink.currentIndex() >= 0 and \
            self.uplink.currentText() == self.uplink.itemText(self.uplink.currentIndex()) else self.uplink.currentText().strip()
        new = dict(self.cfg, SERIAL_PORT=self.value_port(), BAUD=self.baud.currentText(),
                   LOCAL_IP=self.local.text().strip(), REMOTE_IP=self.remote.text().strip(),
                   DNS1=self.dns1.text().strip(), DNS2=self.dns2.text().strip(), UPLINK=up or "",
                   PPP_UNIT=str(self.unit.value()), PROXY_PORT=str(self.pport.value()),
                   PROXY_STRIP_SCRIPTS="1" if self.strip.isChecked() else "0",
                   PROXY_TLS="1" if self.tls.isChecked() else "0",
                   PROXY_TLS_TO_HTTP="1" if self.tls2http.isChecked() else "0",
                   PROXY_COMPRESS="1" if self.gz.isChecked() else "0",
                   PROXY_IMAGE_QUALITY=str(self.imgq.value()), PROXY_IMAGE_MAX_WIDTH=str(self.imgw.value()),
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
        self.resize(920, 860)
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

        gp = QtWidgets.QGroupBox("Web proxy"); v.addWidget(gp)
        g = QtWidgets.QGridLayout(gp); g.setHorizontalSpacing(18)
        fields = [("Status", "pst"), ("Browser setting", "pset"), ("HTTPS", "ptls"),
                  ("Requests", "preq"), ("Clients", "pcli"), ("Avg time", "pavg"),
                  ("Fetched", "pin"), ("Sent to PC", "pout"), ("Saved", "psave"),
                  ("Optimizing", "pslim"), ("Last request", "plast")]
        for i, (lab, key) in enumerate(fields):
            r, c = divmod(i, 3) if i < 9 else (3 + i - 9, 0)
            l = QtWidgets.QLabel(lab + ":"); l.setStyleSheet("color:gray;")
            val = QtWidgets.QLabel("–"); val.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            g.addWidget(l, r, c * 2)
            if i >= 9:              # full-width rows
                val.setMinimumWidth(10); g.addWidget(val, r, 1, 1, 5)
            else: g.addWidget(val, r, c * 2 + 1)
            self.st[key] = val
        for c in (1, 3, 5): g.setColumnStretch(c, 1)
        self._preset()

        tabs = QtWidgets.QTabWidget(); v.addWidget(tabs, 1)
        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.console = QtWidgets.QPlainTextEdit(); self.console.setReadOnly(True); self.console.setFont(mono)
        tabs.addTab(self.console, "Console (control the PC)")
        self.dlog = QtWidgets.QPlainTextEdit(); self.dlog.setReadOnly(True); self.dlog.setFont(mono)
        self.dlog.setMaximumBlockCount(5000); tabs.addTab(self.dlog, "Dialing log")
        self.plog = QtWidgets.QPlainTextEdit(); self.plog.setReadOnly(True); self.plog.setFont(mono)
        self.plog.setMaximumBlockCount(5000); tabs.addTab(self.plog, "Proxy log")

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
        pxa = self._active(PX_UNIT); self._pstats(pxa)
        gw = "●" if self._active(GW_UNIT) else "○"; px = "●" if pxa else "○"
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

    # ---- web proxy stats (from its per-request journal lines) ----
    def _preset(self):
        self.pc = dict(n=0, err=0, nin=0, nout=0, ms=0, tls=0, tlsfail=0, gz=0, img=0, redir=0, tunnel=0)
        self.plisten = None; self.ptls = None; self.pfp = None
        self.st["plast"].setText("–"); self.st["plast"].setToolTip("")

    def _pstats(self, active):
        S = self.st; port = self.cfg["PROXY_PORT"]; pc = self.pc; c = self.cfg
        S["pst"].setText(("Running – listening on %s" % (self.plisten or "port " + port))
                         if active else "Stopped")
        S["pset"].setText("HTTP + Security proxy %s : %s" % (c["LOCAL_IP"], port))
        if c["PROXY_TLS"] == "0":
            S["ptls"].setText("off (http:// only)"); S["ptls"].setToolTip("")
        else:
            S["ptls"].setText("%d accepted, %d failed, %d passed through%s" % (
                              pc["tls"], pc["tlsfail"], pc["tunnel"],
                              "  (last: %s)" % self.ptls if self.ptls else ""))
            S["ptls"].setToolTip("Install the certificate on the PC from http://%s:%s/" % (c["LOCAL_IP"], port)
                                 + ("\nCA SHA-1: " + self.pfp if self.pfp else ""))
        opt = []
        if c["PROXY_STRIP_SCRIPTS"] != "0": opt.append("scripts/styles stripped")
        if c["PROXY_COMPRESS"] != "0": opt.append("gzip (%d sent)" % pc["gz"])
        if c["PROXY_IMAGE_QUALITY"] != "0":
            opt.append("images → JPEG q%s%s (%d re-encoded)" % (c["PROXY_IMAGE_QUALITY"],
                       ", ≤%spx" % c["PROXY_IMAGE_MAX_WIDTH"] if c["PROXY_IMAGE_MAX_WIDTH"] != "0" else "",
                       pc["img"]))
        if c["PROXY_TLS"] != "0" and c["PROXY_TLS_TO_HTTP"] != "0":
            opt.append("https → http on the wire (%d)" % pc["redir"])
        S["pslim"].setText(";  ".join(opt) or "off (pages passed through)")
        S["preq"].setText("%d  (%d failed)" % (pc["n"], pc["err"]) if pc["n"] else "none yet")
        S["pavg"].setText("%.1f s" % (pc["ms"] / pc["n"] / 1000.0) if pc["n"] else "–")
        S["pin"].setText(hsize(pc["nin"])); S["pout"].setText(hsize(pc["nout"]))
        S["psave"].setText("%s  (%d%%)" % (hsize(pc["nin"] - pc["nout"]),
                           100 - pc["nout"] * 100 // pc["nin"]) if pc["nin"] > pc["nout"] else "–")
        n = 0
        if active:
            n = len(run(["ss", "-Htn", "state", "established", "( sport = :%s )" % port]).stdout.splitlines())
        S["pcli"].setText("%d open connection%s" % (n, "" if n == 1 else "s") if active else "–")

    def _ptrack(self, ln, msg):
        if msg.startswith("retroproxy on "):
            self._preset(); self.plisten = msg.split()[2]; return
        if msg.startswith("CA certificate SHA-1 "):
            self.pfp = msg.split()[3]; return
        if not msg.startswith("[proxy] REQ "): return
        # [proxy] REQ client method code in= out= ms= flags= url [(note)]
        p = msg.split(None, 9)
        if len(p) < 9: return
        if p[8].startswith("flags="):
            if len(p) < 10: return
            flags, url = p[8][6:].split(","), p[9]
        else:                                   # older log lines without flags
            flags, url = [], " ".join(p[8:])
        method, code = p[3], p[4]
        try:
            nin, nout, ms = (int(x.split("=", 1)[1]) for x in p[5:8])
        except ValueError: return
        pc = self.pc; bad = not code.isdigit() or int(code) >= 400
        if method == "CONNECT" and "tunnel" in flags:   # modern TLS client, passed through
            pc["tunnel"] += 1; pc["nin"] += nin; pc["nout"] += nout
            pc["err"] += bad
        elif method == "CONNECT":               # SSL/TLS handshake from the old browser
            if code == "200":
                pc["tls"] += 1
                if " (" in url: self.ptls = url.split(" (", 1)[1].rstrip(")")
            elif code != "501": pc["tlsfail"] += 1
            else: pc["err"] += 1
        else:
            pc["n"] += 1; pc["nin"] += nin; pc["nout"] += nout; pc["ms"] += ms
            pc["err"] += bad
            for f in ("gz", "img", "redir"): pc[f] += f in flags
        ts = ln.split()[2] if len(ln.split()) > 2 else ""
        short = url if len(url) <= 90 else url[:87] + "…"
        self.st["plast"].setText("%s  %s %s  %s  %s, %.1f s" % (ts, method, code, short, hsize(nout), ms / 1000.0))
        self.st["plast"].setToolTip(url)

    def _on_ping(self, *_):
        out = bytes(self.latproc.readAllStandardOutput()).decode(errors="replace")
        i = out.find("time=")
        self.st["lat"].setText(out[i + 5:].split()[0] + " ms" if i >= 0 else "no reply")

    # ---- dialing log: follow the gateway's journal ----
    def _start_journal(self):
        self.sessions = 0; self.last_dial = "–"; self.modem_state = "unknown"
        self.jproc = self._follow(getattr(self, "jproc", None), GW_UNIT, self.dlog, self._track,
                                  ["-n", "80"], dedupe=True)
        # proxy: replay the current run so the counters cover it, else the last lines
        self._preset()
        since = run(["systemctl", "show", "-p", "ActiveEnterTimestamp", "--value", PX_UNIT]).stdout.strip()
        extra = ["--since", " ".join(since.split()[1:3])] if self._active(PX_UNIT) and since else ["-n", "200"]
        self.pproc = self._follow(getattr(self, "pproc", None), PX_UNIT, self.plog, self._ptrack, extra)

    def _follow(self, old, unit, view, track, extra, dedupe=False):
        """Tail a unit's journal into `view`, passing each line to `track`."""
        if old:
            old.kill(); old.waitForFinished(500)
        view.clear()
        p = QtCore.QProcess(self); st = {"buf": b"", "last": None}
        def out():
            st["buf"] += bytes(p.readAllStandardOutput())
            *lines, st["buf"] = st["buf"].split(b"\n")
            for raw in lines:
                ln = raw.decode("utf-8", "replace"); msg = ln.split(": ", 1)[-1]
                if dedupe and msg == st["last"]: continue
                st["last"] = msg; track(ln, msg); view.appendPlainText(ln)
        p.readyReadStandardOutput.connect(out)
        p.readyReadStandardError.connect(lambda: view.appendPlainText(
            bytes(p.readAllStandardError()).decode(errors="replace").strip()
            + "\n(to see the log, add yourself to the systemd-journal group and log in again)"))
        p.start("journalctl", ["-u", unit, "-f", "-o", "short", "--no-hostname"] + extra)
        return p

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
        for p in (self.jproc, self.pproc):
            p.kill(); p.waitForFinished(500)
        if self.connected: self.reqDisc.emit()
        self.thr.quit(); self.thr.wait(1500); e.accept()

def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("retrogw"); app.setDesktopFileName("retrogw")
    w = Win(); w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
