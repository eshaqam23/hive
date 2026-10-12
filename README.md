# Hive

Hive is an early front-end prototype for controlling and monitoring a ROS 2 robot fleet from one operator laptop.

The current version is intentionally minimal and frontend-only. It uses simulated robot profiles, fake connection states, and placeholder topic data so the interface can be designed before the backend ROS bridge exists.

## Current Features

- Fleet dashboard with seeded demo robots
- Add Robot modal for creating local robot profiles
- Morphology presets:
  - Quadcopter
  - Manipulator
  - Mobile manipulator
  - RC car
  - Treaded car
- Robot profile pages with capabilities and ROS 2 topic lists
- Simulated connect/disconnect behavior
- Simulated topic viewer for image, telemetry, and raw message topics
- Fleet command page with a ChatGPT-style command box
- Local persistence with `localStorage`

## Tech Stack

- JavaScript
- React
- Vite
- CSS

## Requirements

- Node.js
- npm

This project was built and tested with Node `v24.18.0` and npm `11.16.0`.

## Install

```powershell
npm install
```

## Run

Start the app with:

```powershell
npm run hive
```

This starts the Vite dev server and opens Hive in the browser.

You can also run:

```powershell
npm run dev
```

If you want to launch the app by typing `hive`, link the local package once:

```powershell
npm link
```

Then start it with:

```powershell
hive
```

## Build

```powershell
npm run build
```

## Current Scope

Hive does not yet connect to real robots. The current connection, topic subscription, and command behavior are mocked in the browser.

The eventual backend should provide real implementations for:

- Connecting to a robot over the network
- Discovering or validating ROS 2 topics
- Subscribing to sensor data
- Publishing control commands
- Translating fleet-level LLM commands into robot-specific actions

The front end is designed so those future backend calls can replace the simulated behavior without changing the main screens.
