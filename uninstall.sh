#!/bin/bash
# Remove retro-dialup-gateway.  --purge also deletes /etc/retrogw and the group.
set -u
[ "$(id -u)" = 0 ] || exec sudo "$0" "$@"
systemctl disable --now retrogw-gateway.service retrogw-proxy.service 2>/dev/null
/opt/retrogw/nat-down.sh 2>/dev/null
rm -f /etc/systemd/system/retrogw-gateway.service /etc/systemd/system/retrogw-proxy.service
systemctl daemon-reload
rm -f /etc/polkit-1/rules.d/50-retrogw.rules /usr/share/applications/retrogw.desktop
rm -f /usr/local/bin/retrogw /usr/local/bin/retrogw-ctl /usr/local/bin/retrogw-serial
rm -rf /opt/retrogw
if [ "${1:-}" = "--purge" ]; then
  rm -rf /etc/retrogw; groupdel retrogw 2>/dev/null
  echo "retro-dialup-gateway removed (config purged)"
else
  echo "retro-dialup-gateway removed (config kept in /etc/retrogw; --purge to delete)"
fi
