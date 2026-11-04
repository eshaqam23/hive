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

## Project Structure

- `src/`: React frontend
- `backend/`: Python backend for local ROS 2 command execution
- `scripts/hive.mjs`: combined launcher for backend and frontend

