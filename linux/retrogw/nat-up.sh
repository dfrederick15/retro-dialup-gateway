#!/bin/bash
# Share this machine's internet connection with the PPP client (root).
# Uses iptables when present (cooperates with ufw/docker/firewalld-iptables),
# otherwise a private nftables table. Settings from /etc/retrogw/retrogw.conf.
set -e
CONF="${RETROGW_CONF:-/etc/retrogw/retrogw.conf}"
[ -r "$CONF" ] && . <(grep -E '^(REMOTE_IP|UPLINK)=[A-Za-z0-9_.:-]*$' "$CONF")
REMOTE="${REMOTE_IP:-192.168.95.2}"
UPLINK="${UPLINK:-$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="dev"){print $(i+1); exit}}')}"
[[ "$REMOTE" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "bad REMOTE_IP"; exit 1; }
[[ "$UPLINK" =~ ^[A-Za-z0-9_.:-]{1,15}$ ]] || { echo "retrogw: no uplink interface (no default route?) - NAT skipped"; exit 0; }
TAG="retrogw"

"$(dirname "$0")/nat-down.sh" >/dev/null 2>&1 || true
sysctl -qw net.ipv4.ip_forward=1

IPT="$(command -v iptables || true)"
if [ -n "$IPT" ] && "$IPT" -L FORWARD -n >/dev/null 2>&1; then
  "$IPT" -I FORWARD -s "$REMOTE" -o "$UPLINK" -j ACCEPT -m comment --comment "$TAG"
  "$IPT" -I FORWARD -d "$REMOTE" -i "$UPLINK" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT -m comment --comment "$TAG"
  "$IPT" -t nat -I POSTROUTING -s "$REMOTE" -o "$UPLINK" -j MASQUERADE -m comment --comment "$TAG"
  echo "retrogw NAT up via iptables ($REMOTE -> $UPLINK)"
else
  nft -f - <<NFT
table ip retrogw {
  chain forward {
    type filter hook forward priority -10; policy accept;
    ip saddr $REMOTE oifname "$UPLINK" accept
    ip daddr $REMOTE ct state established,related accept
  }
  chain postrouting {
    type nat hook postrouting priority 100; policy accept;
    ip saddr $REMOTE oifname "$UPLINK" masquerade
  }
}
NFT
  echo "retrogw NAT up via nftables ($REMOTE -> $UPLINK)"
fi
