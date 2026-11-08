#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$DIR/server.pid"

if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "🛑 Stopped Phone AI Gateway (PID: $PID)"
    else
        echo "Process $PID not running."
    fi
    rm -f "$PIDFILE"
else
    # Find any running main.py in this dir
    pkill -f "phone-ai-gateway/main.py" 2>/dev/null && echo "🛑 Stopped background instance." || echo "Not running."
fi
