#!/usr/bin/env bash
# =============================================================================
# SIMULAQRON MANAGER SCRIPT
# Location: /home/student/CYQT/GUI_lab/backend/manage_simulaqron.sh
# Usage:
#   ./manage_simulaqron.sh start    -> Safely start SimulaQron (cleans stale PIDs)
#   ./manage_simulaqron.sh stop     -> Gracefully stop SimulaQron
#   ./manage_simulaqron.sh status   -> Check running status and active nodes
#   ./manage_simulaqron.sh restart  -> Force kill old & start fresh
# =============================================================================

VENV_PATH="/home/student/simulaqron412/simulaqron-venv"
CONFIG_FILE="/home/student/CYQT/simulaquroncode/simulaqron/examples/native-mode/bb84_native/simulaqron_network.json"
PID_DIR="$HOME/.simulaqron_pids"
PID_FILE="$PID_DIR/simulaqron_network_default.pid"

# Colors for terminal output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 1. Activate Virtual Environment
if [ -d "$VENV_PATH" ]; then
    source "$VENV_PATH/bin/activate"
else
    echo -e "${RED}[ERROR] Virtual environment not found at: $VENV_PATH${NC}"
    exit 1
fi

ACTION="${1:-status}"

cleanup_stale() {
    # Check if PID file exists but process is dead
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
    # Kill any python processes running simulaqron
    PIDS=$(ps aux | grep -i "simulaqron" | grep -v "manage_simulaqron" | grep -v "grep" | awk '{print $2}')
    if [ -n "$PIDS" ]; then
        echo "$PIDS" | xargs kill -9 > /dev/null 2>&1
    fi
    # Remove all PID files
    rm -rf "$PID_DIR"/*.pid
    echo -e "${GREEN}[OK] Stale processes and PID files cleared.${NC}"
}

case "$ACTION" in
    start)
        echo -e "${BLUE}========================================${NC}"
        echo -e "${BLUE}   Starting SimulaQron Network          ${NC}"
        echo -e "${BLUE}========================================${NC}"
        
        cleanup_stale

        # Check if already running
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p "$PID" > /dev/null 2>&1; then
                echo -e "${YELLOW}[WARNING] SimulaQron is ALREADY RUNNING (PID: $PID).${NC}"
                echo -e "To restart fresh, run: ${GREEN}./manage_simulaqron.sh restart${NC}"
                simulaqron nodes get
                exit 0
            else
                rm -f "$PID_FILE"
            fi
        fi

        echo -e "${YELLOW}[INFO] Launching Alice and Bob nodes...${NC}"
        simulaqron start --nodes=Alice,Bob --network-config-file "$CONFIG_FILE"

        sleep 2
        echo -e "\n${GREEN}[SUCCESS] SimulaQron is now running! Active nodes:${NC}"
        simulaqron nodes get
        ;;

    stop)
        echo -e "${YELLOW}[INFO] Stopping SimulaQron...${NC}"
        simulaqron stop > /dev/null 2>&1
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
        if [ -f "$PID_FILE" ] && ps -p "$(cat "$PID_FILE")" > /dev/null 2>&1; then
            PID=$(cat "$PID_FILE")
            echo -e "${GREEN}● Status: RUNNING (PID: $PID)${NC}"
            echo -e "${BLUE}Active Nodes in Network:${NC}"
            simulaqron nodes get
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
