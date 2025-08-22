#!/bin/bash
# Start Sorting App.command

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
VENV_DIR="$SCRIPT_DIR/venv"
LOG_FILE="/tmp/sortingapp.log"
PORT=1789

echo "Starting Trustpilot Sorting App..."
echo "Script: $SCRIPT_DIR"
echo "Log: $LOG_FILE"
echo "Port: $PORT"
echo "-----------------------------------"

# Create/activate venv
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment at $VENV_DIR"
  python3 -m venv "$VENV_DIR" || { echo "Failed to create venv"; read -p "Press Enter to close..."; exit 1; }
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate" || { echo "Failed to activate venv"; read -p "Press Enter to close..."; exit 1; }
echo "Virtual environment activated. Python: $(python -V)"

# Ensure pip up-to-date
python -m pip install --upgrade pip >/dev/null 2>&1

# Ensure dependencies
if ! python -c "import uvicorn" 2>/dev/null; then
  echo "Installing dependencies from requirements.txt (first run may take a while)..."
  pip install -r "$SCRIPT_DIR/requirements.txt" >> "$LOG_FILE" 2>&1 || {
    echo "Error installing dependencies. See $LOG_FILE"
    tail -n 80 "$LOG_FILE" || true
    read -p "Press Enter to close..."; exit 1;
  }
fi

# Kill any previous server on target port
if lsof -ti:$PORT >/dev/null 2>&1; then
  echo "Port $PORT in use; attempting to free it..."
  for PID in $(lsof -ti:$PORT); do
    echo "TERM $PID"
    kill -TERM "$PID" 2>/dev/null || true
  done
  /bin/sleep 1
  for PID in $(lsof -ti:$PORT); do
    echo "KILL $PID"
    kill -KILL "$PID" 2>/dev/null || true
  done
  /bin/sleep 1
fi

# Also try to kill any uvicorn processes for this app (best-effort)
for P in $(pgrep -f "uvicorn.*app.main:app" 2>/dev/null); do
  echo "Killing uvicorn process $P"
  kill -TERM "$P" 2>/dev/null || true
  /bin/sleep 1
  kill -KILL "$P" 2>/dev/null || true
done

# Start server with python -m uvicorn and redirect logs
cd "$SCRIPT_DIR" || { echo "Failed to cd to $SCRIPT_DIR"; read -p "Press Enter to close..."; exit 1; }
echo "Starting server on http://127.0.0.1:$PORT ..."
nohup python -m uvicorn app.main:app --host 127.0.0.1 --port $PORT >> "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "Server PID: $SERVER_PID"

echo "Waiting for server to come up..."
/bin/sleep 2

# Try to open browser
open "http://127.0.0.1:$PORT" 2>/dev/null || true

echo "-----------------------------------"
echo "If the app didn't open, check logs below:"
echo "-----------------------------------"
tail -n 80 "$LOG_FILE" || true

echo ""
echo "Press Enter to stop the server and close this window..."
read -r _

# Gracefully stop server
kill "$SERVER_PID" 2>/dev/null || true
/bin/sleep 1
if kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "Force killing server..."
  kill -KILL "$SERVER_PID" 2>/dev/null || true
fi

echo "Done."


