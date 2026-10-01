#!/usr/bin/env bash
# Start the MolmoAct2 FastAPI policy server (port 8202) from the local Hugging Face cache.
# HF_HUB_OFFLINE=1: no network access is attempted, so it also starts without internet
# (Mac-Spark direct link at the venue). Log: ~/molmoact2-setup/logs/host-server-8202.log
set -euo pipefail
cd ~/molmoact2-setup/workspaces/molmoact2-official
if ss -ltn | grep -q ":8202"; then
  echo "policy server already listening on 8202"; exit 0
fi
HF_HUB_OFFLINE=1 setsid nohup ~/.local/bin/uv run --no-sync python examples/yam/host_server_yam.py \
  --host 127.0.0.1 --port 8202 --dtype bfloat16 \
  > ~/molmoact2-setup/logs/host-server-8202.log 2>&1 < /dev/null &
for i in $(seq 1 60); do
  sleep 3
  if ss -ltn | grep -q ":8202"; then echo "policy server listening on 8202 after $((i*3)) s"; exit 0; fi
done
echo "policy server did not come up; see ~/molmoact2-setup/logs/host-server-8202.log" >&2
exit 1
