#!/usr/bin/env bash
# Start both YAM follower servers (right = port 1234, left = port 1235) in the background.
# The CAN bitrate is lost on every reboot, so it is (re)applied first; without it the servers
# exit at once with "Missing or unavailable CAN interfaces". SUPPORT BOTH ARMS before running:
# the gripper calibration moves for a few seconds, then the arms float in gravity compensation.
# Log: ~/molmoact2-setup/logs/bi_yam_servers.log
set -euo pipefail
for i in can_follower_l can_follower_r; do
  sudo -n ip link set "$i" up type can bitrate 1000000 2>/dev/null || true
done
down=$(ip -br link show type can | awk '$2 != "UP" {print $1}')
if [ -n "$down" ]; then
  echo "CAN interface(s) not UP: $down (check the CANable USB cables)" >&2; exit 1
fi
if pgrep -f "[m]inimum_gello" >/dev/null; then
  echo "follower servers already running (pkill -f '[m]inimum_gello' stops them; hold the arms)"; exit 0
fi
cd ~/molmoact2-setup/workspaces/lerobot-client
source .venv/bin/activate
setsid nohup python -m lerobot.scripts.setup_bi_yam_servers --eval \
  > ~/molmoact2-setup/logs/bi_yam_servers.log 2>&1 < /dev/null &
for i in $(seq 1 20); do
  sleep 1
  if [ "$(ss -ltn | grep -cE ':1234|:1235')" = 2 ]; then
    echo "follower servers listening on 1234/1235 after $i s"
    echo "check temperatures with: ~/molmoact2-setup/g5 temps --seconds 5"; exit 0
  fi
  pgrep -f "[s]etup_bi_yam_servers" >/dev/null || break
done
echo "follower servers did not come up; see ~/molmoact2-setup/logs/bi_yam_servers.log:" >&2
tail -5 ~/molmoact2-setup/logs/bi_yam_servers.log >&2
exit 1
