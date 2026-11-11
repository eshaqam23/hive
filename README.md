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
- Command log that sends text to each connected robot on `/hive/command`
- Hold-to-drive with keyboard (W A S D / arrows, Space to stop) and a backend deadman
- RViz-style 3D viewer of each robot's CAD model (orbit, pan, zoom, grid, axes)
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

## How Hive talks to a robot

The backend drives each robot with the `ros2` CLI, scoped to that one robot:

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=<ROBOT_DOMAIN_ID>
unset ROS_LOCALHOST_ONLY
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_STATIC_PEERS='<ROBOT_IP>'
```

Each robot profile stores its own ROS domain ID (auto-assigned from `42`, editable when adding, and
unique per robot), so several robots can all use `/cmd_vel` without hearing each other.

| Action | What the backend runs |
| --- | --- |
| Connect | `ros2 node list`, `ros2 topic list -t` and `ros2 topic info /cmd_vel`, in parallel, with `--no-daemon --spin-time 2.5`. Fails if no node answers; warns if nothing subscribes to `/cmd_vel`. |
| Drive (hold) | `ros2 topic pub -r 10 -w 1 --max-wait-time-secs 4 /cmd_vel geometry_msgs/msg/Twist ...` for as long as the control is held |
| Stop / release | kills the stream, then `ros2 topic pub --once -w 1 /cmd_vel` with a zero Twist |
| Gripper | `ros2 topic pub --once -w 1 /gripper_open std_msgs/msg/Bool "{data: true\|false}"` |
| Command log | `ros2 topic pub --once -w 1 /hive/command std_msgs/msg/String "{data: ...}"` |
| Status (every 20 s) | `ros2 node list --no-daemon`; a robot with no nodes is marked Lost contact |

Velocities: forward and backward `linear.x = ±0.5`, rotation `angular.z = ±1.0`.

### Safety

- **Hold to drive.** `/cmd_vel` streams at 10 Hz only while a control (button, or W A S D / arrows) is held. Releasing it, pressing Stop or Space, the window losing focus, or the tab closing all stop the robot.
- **Deadman.** While driving, the page sends a keepalive every 250 ms. If keepalives stop for 1 s (browser crash, closed laptop, dropped Wi-Fi), the backend stops the robot itself.
- **Newest command wins.** Each robot has one command channel; a new command kills any older one still in flight, so a stop can never be overtaken by a drive command that was still discovering the robot.
- **Disconnect stops the robot** before letting it go, and stopping the backend (Ctrl+C) stops every robot it is driving.
- **Only Hive can call the backend.** Requests from other web origins are refused (set `HIVE_ALLOWED_ORIGINS` to change the list), so another website open in your browser cannot drive a robot.

## What each robot needs

1. ROS 2 Jazzy (or another distro whose DDS can discover Jazzy), on the same network as the laptop.
2. `ROS_DOMAIN_ID` set to the domain you entered for it in Hive.
3. A driver node subscribed to `/cmd_vel` (`geometry_msgs/msg/Twist`). Hive's drive commands do nothing without one, and Connect will warn you.
4. Optional: a subscriber on `/gripper_open` (`std_msgs/msg/Bool`) for the Gripper RC Car, and on `/hive/command` (`std_msgs/msg/String`) to receive the command log's text. Hive only delivers the text; a node on the robot has to act on it.
5. Discovery that reaches the laptop: either the robot's default discovery (`ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET`) or `ROS_STATIC_PEERS=<laptop IP>`.
6. A `/cmd_vel` timeout on the robot side is still a good idea: a zero Twist takes about a second to arrive, because each `ros2` call discovers the robot again.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `HIVE_ROS_SETUP` | `/opt/ros/jazzy/setup.bash` | ROS 2 setup script the backend sources |
| `HIVE_BACKEND_HOST` / `HIVE_BACKEND_PORT` | `127.0.0.1` / `8787` | Where the backend listens |
| `HIVE_ALLOWED_ORIGINS` | `http://127.0.0.1:5173,http://localhost:5173` (and `:4173`) | Web origins allowed to use the API |
| `HIVE_COMMAND_TOPIC` | `/hive/command` | Topic for the command log's text |
| `VITE_HIVE_API_URL` | `http://127.0.0.1:8787/api` | Where the front end finds the backend |

## API

All routes are `POST` with a JSON body of `{"ip": "<robot IP>", "domain_id": <n>, ...}` except
`GET /api/health`, which also reports whether the ROS 2 setup script exists.

| Route | Extra fields | Returns |
| --- | --- | --- |
| `/api/robots/connect` | | `nodes`, `topics`, `cmd_vel_subscribers`, `warnings` |
| `/api/robots/status` | | `nodes` |
| `/api/robots/drive` | `action`: `forward` `backward` `left` `right` `stop` | `streaming` |
| `/api/robots/drive/keepalive` | | `streaming` |
| `/api/robots/control` | `action`: a drive action, `stop`, `gripper_open`, `gripper_close` (one-shot) | `topic` |
| `/api/robots/command` | `text` (up to 500 characters) | `topic` |
| `/api/robots/disconnect` | | `stopped` |

Errors come back as `{"ok": false, "error": "..."}` with a status code: `400` bad input, `403` origin
refused, `409` replaced by a newer command, `502` the robot did not answer or nothing listened,
`503` ROS 2 not installed, `504` timed out.

## Testing without robots

`backend/test/` has a mock of the `ros2` CLI with three simulated robots and an end-to-end suite
(16 checks) that verifies what each simulated robot actually received. See `backend/test/README.md`.

## First run with the real robots

1. `npm run hive`, and check the sidebar reads "Backend online · ROS 2 ready".
2. Add the robot with its IP and the domain it runs on; press Connect. You should see "Found N nodes and M topics", and no `/cmd_vel` warning.
3. With the wheels off the ground, hold Forward for a second and release: the wheels should turn, then stop.
4. Hold W, then click outside the browser window: the robot should stop.
5. Hold a direction and close the tab: the robot should stop within about a second (deadman).
6. Press Disconnect: the robot should stop.

## Project Structure

- `src/`: React frontend
- `backend/`: Python backend for local ROS 2 command execution
- `backend/test/`: mock `ros2` CLI and end-to-end tests
- `public/models/`: the robots' CAD models (GLB) and their credits
- `scripts/hive.mjs`: combined launcher for backend and frontend

