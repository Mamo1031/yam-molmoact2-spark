#!/usr/bin/env bash
# Start/stop the i2rt gravity-compensation loop for one arm, detached, with a log and pid file.
# Usage: gravcomp.sh start <can_iface>   |   gravcomp.sh stop <can_iface>   |   gravcomp.sh status <can_iface>
# SAFETY: support the arm by hand when starting AND when stopping (torque disappears on stop).
set -u
CMD="${1:?start|stop|status}"; IF="${2:?can iface, e.g. can_follower_r}"
ROOT=~/molmoact2-setup/workspaces/lerobot-client
LOG=~/molmoact2-setup/logs/gravcomp_${IF}.log
PID=~/molmoact2-setup/logs/gravcomp_${IF}.pid
case "$CMD" in
  start)
    if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then echo "already running (pid $(cat "$PID"))"; exit 0; fi
    cd "$ROOT/i2rt" && source "$ROOT/.venv/bin/activate"
    : > "$LOG"
    setsid nohup python i2rt/robots/motor_chain_robot.py --channel "$IF" --gripper_type linear_4310 --operation_mode gravity_comp > "$LOG" 2>&1 < /dev/null &
    echo $! > "$PID"; sleep 4; echo "started pid $(cat "$PID")"; tail -5 "$LOG" ;;
  stop)
    if [ -f "$PID" ]; then kill -INT "$(cat "$PID")" 2>/dev/null; sleep 2; kill -0 "$(cat "$PID")" 2>/dev/null && kill -TERM "$(cat "$PID")"; rm -f "$PID"; echo "stopped"; else echo "not running"; fi ;;
  status)
    if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then echo "running pid $(cat "$PID")"; else echo "not running"; fi; tail -5 "$LOG" 2>/dev/null ;;
esac
