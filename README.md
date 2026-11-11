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

### Clean start (Windows PowerShell; use `python3` and `.venv/bin/` on Ubuntu)

```bash
npm install
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest -q          # deterministic tests (fake fleet, Baseline, scripted mock)
cd ..
copy .env.example .env                    # then set QWEN_BASE_URL for live Qwen
npm run hive                               # manual-control backend + Executive + Vite
```

`npm run hive -- --no-executive` starts the original app only. The Executive alone runs with
`cd backend && .venv/Scripts/python -m hive_exec`.

### Serving Qwen3-8B (GPU host, not this laptop)

```bash
pip install vllm
vllm serve Qwen/Qwen3-8B --host 127.0.0.1 --port 8000 --max-model-len 12288 --seed 1234 --api-key "$KEY"
ssh -L 8000:127.0.0.1:8000 gpu-host        # from the operator laptop
```

Then set `QWEN_BASE_URL=http://127.0.0.1:8000/v1` and `QWEN_API_KEY` in `.env`. On startup the
Executive probes `/v1/models` plus one schema-constrained request, and enables live generation
only if both pass (`POST /api/exec/planner/probe` re-checks). Smoke evaluation:
`cd backend && .venv/Scripts/python scripts/smoke_eval.py --execute`.

### Demo

1. Executive page: Scenario **Unknown object/route** and **Reset run**.
2. Plan the default command: *Find the red block in the work area, check the pickup corridor, and bring the block to the drop-off area.*
3. Approve revision 1. The camera inspects and the lidar scans concurrently, and the map updates from their reports.
4. Approve revision 2 (transport by `gripper_01`). The mission completes only after confirmed placement.

Fault checkboxes (route blocked, block missing, two red blocks, gripper unavailable or fault after
pickup, navigation fault after pickup, uncertain place, hangs, low battery) apply on the next reset.

## Project Structure

- `src/`: React frontend
- `backend/`: Python backend for local ROS 2 command execution
- `scripts/hive.mjs`: combined launcher for backend, Executive and frontend
- `src/executive/`: Executive page
- `backend/hive_exec/`: Executive service (contracts, planner, validator, scheduler, gateways)
- `backend/tests/`: Executive tests
- `ros/`, `bridge/`: Delivery B (ROS 2 fake action servers, Synapse bridge)
- `docs/`: architecture, Delivery B notes, run report

