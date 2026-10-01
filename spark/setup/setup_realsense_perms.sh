#!/usr/bin/env bash
# One-time: install librealsense udev rules and add the user to the video group.
set -euo pipefail
install -m 644 ~murata-lab/molmoact2-setup/99-realsense-libusb.rules /etc/udev/rules.d/99-realsense-libusb.rules
udevadm control --reload-rules
udevadm trigger
usermod -aG video murata-lab
echo "--- done. groups for murata-lab (effective after next login):"; id murata-lab
