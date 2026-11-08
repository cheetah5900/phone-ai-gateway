#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$DIR/server.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "Phone AI Gateway is already running (PID: $(cat "$PIDFILE"))"
    exit 0
fi

nohup python3 -u "$DIR/main.py" > "$DIR/server.log" 2>&1 &
echo $! > "$PIDFILE"
echo "✅ Phone AI Gateway started in background (PID: $(cat "$PIDFILE"))"
echo "📜 Logs: $DIR/server.log"
