#!/usr/bin/env bash
# Re-map the CANable with serial 208E37AE45465006 from can_follower_l to can_follower_r (right arm).
set -euo pipefail
RULES=/etc/udev/rules.d/90-can.rules
sed -i "s/ATTRS{serial}==\"208E37AE45465006\", NAME=\"can_follower_l\"/ATTRS{serial}==\"208E37AE45465006\", NAME=\"can_follower_r\"/" "$RULES"
echo "--- $RULES"; cat "$RULES"
ip link set can_follower_l down 2>/dev/null || true
udevadm control --reload-rules
# A rename needs the interface to be re-added: unbind/rebind the USB device is simplest.
DEV=$(readlink -f /sys/class/net/can_follower_l/device 2>/dev/null | xargs -r basename)
if [ -n "$DEV" ]; then
  echo "$DEV" > /sys/bus/usb/drivers/gs_usb/unbind
  sleep 1
  echo "$DEV" > /sys/bus/usb/drivers/gs_usb/bind
  sleep 2
fi
IF=$(ls /sys/class/net | grep -E "^can" | head -1)
echo "--- interface now named: $IF"
ip link set "$IF" up type can bitrate 1000000
ip -br link show type can
