#!/usr/bin/env bash
# Persist the LEFT CANable (serial 20A7378545465006) as can_follower_l.
set -euo pipefail
RULES=/etc/udev/rules.d/90-can.rules
LINE="SUBSYSTEM==\"net\", ACTION==\"add\", ATTRS{serial}==\"20A7378545465006\", NAME=\"can_follower_l\""
grep -qF "20A7378545465006" "$RULES" || echo "$LINE" >> "$RULES"
udevadm control --reload-rules
echo "--- $RULES"; cat "$RULES"
