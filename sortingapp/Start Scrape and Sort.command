#!/bin/bash
# Start Scrape and Sort.command

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
VENV_DIR="$SCRIPT_DIR/venv"
LOG_FILE="/tmp/sortingapp.log"
PORT=1789

echo "Starting Scrape & Sort..."
echo "Script: $SCRIPT_DIR"
echo "Log: $LOG_FILE"
echo "Port: $PORT"
echo "-----------------------------------"

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment at $VENV_DIR"
  python3 -m venv "$VENV_DIR" || { echo "Failed to create venv"; read -p "Press Enter to close..."; exit 1; }
fi

source "$VENV_DIR/bin/activate" || { echo "Failed to activate venv"; read -p "Press Enter to close..."; exit 1; }
echo "Virtual environment activated. Python: $(python -V)"

python -m pip install --upgrade pip >/dev/null 2>&1
if ! python -c "import uvicorn" 2>/dev/null; then
  echo "Installing dependencies from requirements.txt..."
  pip install -r "$SCRIPT_DIR/requirements.txt" >> "$LOG_FILE" 2>&1 || { echo "Error installing deps. See $LOG_FILE"; tail -n 80 "$LOG_FILE"; read -p "Press Enter to close..."; exit 1; }
fi

free_port() {
  local P=$1
  if lsof -ti:$P >/dev/null 2>&1; then
    echo "Freeing port $P..."
    for PID in $(lsof -ti:$P); do
      kill -TERM "$PID" 2>/dev/null || true
    done
    /bin/sleep 1
    for PID in $(lsof -ti:$P); do
      kill -KILL "$PID" 2>/dev/null || true
    done
    /bin/sleep 1
  fi
}

# Free ports we might have used before (1789 new, 8000 legacy) BEFORE starting
free_port $PORT
free_port 8000

cd "$SCRIPT_DIR" || { echo "Failed to cd to $SCRIPT_DIR"; read -p "Press Enter to close..."; exit 1; }
echo "Starting server on http://127.0.0.1:$PORT ..."
nohup python -m uvicorn app.main:app --host 127.0.0.1 --port $PORT >> "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "Server PID: $SERVER_PID"

# Wait for server to be healthy
for i in {1..20}; do
  /bin/sleep 0.5
  if curl -sSf "http://127.0.0.1:$PORT/landing" >/dev/null 2>&1; then
    break
  fi
done
open "http://127.0.0.1:$PORT/landing" 2>/dev/null || true

echo "-----------------------------------"
echo "If the app didn't open, check logs below:"
echo "-----------------------------------"
tail -n 80 "$LOG_FILE" || true

echo ""
echo "Server is running in the background."
echo "Use 'Stop Server.command' to stop it and free the port."
echo "Press Enter to close this window (server will keep running)..."
read -r _
exit 0


