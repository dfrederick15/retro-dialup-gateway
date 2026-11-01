#!/bin/bash
# retro-dialup-gateway installer.
#   sudo ./install.sh                install deps, files, services (enabled + started)
#   sudo ./install.sh --no-deps      skip the package-manager step
#   sudo ./install.sh --no-start     install but leave the services stopped
set -euo pipefail
[ "$(id -u)" = 0 ] || exec sudo "$0" "$@"

HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX=/opt/retrogw
CONF_DIR=/etc/retrogw
CONF=$CONF_DIR/retrogw.conf
TARGET_USER="${SUDO_USER:-${PKEXEC_UID:+$(id -nu "$PKEXEC_UID")}}"
DEPS=1; START=1
for a in "$@"; do
  case "$a" in
    --no-deps) DEPS=0 ;;
    --no-start) START=0 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown option: $a"; exit 1 ;;
  esac
done
say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

# ---- 1. dependencies ----
if [ $DEPS = 1 ]; then
  say "Installing dependencies"
  if command -v apt-get >/dev/null; then
    apt-get update -qq
    apt-get install -y python3 python3-serial python3-pyqt5 ppp lrzsz iproute2 nftables
  elif command -v dnf >/dev/null; then
    dnf install -y python3 python3-pyserial python3-qt5 ppp lrzsz iproute nftables
  elif command -v pacman >/dev/null; then
    pacman -S --needed --noconfirm python python-pyserial python-pyqt5 ppp lrzsz iproute2 nftables
  elif command -v zypper >/dev/null; then
    zypper --non-interactive install python3 python3-pyserial python3-qt5 ppp lrzsz iproute2 nftables
  else
    echo "Unknown package manager. Install manually: python3, pyserial, PyQt5, pppd, lrzsz, iproute2, nftables"
  fi
fi
for c in python3 pppd ip; do
  command -v $c >/dev/null || [ -x /usr/sbin/$c ] || { echo "missing: $c"; exit 1; }
done
python3 -c 'import serial' 2>/dev/null || { echo "missing: python3 pyserial"; exit 1; }

# ---- 2. files ----
say "Installing to $PREFIX"
install -d "$PREFIX" "$PREFIX/windows"
install -m 644 "$HERE"/linux/retrogw/*.py "$PREFIX/"
install -m 755 "$HERE"/linux/retrogw/*.sh "$PREFIX/"
chmod 755 "$PREFIX"/{vmodem,retroproxy,retrogw_gui,tcpctl,serctl}.py
install -m 644 "$HERE"/windows/bin/*.EXE "$HERE"/windows/README.txt "$PREFIX/windows/"
rm -rf "$PREFIX/__pycache__"

for pair in "retrogw:retrogw_gui.py" "retrogw-ctl:tcpctl.py" "retrogw-serial:serctl.py"; do
  ln -sf "$PREFIX/${pair#*:}" "/usr/local/bin/${pair%%:*}"
done
install -Dm 644 "$HERE/linux/retrogw.desktop" /usr/share/applications/retrogw.desktop

# ---- 3. group, permissions, config ----
say "Configuring"
getent group retrogw >/dev/null || groupadd --system retrogw
install -d -m 755 "$CONF_DIR"
if [ ! -f "$CONF" ]; then
  PORT="$(ls /dev/serial/by-id/* 2>/dev/null | grep -viE 'jtag|debug|sdr|pluto|arduino' | head -n1 || true)"
  [ -n "$PORT" ] || PORT=/dev/ttyUSB0
  SERIAL_PORT="$PORT" python3 -c "import sys; sys.path.insert(0,'$PREFIX'); import retrogw_config as C; print(C.dumps(C.load('/nonexistent')), end='')" > "$CONF"
  echo "    created $CONF (serial port guess: $PORT)"
else
  echo "    keeping existing $CONF"
fi
chown root:retrogw "$CONF"; chmod 664 "$CONF"

if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != root ]; then
  groups=retrogw
  getent group systemd-journal >/dev/null && groups="$groups,systemd-journal"
  # serial-port group: dialout (Debian/Fedora/SUSE) or uucp (Arch)
  if getent group dialout >/dev/null; then groups="$groups,dialout"
  elif getent group uucp >/dev/null; then groups="$groups,uucp"; fi
  usermod -aG "$groups" "$TARGET_USER"
  echo "    added $TARGET_USER to: $groups  (log out and back in for this to apply)"
fi

if [ -d /etc/polkit-1/rules.d ]; then
  install -m 644 "$HERE/linux/polkit/50-retrogw.rules" /etc/polkit-1/rules.d/50-retrogw.rules
fi

# ---- 4. services ----
say "Installing services"
install -m 644 "$HERE"/linux/systemd/retrogw-*.service /etc/systemd/system/
systemctl daemon-reload
if command -v systemctl >/dev/null && systemctl is-active -q ModemManager 2>/dev/null; then
  echo "    NOTE: ModemManager is running and may probe the serial port with AT commands."
  echo "          If dialing is flaky:  sudo systemctl disable --now ModemManager"
fi
if [ $START = 1 ]; then
  # upgrade: restart what was already running so the new code loads
  systemctl try-restart retrogw-proxy.service
  if ls /sys/class/net | grep -q '^ppp'; then
    echo "    A PPP link is up: not restarting retrogw-gateway (would hang up the PC)."
    echo "    Restart it later:  sudo systemctl restart retrogw-gateway"
  else
    systemctl try-restart retrogw-gateway.service
  fi
  systemctl enable --now retrogw-proxy.service retrogw-gateway.service
else
  systemctl enable retrogw-proxy.service retrogw-gateway.service
fi

say "Done"
cat <<MSG
  Control panel:   retrogw            (or "Retro Dial-Up Gateway" in the app menu)
  Settings:        Settings menu in the panel, or edit $CONF
  CLI control:     retrogw-ctl "dir c:\\"
  Windows files:   $PREFIX/windows/  (SERSHTCP.EXE, SERSHELL.EXE, REBOOT.EXE, KILL.EXE)
  Log:             journalctl -u retrogw-gateway -f
MSG
