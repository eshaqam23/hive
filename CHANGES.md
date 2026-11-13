# Hive: changes on this branch, and what is left

Branch `feature/hive-redesign-and-robot-wiring`, starting from `main` at `9a8652b` ("Updated gripper
topic"). This file records what changed in the front end and the backend, how it was checked, and
what still needs doing before Hive is trusted with real robots.

**The most important caveat first:** everything robot-facing was tested against a mock of the ROS 2
`ros2` CLI, not real ROS 2. ROS 2 was not installed on the development Mac. Run the checklist in
[What to do next](#what-to-do-next) with the real robots before relying on any of it.

---

## Update: merged with `feature/remote_control` (persistent ROS 2 workers)

Branch `feature/redesign-remote-control` combines this branch's front end with the persistent-worker
backend from `feature/remote_control`, which removes the ~1 s discovery delay listed under "Should do"
below. The API, its request and response shapes, and the safety behaviour the front end relies on are
unchanged; only how the backend reaches the robot changed.

- **Backend:** one worker process per robot with a long-lived `rclpy` node, instead of a `ros2` CLI
  call per command. A press reaches the robot in milliseconds; `/cmd_vel` streams at 20 Hz (was 10).
  Connect reads the worker's ROS graph (nodes, topics, `/cmd_vel` subscribers); the deadman and stop
  now run in the worker, which also stops the robot if the backend dies.
- **Ordering:** "newest command wins" is now enforced with command numbers: the page sends `session` and
  `seq` with drive and gripper commands, and the backend rejects an older one with `409` (the front end
  already handles `superseded`). Driving and the gripper are ordered separately.
- **Front end:** unchanged apart from sending `session` and `seq`. `vite.config.js` pins ports 5173 and
  4173 (`strictPort`), because the backend only accepts those origins.
- **Tests:** the mock is now a mock of `rclpy` (`backend/test/mock_ros/python/`) instead of the `ros2`
  CLI, with the same three robots and message log. The suite has 20 checks (added: press latency and
  20 Hz rate, an out-of-order command rejected, drive and gripper ordered independently); 20 of 20 pass.
  Also checked in a browser: connect, hold Forward, hold W, window blur and tab close stop, gripper,
  command log, disconnect, and a `kill -9` of the backend mid-drive (the worker stopped the robot).
- **Requirements:** Node 20.19+ (Vite 7, Tailwind v4); Ubuntu 24.04's `apt` Node 18 is too old.

Sections below describe the original redesign; where they mention `ros2 topic pub`, the worker now
does the equivalent with `rclpy`.

---

## Summary

| Area | State |
| --- | --- |
| Front end look | Rebuilt: the Claude app's design (dark mode), with a few Spotify touches |
| 3D models | Real ROS / Gazebo CAD, shown in an RViz-style viewer you can orbit, pan and zoom |
| Backend | Rewritten for real robots: safe stop ordering, hold-to-drive, deadman, real connect checks |
| Front end ↔ backend wiring | Rewired to the new backend, tested end to end in a browser against the mock |
| Tests | `backend/test/`: mock `ros2` CLI, three simulated robots, a 16-check end-to-end suite |
| Real robots | **Not yet tested** |
| Phone layout | Not worked on, by request |

---

## Backend (`backend/server.py`)

### Bugs fixed

Measured against the original code with the end-to-end suite: **4 of 16 checks passed before, 16 of
16 after.**

1. **A stop could arrive before the drive command it was meant to stop.** Each press started its own
   `ros2 topic pub`, and each takes about a second to discover the robot. With realistic discovery
   delays, Forward then Stop left the robot driving forward in 4 of 6 trials. Each robot now has one
   command channel per topic group; a new command kills any older process still in flight, so the
   newest command always wins (6 of 6 correct afterwards).
2. **Connect reported Live for a robot that was switched off.** `ros2 node list` exits 0 even when it
   finds nothing. Connect now requires at least one node to answer.
3. **Any website open in the browser could drive the robot.** The API answered every origin with
   `Access-Control-Allow-Origin: *`. Only Hive's own origins are allowed now (`HIVE_ALLOWED_ORIGINS`);
   others get 403.
4. **Driving a robot whose driver was not running hung for 8 seconds,** then said "ROS command timed
   out." `--once -w 1` without `--max-wait-time-secs` waits forever. It now fails in about 4 seconds
   with "Nothing on the robot is listening on /cmd_vel".
5. **A non-object JSON body crashed with a 500.** Bodies are now validated (object, valid JSON, 64 KB
   limit) and return 400 or 413.
6. **Connect used the `ros2` daemon,** which can remember an old peer list. Discovery now runs with
   `--no-daemon --spin-time 2.5`.
7. **Commands ran in a login shell** (`bash -lc`), so the operator's own profile could change the ROS
   environment. Now `bash -c`.
8. **Shutting the backend down left robots running.** Ctrl+C / SIGTERM now stops every robot it was
   driving, then exits.

### Added

- **Hold-to-drive streaming:** `POST /api/robots/drive` streams `/cmd_vel` at 10 Hz
  (`ros2 topic pub -r 10 -w 1 --max-wait-time-secs 4`) until stop.
- **Deadman:** `POST /api/robots/drive/keepalive` every 250 ms while driving; if keepalives stop for
  1 s, the backend stops the robot itself.
- **Real connect:** `ros2 node list`, `topic list -t` and `topic info /cmd_vel` run in parallel;
  returns nodes, topics and the `/cmd_vel` subscriber count, and warns if nothing listens.
- **Status:** `POST /api/robots/status`, used every 20 s to notice a robot that went quiet.
- **Command log delivery:** `POST /api/robots/command` publishes the text as `std_msgs/msg/String` on
  `/hive/command` (`HIVE_COMMAND_TOPIC`). Text is JSON-escaped and shell-quoted; quotes, `$` and
  backticks arrive intact.
- **Disconnect:** `POST /api/robots/disconnect` stops the robot before letting it go.
- **Health:** `GET /api/health` reports whether the ROS 2 setup script exists.
- **Clear errors:** ROS 2 missing (503), robot not answering or nothing listening (502), replaced by
  a newer command (409), timeout (504).

The original `POST /api/robots/control` (one-shot actions and the gripper) still works.

---

## Front end (`src/`)

### Design

- **Claude app layout:** a flat sidebar with an "Add robot" row in place of Claude's "New chat", Home
  and Command log, the Robots list (All / Live / Offline filters), and a backend status footer.
  Claude's dark palette (`#262624`), Newsreader serif for headings and Hive's replies, coral as the
  one accent.
- **Spotify touches:** a play button that appears on card hover (connect / disconnect), signal bars
  for live robots, a floating drive dock on Home.
- **Kept as approved:** the hexagon logo, the hero with its buttons and stats (description shortened
  to one line), the coral command-log callout, the drive controls beside topics and capabilities,
  and the Robots list with its filters.
- **Tailwind CSS v4** added (`vite.config.js`, `src/styles.css`). three.js is split into its own chunk.
- Design tokens and copy came from the design-taste-frontend skill (copy linted: 0 fail, 0 warn);
  reviewed against Vercel's web interface guidelines.

### 3D models

- **Real CAD, not stylised models:** the NXP MR-Buggy3 Gazebo model (Rudis Laboratories, CC BY 4.0)
  as the car, with real payloads: an Intel RealSense D435 (camera car), a TurtleBot3 LDS-01 lidar
  (LiDAR car), and an OpenMANIPULATOR-X arm (gripper car), all Apache 2.0.
- Converted from ~110 MB of Collada / STL to ~713 KB of meshopt-compressed GLB in `public/models/`,
  stored in ROS coordinates (x forward, y left, z up). Licences and changes are recorded in
  `public/models/CREDITS.md`, with the Apache 2.0 text alongside.
- **RViz-style viewer** (`src/RobotViewer.jsx`) on each robot's page: left-drag orbit, right-drag pan,
  scroll zoom, front / side / top views, zoom buttons, a 10 cm grid and a ROS axis triad (x red,
  y green, z blue). Renders only while moving.
- Cards, preset cards, sidebar thumbnails, the dock and the Add dialog show snapshots of the same
  models, drawn by one shared offscreen renderer (one WebGL context for the whole page).

### Wiring to the backend (bugs fixed)

1. **Driving is hold-to-drive:** pointer down / up on the pad and dock buttons, Space or Enter held on
   a focused button, or holding W A S D / arrows. Keepalive every 250 ms while held.
2. **Stops on anything that takes your hands off the controls:** release, Stop or Space, the window
   losing focus, the tab being hidden, or the page closing (a `sendBeacon` stop).
3. **Connect shows what it found:** "Found N nodes and M topics on domain D", and an amber warning if
   nothing listens on `/cmd_vel`.
4. **Disconnect stops the robot** instead of only changing the label.
5. **Lost contact:** connected robots are checked every 20 s and marked "Lost contact" if they stop
   answering.
6. **The command log really sends:** to each connected robot, with a delivered / not delivered line
   per robot. It used to show "executing" without sending anything.
7. **A reload no longer shows robots as Live** without checking; every robot starts disconnected.
8. **ROS domains are never silently reassigned.** Loading only repairs invalid IDs; the Add dialog
   refuses a domain another robot already uses, and says which robot has it.
9. **Clear errors** when the backend is not running ("Can't reach the Hive backend … npm run backend")
   or ROS 2 is missing (shown in the sidebar footer).
10. IP format checked in the Add dialog; `setPointerCapture` failures can no longer block a press.

---

## How it was checked

- **Backend:** `backend/test/test_e2e.py` against the mock in `backend/test/mock_ros/`, which scopes
  discovery by `ROS_DOMAIN_ID` and `ROS_STATIC_PEERS` and records every message each simulated robot
  receives. 16 of 16 pass. The stop-ordering race was also run with randomised discovery delay.
- **Front end:** driven in a browser against the same mock: connect (running, powered off, no driver),
  holding Forward by pointer, holding W, losing focus mid-drive, the command log, disconnect, a robot
  dropping off mid-session, and a duplicate domain. Each was checked against what the simulated robot
  received, not only what the page showed. No JavaScript errors on a fresh load.
- **Builds:** `npm run build` passes.

Re-run the backend tests:

```bash
HIVE_ROS_SETUP="$PWD/backend/test/mock_ros/setup.bash" python3 backend/server.py
```

```bash
python3 backend/test/test_e2e.py backend/test/mock_ros
```

---

## What to do next

### Must do before trusting it with robots

- [ ] **Test with real ROS 2 and a real robot,** wheels off the ground, using "First run with the real
      robots" in `README.md`: connect, hold Forward and release, hold W and click away, close the tab
      mid-drive (deadman), disconnect.
- [ ] **Check DDS discovery on the real network.** The laptop uses `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`
      plus `ROS_STATIC_PEERS=<robot IP>`; confirm each robot can discover the laptop back (default
      `SUBNET` discovery, or `ROS_STATIC_PEERS=<laptop IP>` on the robot).
- [ ] **Confirm each robot runs a driver on `/cmd_vel`** and is on the domain entered in Hive.
- [ ] **Add a `/cmd_vel` timeout on the robot side.** Releasing a control stops the stream at once,
      but the zero Twist takes about a second to arrive (each `ros2` call rediscovers the robot).

### Should do

- [ ] **Persistent ROS bridge per robot** (an `rclpy` node kept alive by the backend) to remove the
      ~1 s discovery delay on every command. This would make stops near-instant and driving feel
      responsive. Not built yet because it could not be tested without ROS 2.
- [ ] **A node on the robot that acts on `/hive/command`.** Hive now delivers the text; nothing on the
      robot interprets it yet.
- [ ] **Tune the payload mounts** in `src/robotModels.js`: the RealSense on the camera car may sit
      inside the top deck, and the lidar needs checking the same way.
- [ ] **The gripper arm** shows in flat grey and is oversized in its straight-out pose; fold it and
      give it its real dark colours.
- [ ] **Make the models larger in their cards.**
- [ ] **Check the viewer controls by hand** in a desktop browser with motion allowed (orbit, pan,
      zoom, view buttons). The test browser forced reduced motion and could not drag.

### Could do

- [ ] Real camera and lidar topic viewers (the Topics list shows names and types only).
- [ ] Speed control instead of the fixed `±0.5` linear and `±1.0` angular.
- [ ] Authentication if the backend is ever exposed beyond `127.0.0.1` (the origin check only protects
      a backend bound to localhost).
- [ ] Phone layout (skipped by request).
- [ ] Remove `public/models/` meshes you do not use, if a preset is dropped.

---

## Files changed

| File | Change |
| --- | --- |
| `backend/server.py` | Rewritten (see Backend) |
| `backend/test/` | New: mock `ros2` CLI, simulated robots, end-to-end suite, README |
| `src/App.jsx` | Rebuilt UI and backend wiring |
| `src/RobotViewer.jsx` | New: RViz-style 3D viewer |
| `src/robotModels.js` | New: model loading, preset assembly, snapshots |
| `src/styles.css` | Rewritten for Tailwind v4 and the Claude design tokens |
| `public/models/` | New: CAD models (GLB), credits, Apache 2.0 licence |
| `index.html` | Fonts, title, description, theme colour |
| `vite.config.js` | New: React and Tailwind plugins, three.js chunk |
| `package.json`, `package-lock.json` | Added `three`, `tailwindcss`, `@tailwindcss/vite` |
| `README.md` | How Hive talks to robots, safety, robot requirements, configuration, API, first-run checklist |
| `.gitignore` | Ignores the mock's runtime logs |
| `CHANGES.md` | This file |
