#!/bin/bash
set +e
LOG_FILE="bot_restarts.log"
PID_FILE="bot.pid"

log_message() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

cleanup() {
  if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    kill "$PID" 2>/dev/null || true
    rm -f "$PID_FILE"
  fi
  exit 0
}

trap cleanup SIGINT SIGTERM

if [ ! -d "venv" ]; then
  echo "venv belum ada, jalankan bash setup.sh dulu"
  exit 1
fi

while true; do
  source venv/bin/activate
  log_message "Starting Discord Invite Hunter"
  python3 bot.py &
  PID=$!
  echo "$PID" > "$PID_FILE"
  wait "$PID"
  CODE=$?
  rm -f "$PID_FILE"
  log_message "Bot exited with code $CODE, restart in 5s"
  sleep 5
done
