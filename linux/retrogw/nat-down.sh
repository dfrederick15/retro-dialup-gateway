#!/bin/bash
# Remove the rules added by nat-up.sh (root).
TAG="retrogw"
IPT="$(command -v iptables || true)"
if [ -n "$IPT" ]; then
  for t in filter nat; do
    "$IPT" -t $t -S 2>/dev/null | grep -- "--comment $TAG" | sed 's/^-A/-D/' | \
      while read -r rule; do eval "\"$IPT\" -t $t $rule"; done
  done
fi
nft delete table ip retrogw 2>/dev/null || true
echo "retrogw NAT down"
