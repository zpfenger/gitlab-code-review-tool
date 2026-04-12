#!/bin/bash
# GitLab Code Review Tool - Start Script
# Usage: ./start.sh [--port PORT] [--host HOST]

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Default values
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5001}"
WORKERS="${WORKERS:-4}"
LOG_LEVEL="${LOG_LEVEL:-info}"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --host)
            HOST="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --workers)
            WORKERS="$2"
            shift 2
            ;;
        --log-level)
            LOG_LEVEL="$2"
            shift 2
            ;;
        --dev)
            DEV_MODE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Change to project directory
cd "$PROJECT_DIR"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Creating..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# Create necessary directories
mkdir -p data/logs data/reports data/config

# Set environment variables
export SECRET_KEY="${SECRET_KEY:-$(python -c 'import secrets; print(secrets.token_hex(32))')}"
export CONFIG_PATH="${CONFIG_PATH:-$PROJECT_DIR/config/admin.yaml}"

echo "=========================================="
echo "GitLab Code Review Tool"
echo "=========================================="
echo "Host: $HOST"
echo "Port: $PORT"
echo "Workers: $WORKERS"
echo "Log Level: $LOG_LEVEL"
echo "Project Dir: $PROJECT_DIR"
echo "=========================================="

if [ "$DEV_MODE" = true ]; then
    echo "Starting in development mode..."
    python run.py --host "$HOST" --port "$PORT" --reload
else
    echo "Starting in production mode..."
    python run.py --host "$HOST" --port "$PORT" --workers "$WORKERS" --log-level "$LOG_LEVEL"
fi
