#!/usr/bin/env bash
# Copy recorded camera clips from the Spark to the Mac and transcode them to H.264 for sharing.
# Usage: bash scripts/fetch_videos.sh [remote_dir] [local_dir]
set -euo pipefail
REMOTE="${1:-spark:~/molmoact2-setup/logs/videos}"
LOCAL="${2:-logs/videos-$(date +%Y-%m-%d)}"
mkdir -p "$LOCAL"
scp -q "$REMOTE/*.mp4" "$REMOTE/*.png" "$LOCAL/" 2>/dev/null || true
for f in "$LOCAL"/*.mp4; do
  [ -f "$f" ] || continue
  case "$f" in *_h264.mp4) continue;; esac
  out="${f%.mp4}_h264.mp4"
  [ -f "$out" ] && continue
  ffmpeg -loglevel error -y -i "$f" -c:v libx264 -preset fast -crf 23 -pix_fmt yuv420p -movflags +faststart "$out"
  echo "transcoded: $out"
done
ls -la "$LOCAL"
