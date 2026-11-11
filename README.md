# Hive

Hive is an early prototype for controlling and monitoring a ROS 2 robot fleet from one operator laptop.

The current version has a React front end and a small Python backend. The first real control goal is manual remote control of preset ROS 2 RC car robots over the local network.

## Current Features

- Fleet dashboard with locally saved robots
- Add Robot modal for creating local robot profiles
- Preset robot types:
  - Camera RC Car
  - LiDAR RC Car
  - Gripper RC Car
- Robot profile pages with capabilities, ROS 2 topic lists, and manual control buttons
- Per-robot ROS domain IDs so robots can reuse topic names without colliding
- ROS 2 `/cmd_vel` publish commands for forward, backward, left rotation, right rotation, and stop
- Gripper open/close buttons for the Gripper RC Car preset
- Fleet command page with a ChatGPT-style command box
- Local persistence with `localStorage`

## Tech Stack

- JavaScript
- React
- Vite
- CSS
- Python standard library backend
- ROS 2 Jazzy command-line tools

## Requirements

- Ubuntu 24.04
- ROS 2 Jazzy installed at `/opt/ros/jazzy`
- Node.js and npm
- Python 3

This project was built and tested with Node `v24.18.0` and npm `11.16.0`.

## Install

```bash
npm install
```

## Run

Start the app with:

```bash
npm run hive
```

This starts both the Python backend and the Vite frontend, then opens Hive in the browser.

You can also run the pieces separately:

```bash
npm run backend
npm run frontend
```

If you want to launch the app by typing `hive`, link the local package once:

```bash
npm link
```

Then start it with:

```bash
hive
```

## Build

```bash
npm run build
```

## Current Scope

Hive now has a backend route for ROS 2 manual control commands. It assumes the operator laptop and robot are on the same network and that ROS Wi-Fi/static peer setup is valid for the robot IP entered during onboarding.

Each robot profile stores its own ROS domain ID. Hive auto-assigns the next unused domain starting at `42`, and the user can edit that value while onboarding. This lets multiple robots use the same topic names, such as `/cmd_vel`, without sharing one ROS domain.

For each ROS command, the backend runs:

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=<ROBOT_DOMAIN_ID>
unset ROS_LOCALHOST_ONLY
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_STATIC_PEERS='<USER_DEFINED_ROBOT_IP>'
```

Then it publishes the requested command to `/cmd_vel`.

## Manual Control Commands

- Forward: `ros2 topic pub --once -w 1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.5}, angular: {z: 0.0}}"`
- Backward: `ros2 topic pub --once -w 1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: -0.5}, angular: {z: 0.0}}"`
- Left rotation: `ros2 topic pub --once -w 1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0}, angular: {z: 1.0}}"`
- Right rotation: `ros2 topic pub --once -w 1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0}, angular: {z: -1.0}}"`
- Stop: `ros2 topic pub --once -w 1 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0}, angular: {z: 0.0}}"`

The gripper commands currently publish string messages to `/gripper_command`. Update `backend/server.py` once the gripper robot's final ROS interface is known.

## Executive (mission planning prototype)

The Executive turns an English mission into a validated, operator-approved plan for three
robots (`camera_01`, `lidar_01`, `gripper_01`). It runs as a separate FastAPI service on
`127.0.0.1:8788`, and its page is under **Executive** in the sidebar. Manual control above is unchanged.

- Planner: `Qwen/Qwen3-8B` (non-thinking) on a separately hosted vLLM endpoint, or the
  **Baseline** templates. Baseline output is always labeled and is never substituted for Qwen.
- Transport: in-process fake fleet (Delivery A) or AgenticROS/Corebrum Synapse to ROS 2 fake
  action servers (Delivery B, see `docs/delivery-b-agenticros.md`).
- Details: `docs/executive-architecture.md`. Run evidence: `docs/run-report.md`.
- Teammates: start with `docs/next-steps.md`.

### One-time setup

Windows PowerShell (on Ubuntu use `python3` and `.venv/bin/python` instead of `.venv\Scripts\python`):

```powershell
npm install
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
cd ..
copy .env.example .env      # optional; only needed for live Qwen or non-default settings
```

### Run the Executive

**Fake robots (default):**

```powershell
npm run hive
```

This starts the manual-control backend (port 8787), the Executive (port 8788) and the frontend,
then opens the browser. Click **Executive** in the sidebar. Stop everything with Ctrl+C.

Other ways to start it:

- `npm run hive -- --no-executive`: the original app only.
- `cd backend; .venv\Scripts\python -m hive_exec`: the Executive service alone.

**Through ROS 2 / AgenticROS Synapse (Delivery B):** needs WSL `Ubuntu-24.04` with ROS 2 Jazzy.
First-time build inside Ubuntu (details in `docs/delivery-b-agenticros.md`):

```bash
sudo apt install ros-jazzy-rosbridge-suite build-essential python3-colcon-common-extensions
# plus Node.js 20 or newer
bash ros/build.sh
HIVE_CORE_PATCH=1 bash bridge/scripts/build-core.sh
```

Then use three terminals:

```powershell
# 1: fake ROS robots + rosbridge
wsl -d Ubuntu-24.04 -- bash /mnt/c/path/to/hive/ros/run_fake_fleet.sh
# 2: Synapse bridge
wsl -d Ubuntu-24.04 -- node /mnt/c/path/to/hive/bridge/server.mjs
# 3: the app, using the ROS path
$env:HIVE_GATEWAY="synapse"; npm run hive
```

Replace `/mnt/c/path/to/hive` with the repo location as seen from WSL. From Git Bash, prefix `wsl`
commands with `MSYS_NO_PATHCONV=1`. The transport badge turns green after the first task
completes a round trip.

**With live Qwen:** Qwen3-8B needs a GPU host running vLLM:

```bash
pip install vllm
vllm serve Qwen/Qwen3-8B --host 127.0.0.1 --port 8000 --max-model-len 12288 --seed 1234 --api-key "$KEY"
ssh -L 8000:127.0.0.1:8000 gpu-host        # from the operator laptop
```

Set `QWEN_BASE_URL=http://127.0.0.1:8000/v1` and `QWEN_API_KEY` in `.env`, then restart.
On startup the Executive checks that the model is listed and answers one request in the plan
format, and it enables Qwen only if both pass. The Qwen badge shows the result.
Without a server, choosing Qwen shows a planner error; it never falls back to Baseline silently.

### Test

**1. Automated tests** (no robots, ROS or Qwen needed; about 10 seconds):

```powershell
cd backend
.venv\Scripts\python -m pytest -q
```

They cover the contracts, the plan checks, the five acceptance stories on the fake fleet,
cancellation, duplicate and stale events, recovery, the HTTP API, and the Qwen client against
a scripted mock server (not real Qwen).

**2. Manual demo in the app** (fake robots, Baseline planner):

1. Run `npm run hive` and open **Executive**.
2. Scenario **Unknown object/route**, then **Reset run**.
3. Planner **Baseline** (pick Qwen only if a server is configured).
4. Keep the default command, *Find the red block in the work area, check the pickup corridor, and
   bring the block to the drop-off area.*, and click **Plan mission**.
5. **Approve r1**: the camera and lidar work at the same time, and the map updates from their reports.
6. **Approve r2**: the gripper drives, picks, returns and places. The mission shows **Completed**.

Each run holds one mission, so click **Reset run** before the next one. Other cases to try:

| Setup before Reset run | Command | Expected |
|---|---|---|
| Scenario **Known object** | default | One transport plan, completed |
| (none) | `Take a picture of the work area.` | Camera only; others idle |
| (none) | `Bring the block over here.` | Clarification question, nothing runs |
| (none) | `Fly the drone over the warehouse.` | Rejected |
| fault `route_blocked` | default | Blocked after scanning |
| fault `block_missing` | default | Blocked: block not found |
| fault `two_red_blocks` | default | Asks which block; answer e.g. `red_block_2` |
| Known + `nav_fault_after_pickup` | default | Failure while carrying, then a recovery plan to approve, then completed |
| Known + `gripper_fault_after_pickup` | default | Blocked, block still held |
| Known + `uncertain_place` | default | Blocked, no retry |
| Known + `low_battery_gripper` | default | Validation fails; Approve disabled |
| Known + `hang_navigate` | default, approve, then **Cancel mission** | Shows canceling, then canceled |

**Export JSON** downloads the full run (plans, approvals, events, timeline).

**3. ROS / Synapse path** (with terminals 1 and 2 from above running):

```powershell
wsl -d Ubuntu-24.04 -- node /mnt/c/path/to/hive/bridge/check.mjs   # transport check, expect 8/8
cd backend
.venv\Scripts\python scripts\b_e2e.py        # eight stories through Synapse, expect 8/8
```

Results are saved to `docs/evidence/b-e2e.json`.

**4. Live Qwen** (only with a configured server):

```powershell
curl -X POST http://127.0.0.1:8788/api/exec/planner/probe      # must report "ok": true
cd backend
.venv\Scripts\python scripts\smoke_eval.py --execute            # 10 commands x 2, report in reports/
.venv\Scripts\python scripts\b_e2e.py --planner qwen            # Qwen through the ROS path
```

To check the smoke-evaluation script itself without Qwen, run
`.venv\Scripts\python scripts\smoke_eval.py --planner baseline --execute`. Its results are labeled Baseline.

## Project Structure

- `src/`: React frontend
- `backend/`: Python backend for local ROS 2 command execution
- `scripts/hive.mjs`: combined launcher for backend, Executive and frontend
- `src/executive/`: Executive page
- `backend/hive_exec/`: Executive service (contracts, planner, validator, scheduler, gateways)
- `backend/tests/`: Executive tests
- `ros/`, `bridge/`: Delivery B (ROS 2 fake action servers, Synapse bridge)
- `docs/`: architecture, Delivery B notes, run report

