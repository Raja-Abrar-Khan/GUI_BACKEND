#!/usr/bin/env bash
# =============================================================================
# SIMULAQRON MANAGER SCRIPT (100% Portable & Self-Contained)
# Location: backend/manage_simulaqron.sh
# Usage:
#   ./manage_simulaqron.sh start    -> Safely start SimulaQron (cleans stale PIDs)
#   ./manage_simulaqron.sh stop     -> Gracefully stop SimulaQron
#   ./manage_simulaqron.sh status   -> Check running status and active nodes
#   ./manage_simulaqron.sh restart  -> Force kill old & start fresh
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/bb84_native/simulaqron_network.json"
PID_DIR="$HOME/.simulaqron_pids"
PID_FILE="$PID_DIR/simulaqron_network_default.pid"

# Fallback config file if not inside bb84_native
if [ ! -f "$CONFIG_FILE" ]; then
    CONFIG_FILE="$SCRIPT_DIR/simulaqron_network.json"
fi

# Colors for terminal output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 1. Dynamically Auto-Detect SimulaQron binary / virtualenv
SIMULAQRON_CMD=""

if command -v simulaqron &>/dev/null; then
    SIMULAQRON_CMD="simulaqron"
elif [ -f "$SCRIPT_DIR/venv/bin/simulaqron" ]; then
    SIMULAQRON_CMD="$SCRIPT_DIR/venv/bin/simulaqron"
elif [ -f "$HOME/simulaqron-venv/bin/simulaqron" ]; then
    SIMULAQRON_CMD="$HOME/simulaqron-venv/bin/simulaqron"
elif [ -f "/home/student/simulaqron412/simulaqron-venv/bin/simulaqron" ]; then
    SIMULAQRON_CMD="/home/student/simulaqron412/simulaqron-venv/bin/simulaqron"
fi

ACTION="${1:-status}"

cleanup_stale() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE" 2>/dev/null)
        if [ -n "$PID" ] && ! ps -p "$PID" > /dev/null 2>&1; then
            echo -e "${YELLOW}[INFO] Found stale PID ($PID). Cleaning up stale PID file...${NC}"
            rm -f "$PID_FILE"
        fi
    fi
}

force_kill_all() {
    echo -e "${YELLOW}[INFO] Force killing any existing SimulaQron / Python node processes...${NC}"
    PIDS=$(ps aux | grep -i "simulaqron" | grep -v "manage_simulaqron" | grep -v "grep" | awk '{print $2}')
    if [ -n "$PIDS" ]; then
        echo "$PIDS" | xargs kill -9 > /dev/null 2>&1
    fi
    rm -rf "$PID_DIR"/*.pid
    echo -e "${GREEN}[OK] Stale processes and PID files cleared.${NC}"
}

check_simulaqron_installed() {
    if [ -z "$SIMULAQRON_CMD" ]; then
        echo -e "${RED}[ERROR] 'simulaqron' executable not found in PATH or standard virtualenvs.${NC}"
        echo -e "${YELLOW}Please install SimulaQron in your environment: pip install simulaqron${NC}"
        exit 1
    fi
}

case "$ACTION" in
    start)
        check_simulaqron_installed
        echo -e "${BLUE}========================================${NC}"
        echo -e "${BLUE}   Starting SimulaQron Network          ${NC}"
        echo -e "${BLUE}========================================${NC}"
        cleanup_stale

        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE" 2>/dev/null)
            if [ -n "$PID" ] && ps -p "$PID" > /dev/null 2>&1; then
                echo -e "${YELLOW}[WARNING] SimulaQron is ALREADY running with PID: $PID${NC}"
                echo -e "To restart fresh, run: ${GREEN}./manage_simulaqron.sh restart${NC}"
                $SIMULAQRON_CMD nodes get 2>/dev/null || true
                exit 0
            else
                rm -f "$PID_FILE"
            fi
        fi

        echo -e "${YELLOW}[INFO] Launching Alice and Bob nodes using $CONFIG_FILE...${NC}"
        $SIMULAQRON_CMD start --nodes=Alice,Bob --network-config-file "$CONFIG_FILE"

        sleep 2
        echo -e "\n${GREEN}[SUCCESS] SimulaQron is now running! Active nodes:${NC}"
        $SIMULAQRON_CMD nodes get 2>/dev/null || true
        ;;

    stop)
        check_simulaqron_installed
        echo -e "${YELLOW}[INFO] Stopping SimulaQron...${NC}"
        $SIMULAQRON_CMD stop > /dev/null 2>&1 || true
        sleep 1
        cleanup_stale
        if [ -f "$PID_FILE" ]; then
            force_kill_all
        fi
        echo -e "${GREEN}[OK] SimulaQron has been stopped.${NC}"
        ;;

    restart)
        echo -e "${YELLOW}[INFO] Restarting SimulaQron...${NC}"
        force_kill_all
        sleep 1
        $0 start
        ;;

    status)
        echo -e "${BLUE}========================================${NC}"
        echo -e "${BLUE}   SimulaQron Network Status            ${NC}"
        echo -e "${BLUE}========================================${NC}"
        cleanup_stale
        if [ -f "$PID_FILE" ] && ps -p "$(cat "$PID_FILE" 2>/dev/null)" > /dev/null 2>&1; then
            PID=$(cat "$PID_FILE")
            echo -e "${GREEN}● Status: RUNNING (PID: $PID)${NC}"
            if [ -n "$SIMULAQRON_CMD" ]; then
                echo -e "${BLUE}Active Nodes in Network:${NC}"
                $SIMULAQRON_CMD nodes get 2>/dev/null || true
            fi
        else
            echo -e "${RED}● Status: NOT RUNNING${NC}"
            echo -e "To start, run: ${GREEN}./manage_simulaqron.sh start${NC}"
        fi
        ;;

    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
