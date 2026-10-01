#!/usr/bin/env bash
# One-time setup for the LEFT CANable: pin its interface name via udev and bring it up at 1 Mbps.
# Usage (needs sudo):  sudo bash ~/molmoact2-setup/setup_can_left.sh <CANABLE_SERIAL>
set -euo pipefail
SERIAL="${1:?usage: sudo bash setup_can_left.sh <CANABLE_SERIAL>}"
RULES=/etc/udev/rules.d/90-can.rules
LINE="SUBSYSTEM==\"net\", ACTION==\"add\", ATTRS{serial}==\"$SERIAL\", NAME=\"can_follower_l\""
touch "$RULES"
grep -qF "$SERIAL" "$RULES" || echo "$LINE" >> "$RULES"
echo "--- $RULES"; cat "$RULES"
udevadm control --reload-rules
udevadm trigger --subsystem-match=net --action=add
sleep 2
IF=$(ls /sys/class/net | grep -E "^can" | head -1)
echo "--- interface now named: $IF"
ip link set "$IF" down 2>/dev/null || true
ip link set "$IF" up type can bitrate 1000000
ip -details -statistics link show "$IF"
