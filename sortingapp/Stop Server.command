#!/bin/bash
# Stop Server.command

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
VENV_PATH="$SCRIPT_DIR/venv"
PORT=1789

echo "Stopping Trustpilot Sorting App Server..."
echo "Port: $PORT"
echo "-----------------------------------"

# Activate virtual environment if present
if [ -d "$VENV_PATH" ]; then
    # shellcheck disable=SC1091
    source "$VENV_PATH/bin/activate" && echo "Virtual environment activated." || true
fi

# Kill processes using port
if lsof -ti:$PORT >/dev/null 2>&1; then
    echo "Found processes on port $PORT: $(lsof -ti:$PORT)"
    for PID in $(lsof -ti:$PORT); do
        echo "TERM $PID"
        kill -TERM "$PID" 2>/dev/null || true
    done
    /bin/sleep 1
    for PID in $(lsof -ti:$PORT); do
        echo "KILL $PID"
        kill -KILL "$PID" 2>/dev/null || true
    done
else
    echo "No processes using port $PORT."
fi

# Kill uvicorn processes for this app (best-effort)
for P in $(pgrep -f "uvicorn.*app.main:app" 2>/dev/null); do
    echo "Killing uvicorn process $P"
    kill -TERM "$P" 2>/dev/null || true
    /bin/sleep 1
    kill -KILL "$P" 2>/dev/null || true
done

echo "✅ Port $PORT should now be free."
read -p "Press Enter to close this window..."
