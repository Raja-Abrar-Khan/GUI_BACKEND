# SIMULAQRON RUN & TROUBLESHOOTING GUIDE

This guide provides both the **automated one-command script** and the **manual step-by-step terminal commands** to start, stop, monitor, and troubleshoot SimulaQron.

---

## ⚡ 1. QUICKEST METHOD: Automated Script

We created a helper script inside `backend/`: **[`manage_simulaqron.sh`](file:///home/student/CYQT/GUI_lab/backend/manage_simulaqron.sh)**.  
It automatically handles virtual environment activation, cleans stale PID locks, and starts/stops nodes safely.

### How to use:

```bash
cd /home/student/CYQT/GUI_lab/backend

# 1. Check current status:
./manage_simulaqron.sh status

# 2. Start Alice and Bob network:
./manage_simulaqron.sh start

# 3. If it is already stuck/running somewhere, Force Restart clean:
./manage_simulaqron.sh restart

# 4. Stop when finished:
./manage_simulaqron.sh stop
```

---

## 🛠️ 2. MANUAL STEP-BY-STEP TERMINAL COMMANDS

If you prefer running the commands manually in your terminal, follow these steps:

### Step 1: Activate SimulaQron Virtual Environment
```bash
source /home/student/simulaqron412/simulaqron-venv/bin/activate
```
*(Your prompt will change to `(simulaqron-venv) student@...`)*

### Step 2: Check Status / Active Nodes
```bash
simulaqron nodes get
```

### Step 3: Start SimulaQron Network
```bash
simulaqron start --nodes=Alice,Bob --network-config-file /home/student/CYQT/simulaquroncode/simulaqron/examples/native-mode/bb84_native/simulaqron_network.json
```

### Step 4: Verify Alice and Bob are Active
```bash
simulaqron nodes get
```
You should see:
```text
Nodes in network:
Alice
Bob
```

### Step 5: Stop SimulaQron Gracefully
```bash
simulaqron stop
```

---

## 🚨 3. TROUBLESHOOTING: "Already Running" or Stuck Processes

A very common issue in SimulaQron is getting the error:
> `Network with name default is already running`  
> or SimulaQron freezes because an old background process didn't close properly.

### Why this happens:
SimulaQron writes a process ID file to `~/.simulaqron_pids/simulaqron_network_default.pid`. If a terminal was closed without running `simulaqron stop`, this PID file remains behind or old python processes keep holding the sockets (ports 8001, 8010, etc.).

### 🔧 Fix in 3 Commands:

#### Command 1: Kill any hanging SimulaQron python processes
```bash
ps aux | grep -i "simulaqron" | grep -v "grep" | awk '{print $2}' | xargs kill -9 2>/dev/null
```

#### Command 2: Delete stale PID lock files
```bash
rm -rf ~/.simulaqron_pids/*.pid
```

#### Command 3: Restart SimulaQron Fresh
```bash
simulaqron start --nodes=Alice,Bob --network-config-file /home/student/CYQT/simulaquroncode/simulaqron/examples/native-mode/bb84_native/simulaqron_network.json
```

*(Or simply run `./manage_simulaqron.sh restart` which does all 3 commands automatically!)*

---

## 🔑 4. HOW TO TEST BB84 KEY GENERATION

Once SimulaQron is running with Alice and Bob, you can test key generation:

```bash
# 1. Activate venv if not already active
source /home/student/simulaqron412/simulaqron-venv/bin/activate

# 2. Go to teammate's bb84 directory
cd /home/student/CYQT/simulaquroncode/simulaqron/examples/native-mode/bb84_native

# 3. Start Bob receiver in background:
python3 bob_bb84_100.py &

# 4. Run Alice sender:
python3 alice_bb84_100.py
```

Check the new key generated in the database:
```bash
tail -n 5 /home/student/CYQT/simulaquroncode/simulaqron/examples/native-mode/qkms/database/key_database.txt
```
