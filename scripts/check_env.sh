#!/usr/bin/env bash
# Collect DGX Spark system information for Gate G1 (reference.md section 5).
# Usage: bash scripts/check_env.sh [output_dir]
# Writes <output_dir>/system-info-<timestamp>.txt and prints it.
set -u

out_dir="${1:-$HOME/molmoact2-setup/logs}"
mkdir -p "$out_dir"
out="$out_dir/system-info-$(date +%Y%m%d-%H%M%S).txt"

run() {
  # Print a header, run the command, never abort on failure.
  echo
  echo "### $*"
  "$@" 2>&1 || echo "(exit $?)"
}

{
  echo "# system-info $(date -Is)"
  run uname -a
  run cat /etc/os-release
  run nvidia-smi
  run nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
  run df -h /
  run free -h
  run ip -brief addr
  run lsusb
  run lsusb -t
  run ip link
  run modinfo gs_usb
  run modinfo slcan
  run modinfo can_raw
  run bash -c 'ls -l /sys/class/net/ | grep -i can || echo "no can interfaces"'
  run bash -c 'command -v uv && uv --version || echo "uv not installed"'
  run bash -c 'command -v candump || echo "can-utils not installed"'
  run bash -c 'command -v rs-enumerate-devices && rs-enumerate-devices -s || echo "librealsense CLI not installed"'
} | tee "$out"

echo
echo "saved: $out"
