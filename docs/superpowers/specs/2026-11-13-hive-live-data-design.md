# Hive: live robot data and reliable multi-car control

Date: 2026-11-13
Branch: `feature/live-data`, cut from `origin/rokesh` (`a0d5960`)
Status: awaiting operator approval (builder check done, questions answered)

## Intent

VT CRO has three RC cars, each run by a Raspberry Pi with ROS 2 Jazzy:

| Car | Robot repo | Sensor / actuator | Topics it really uses (repo `main`) |
| --- | --- | --- | --- |
| Camera car | `eshaqam23/cam-bot-ws` | Intel RealSense (`realsense2_camera` `rs_launch.py`, defaults) | subscribes `cmd_vel` (Twist); publishes `/camera/camera/color/image_raw`, `/camera/camera/depth/image_rect_raw`, camera_info, `ros_data`, `esp_data` |
| LiDAR car | `eshaqam23/lid-bot-ws` | Slamtec RPLIDAR A1 (`sllidar_ros2`) | subscribes `cmd_vel`; publishes `/scan` (LaserScan, ~7.5 Hz), `ros_data`, `esp_data` |
| Gripper car | `eshaqam23/gripper-bot-ws` | servo gripper | subscribes `cmd_vel`, `gripper_open` (Bool); publishes `ros_data`, `esp_data` |

The operator wants one laptop app (Hive) that:

1. connects to all three cars at once over Wi-Fi (ROS 2 / DDS, not SSH),
2. gives each car its own page where the operator drives it with on-screen and keyboard controls,
3. shows that car's streamed data live on the same page: camera image, LiDAR scan, gripper state,
4. does this generically: any topic a car publishes is discovered and shown automatically, with a
   specialised view for known types (images, laser scans) and a readable fallback for everything
   else, so a new sensor on any car needs no new Hive code.

**Why:** today Hive only drives (and unreliably), and nothing on screen shows what the cars sense.
The team needs to see and drive the fleet from one place without touching the code running on the
Pis.

## Hard constraints

- **No code changes on the Pis.** The three robot repos stay as they are. Allowed on the Pi side:
  environment/config only (ROS domain ID, static peers, firewall), set up once. Runtime ROS parameter
  changes sent from the laptop (e.g. RealSense resolution) are allowed: the operator approved this.
- **No new installs on the Pis.** Laptop-side installs are fine.
- Data and control travel over Wi-Fi through ROS 2 DDS. SSH is not in the data or control path.
- Laptop: Windows 11 + WSL2 Ubuntu 24.04 (mirrored networking) with ROS 2 Jazzy in WSL; browser on
  Windows. Teammates may run native Ubuntu 24.04. Both must work.
- Wi-Fi is VT campus Wi-Fi: multicast is unreliable, bandwidth is shared. Discovery uses unicast
  static peers.
- Repo rules (operator's global rules): tests before implementation, 80% coverage minimum, prefer
  immutable updates, commit messages `type: description` with no attribution trailer.

## Goals

1. Reliable driving of each car from its page (hold-to-drive, keyboard, stop that is confirmed).
2. Live camera and LiDAR views on each car's page, inside the redesign UI.
3. A generic data pipeline: discovered topic, then type adapter, then viewer, with fallback.
4. Up to three cars connected at the same time without cross-talk.
5. Camera bandwidth that campus Wi-Fi can carry, controlled from the laptop.

## Non-goals

- Changing robot code, launch files or firmware (including the missing `cmd_vel` timeout).
- Auto-starting robot nodes at Pi boot (robot-side setup; may be documented, not built).
- The Executive / LLM planner (`exec` branch) and the React Flow pipeline UI (`pipeline-*` branches).
- Phone layout.
- Live sensor thumbnails on the Home page (would cost Wi-Fi for cars nobody is looking at).
- Mapping / SLAM, recording, playback.

## Anti-goals (would be a failure even if it "works")

- Driving car A moves car B.
- The camera stream starves the drive commands of Wi-Fi.
- Hive shows "stopped" while the car is still moving.
- Adding a new sensor type requires editing `App.jsx` in several places.

## Taking the best from each branch

| Branch | Taken | Left |
| --- | --- | --- |
| `rokesh` (base) | redesign UI (Claude-style dark UI, sidebar, hero, drive pad, Activity log), persistent per-robot rclpy worker, wait-for-discovery, hold-to-drive stream, 1 s deadman, newest-command-wins ordering (`session`/`seq`), stop on backend death, auto-reconnect, origin allow-list, mock-`rclpy` e2e suite (20 checks) | |
| `feature/hive-redesign-and-robot-wiring` | already contained in `rokesh` | |
| `feature/redesign-remote-control` (Adam, 11-13) | nothing in control logic. Check its README for setup notes worth keeping | one-shot publishes with no discovery wait (first presses lost), always reports success, no deadman, CORS `*`, gripper on `/gripper_command` String (robot listens on `/gripper_open` Bool) |
| `feature/remote_control` | already contained in `rokesh` | |
| `exec` | `--backend-only` launcher flag; the idea of a platform-aware launcher (`9d585c3`) | Executive, planner, bridge, fake fleet (out of scope). Its `HIVE_PYTHON=python` default on Windows does not help here because rclpy lives in WSL |
| `pipeline-frontend`, `pipeline-design-ideas` | nothing | separate UI direction |
| `main`, `frontend/all-features` | nothing new | stale |

## Approaches considered

**A. Extend Hive's per-robot worker (chosen by the operator).** The worker process that already
holds one DDS participant per car also subscribes to that car's topics on demand, converts each
message into a browser-friendly payload with a per-type adapter, and streams it to the page. Pros:
one DDS participant per car (least discovery traffic over Wi-Fi), safety and camera settings stay in
one process, no Pi installs, laptop deps already present (numpy, OpenCV, `rosidl_runtime_py`). Cons:
Hive owns the adapter code.

**B. `foxglove_bridge` on the laptop, one per car domain.** Proven generic decoding of any type in
the browser. Rejected: a second DDS participant per car (about double the discovery chatter on campus
Wi-Fi), two systems to keep in sync, and driving, safety and RealSense settings would still have to
go through Hive's backend.

**C. rosbridge + web_video_server.** Rejected: three processes per car, slow JSON for large messages.

## Design

### 1. Network and isolation (setup, no code on the Pis)

- Each car gets its own `ROS_DOMAIN_ID`: camera car 42, LiDAR car 43, gripper car 44 (editable in
  Hive; Hive already auto-assigns from 42). Each car is reached at its own IP via
  `ROS_STATIC_PEERS=<car ip>`; Hive's worker sets `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`. Both are
  needed: the IP alone does not isolate, because any Pi that announces itself to the laptop on the
  same domain would match every Hive publisher on that domain.
- Pi side, once per Pi (environment/config only): export the domain ID in whatever starts the robot's
  launch file; optionally `ROS_STATIC_PEERS=<laptop ip>`. DDS ports move with the domain
  (`7400 + 250 x domain`: 42 → 17900, 43 → 18150, 44 → 18400, each using a small range above the
  base), so the lid-bot Pi's existing `ufw` rule (UDP 7400-7500 from the laptop) and the Windows
  Hyper-V firewall rule `LidBot-ROS2-DDS-from-Pi` must be widened to the new ranges.
- Hive ships a short `docs/robot-setup.md` with the exact per-Pi commands (domain export, ufw rule)
  and the laptop firewall rule, and the robot page shows the expected domain and ports when a
  connect fails with "no nodes answered".
- **RMW pinned.** The worker exports `RMW_IMPLEMENTATION=rmw_fastrtps_cpp` (override with
  `HIVE_RMW`), because the operator's WSL `~/.bashrc` sets Cyclone DDS and domain 42 while the Pis run
  the Jazzy default (Fast DDS), and the backend currently inherits the launching shell's environment.

### 2. Control reliability (backend worker)

Keep everything `rokesh` does (worker, discovery wait, ordering, deadman, stop on backend death,
reconnect) and add:

- **Stale commands are dropped, not replayed.** The `/cmd_vel` publisher uses reliable QoS with
  `KeepLast(1)` (was depth 10), so after a Wi-Fi stall the car gets the newest command, not a backlog
  of old "forward"s.
- **Drive stream at 10 Hz** (was 20). The car firmware has no timeout, so the stream only exists to
  recover lost packets. 10 Hz halves Wi-Fi packets and serial load on the Pi (9600 baud).
- **Stop burst.** A stop publishes a zero Twist immediately and repeats it 4 more times over 200 ms.
- **Closed-loop confirmation from data the cars already publish.** Every car's `motor_commands` node
  publishes the exact serial line it sent to the ESP on `ros_data` and the ESP's reply on
  `esp_data`. The worker subscribes to both whenever a car is connected and parses them:
  - lid/cam: `L,ld,R,rd`; gripper: `D,L,ld,R,rd`, and `G,angle` (`main`) or `G,claw,joint` (newer
    branches). The parser accepts all of these, and lines may end in `\n`.
  - On the newer gripper branches `motor_commands` also resends the last drive line at 10 Hz as a
    heartbeat, so `ros_data` repeats. Confirmation compares content, not arrival.
  - After a stop, if no zero-speed line arrives on `ros_data` within 500 ms, Hive keeps re-sending the
    stop every 200 ms for up to 5 s and shows a red "Stop not confirmed: car may still be moving"
    banner until a zero line arrives. Same idea for drive: the drive pad shows the last line the car
    confirmed and its age ("Car confirmed 127,1,127,1 · 40 ms ago").
  - If `ros_data` is not on the graph (bridge node not running), the page says so plainly instead of
    pretending ("Car is not reporting motor commands: is motor_commands running?").
- **Gripper: auto-detected interface** (my call; reviewer found the gripper Pi most likely runs a
  newer gripper-bot branch, because `main`'s launch file needs `gripper_controller`, which only exists
  on `fix-motor-commands` / `esp32-c6-pins` / `esp32-wifi`). Hive checks the discovered graph in this
  order and drives whichever exists:
  1. services `/gripper/grab` and `/gripper/release` (`std_srvs/srv/Trigger`): Open = release,
     Close = grab (newer branches);
  2. subscriber on `/gripper/position` (`std_msgs/Float32`, 0.0 open to 1.0 closed): Open/Close buttons
     plus a position slider (newer branches);
  3. subscriber on `/gripper_open` (`std_msgs/Bool`): Open/Close (gripper-bot `main`).
  If none exist, the gripper buttons are disabled with the reason shown. Discovery therefore also
  lists services (`get_service_names_and_types`). The mock gains Trigger services.
- **Other operators.** If the worker sees another publisher on the car's `/cmd_vel` (a teammate's
  Hive, teleop on the Pi), the page shows "Someone else is also driving this car". Stop confirmation
  then says "confirmed (other publishers present)" because `ros_data` echoes every publisher. No
  lock or lease. Only one operator per car is supported, and the warning makes a second one visible
  (my call).
- **Speeds (operator decided: half power default, add a slider).** In every car's `motor_commands`,
  0.5 m/s maps to PWM 255, which is full throttle. `rokesh`'s forward (0.5 m/s) is therefore flat out,
  and its turn (1.0 rad/s) is only PWM 51 per wheel, too weak to spin the car. New defaults:
  forward/back 0.25 m/s (PWM ~127), turn 2.5 rad/s (PWM ~129 per wheel, opposite directions). A speed
  slider on the drive pad sets power from 20% to 100% (wheel speed 0.1 to 0.5 m/s), default 50%, and
  scales drive and turn together. The slider value is sent with each drive command and validated
  server-side (clamped to the 20-100% range).

### 3. Generic live data pipeline

```
car (DDS over Wi-Fi) ─► per-car worker: subscribe on demand ─► adapter by type ─► backend ─► browser
                                         (best effort, keep 1)   (adapters.py)     SSE / MJPEG   viewer registry
```

**Discovery** already returns every topic with its type (`connect`). Topics are listed on the car
page with live rate (Hz) and bandwidth once watched.

**Subscribing on demand.** The worker subscribes to a topic only while some page is viewing it, and
destroys the subscription when the last viewer leaves. With no subscriber DDS sends nothing over Wi-Fi.
Sensor subscriptions use `qos_profile_sensor_data` semantics (best effort, `KeepLast(1)`), so lagging
frames are dropped instead of retransmitted. Exception: `ros_data` / `esp_data` use reliable QoS to
match the robot's default publishers.

The worker must not let sensor traffic delay driving. Callbacks only store the newest raw message per
topic; conversion happens at a capped rate per topic (images ≤ 15 fps, scans ≤ 10 Hz, generic ≤ 5 Hz)
on a separate converter thread (OpenCV's encoder releases the GIL), and the main loop drains all
ready callbacks each tick instead of one `spin_once` per tick. **Builder's call** on the exact
threading, as long as the drive tick stays on time (test asserts it, see Testing).

**Adapters** (`backend/adapters.py`, pure functions from message to payload, keyed by ROS type
string; one registry dict; unknown types fall through to `generic`):

| ROS type | Payload kind | Conversion |
| --- | --- | --- |
| `sensor_msgs/msg/Image` | `image` | encodings `rgb8`, `bgr8`, `rgba8`, `bgra8`, `mono8`, `mono16`, `16UC1` / `32FC1` depth (colour-mapped), `yuv422*`; to JPEG (quality ~70) with OpenCV on the laptop |
| `sensor_msgs/msg/CompressedImage` | `image` | pass-through JPEG/PNG bytes |
| `sensor_msgs/msg/LaserScan` | `scan` | `angle_min`, `angle_increment`, `range_min`, `range_max`, ranges as rounded mm ints (inf/NaN → null), intensities dropped |
| `sensor_msgs/msg/PointCloud2` | `points` | x, y, z decimated to ≤ 5000 points. **Builder's call** whether to ship this now or stub it |
| `geometry_msgs/msg/Twist`, `std_msgs/msg/*` scalar/String/Bool | `value` | flat key/value |
| `nav_msgs/msg/Odometry`, `geometry_msgs/msg/Pose*` | `value` | x, y, yaw, speeds |
| anything else | `generic` | `rosidl_runtime_py.message_to_ordereddict`, arrays truncated to 32 items with the total count, byte arrays replaced by their length |

Each payload carries `topic`, `type`, `kind`, `stamp`, measured `hz`, measured `bytes_per_s` (from
the raw message size, so the page shows the real Wi-Fi cost).

**Backend to browser** (stdlib only, no new Python deps):

- `GET /api/robots/stream?ip=…&domain_id=…&topics=/scan,/ros_data` returns Server-Sent Events: one
  JSON event per payload for `scan`, `value`, `generic`, `points`. One stream per open car page.
- `GET /api/robots/camera.mjpg?ip=…&domain_id=…&topic=…` returns
  `multipart/x-mixed-replace` MJPEG for `image` topics, shown with a plain `<img>`.
- Both endpoints apply the same origin allow-list as the POST routes when an `Origin` header is
  present, and both end the worker subscription when the browser disconnects.
- Only the open car page streams, so at most one SSE and one or two MJPEG connections are open,
  inside the browser's 6-connections-per-host limit.
- Worker to backend: payloads travel on the existing stdout JSON-lines pipe (JPEG base64-encoded),
  tagged so they are not confused with request replies. **Builder's call** to switch to
  length-prefixed binary frames if base64 measurably costs too much at 15 fps.

**Frontend viewer registry** (`src/viewers/registry.js`): one table mapping payload `kind`, and
optionally an exact ROS type or topic name, to a React panel:

| kind | Panel | What it shows |
| --- | --- | --- |
| `image` | `CameraPanel` | MJPEG `<img>`, fps, MB/s, resolution, "stale" overlay if no frame for 2 s |
| `scan` | `LidarPanel` | top-down radar plot on canvas, car at centre, range rings, forward up, nearest-obstacle readout |
| `value` | `ValuePanel` | compact key/value readout |
| `points` | `PointsPanel` | top-down scatter (if built) |
| `generic` | `GenericPanel` | collapsible tree, updates in place |

Adding a viewer for a new type = one adapter entry + one registry entry. Nothing else in `App.jsx`
changes.

### 4. Camera bandwidth (operator-approved runtime parameters)

RealSense defaults are colour 640x480 at 30 fps plus depth 848x480 at 30 fps: about 52 MB/s raw,
more than campus Wi-Fi carries, and enough to starve all three cars' drive commands.

- On connect, if the car exposes a node with parameter `rgb_camera.color_profile` (discovered by
  listing parameters, not by hard-coding the node name; expected `/camera/camera`), the worker sets
  over ROS (`SetParameters` service):
  - `enable_depth = false` (saves ~24 MB/s),
  - `rgb_camera.color_profile = 424x240x15` (~4.6 MB/s raw). Default is my call; see Open points.
- The camera panel has a quality picker: Low 424x240 @ 6 fps (~1.8 MB/s), Medium 424x240 @ 15
  (~4.6 MB/s, default), High 640x480 @ 15 (~13.8 MB/s), plus a depth on/off toggle (depth shown
  colour-mapped through the same pipeline). Settings are re-applied on every connect and reconnect,
  because the Pi forgets them when its launch file restarts.
- If the car already publishes `…/image_raw/compressed` (only if image_transport plugins happen to be
  installed on the Pi), Hive prefers it automatically.
- The panel always shows the measured MB/s coming from the car.
- **Must be verified on the real camera car first:** that its installed `realsense2_camera` accepts
  these changes at runtime and the exact profile string format (`424x240x15` vs `424,240,15`). If
  runtime change is refused, fall back to Low-rate viewing (subscribe, display at most 6 fps) and say
  so on the panel. Correction (reviewer): viewing at 6 fps on the laptop saves no Wi-Fi, because DDS
  still delivers every raw frame. So if runtime change is refused, the camera panel is **blocked by
  default** with the reason and the measured cost, and offers an explicit "Stream full size anyway
  (~27 MB/s, may disrupt driving)" button. It is never on automatically (my call).

### 5. UI: the car page inside the redesign

Keep the `rokesh` redesign look (dark Claude-style palette, Newsreader headings, coral accent,
sidebar, hero, drive pad, Activity log). Changes on the car page:

```
← Home                                                         Remove robot
┌──────────────────────── Live view ───────────────────────┐   LiDAR RC Car
│ [ Camera ] [ LiDAR ] [ 3D model ]           ● 7.5 Hz      │   Rover 2
│                                                           │   ● Live · 10.0.0.12 · domain 43
│        camera frame  /  radar plot  /  CAD model          │   ▶ Connected · Ready
│                                                           │   Found 4 nodes, 9 topics
│                                   0.08 MB/s from car      │
└───────────────────────────────────────────────────────────┘
┌ Drive controls ──────────────┐  ┌ Topics (live) ────────────────────────┐
│        ▲ W                   │  │ /scan      LaserScan   7.5 Hz   view  │
│   ◀ A  ■ Space  ▶ D          │  │ /ros_data  String     "0,1,0,1"       │
│        ▼ S                   │  │ /esp_data  String     "OK"            │
│ Car confirmed 0,1,0,1 · 40ms │  │ /tf        TFMessage  generic …       │
│ Gripper  [Open] [Close]      │  └───────────────────────────────────────┘
│ Camera   Low · Med · High    │
└──────────────────────────────┘
Activity (unchanged)
```

- The hero stage (currently the 3D CAD model) becomes a **Live view** with tabs, one per watchable
  sensor topic on that car, plus "3D model". It shows live data when connected and the CAD model when
  not.
- The Topics list becomes live: each row shows rate and latest small value, and clicking a row opens
  it in the Live view (any type, via the registry).
- The drive pad gains the car-confirmed line, the stop-not-confirmed banner, and camera quality for
  cars with a camera.
- Presets fixed to the real topic names: camera car lists `/camera/camera/color/image_raw`. Presets
  only set the expected topics and the 3D model; what is shown comes from discovery.
- Home page unchanged except each card's status reflects real link health.

### 6. Running on this laptop (Windows + WSL) and on Ubuntu

- Ubuntu: `npm run hive`, unchanged.
- Windows: `npm run hive` from PowerShell starts Vite with Windows Node and starts the backend inside
  WSL (`wsl.exe --cd <repo path translated to /mnt/...> python3 backend/server.py`), because rclpy only
  exists in WSL. With mirrored networking the Windows browser reaches `127.0.0.1:8787`.
  `--backend-only` from `exec` kept. Distro name configurable (`HIVE_WSL_DISTRO`, default the WSL
  default).

## Testing

Tests first for each piece (operator rule: TDD, 80% coverage). Coverage scope (my call): 80% of new
and changed code. New frontend logic goes into small modules outside `App.jsx` (registry, LiDAR
geometry, stop-confirmation state, gripper-interface detection) so it can be unit-tested. Backend
adapters are tested in-process. The worker is covered by the e2e suite, with `coverage run` enabled in
the worker subprocess via `COVERAGE_PROCESS_START`.

- **Adapters** (`backend/test/test_adapters.py`, plain pytest, no ROS): each encoding to JPEG
  decodes back to the right size; LaserScan inf/NaN handling and mm rounding; generic truncation;
  unknown type falls to generic.
- **Worker and API e2e** (extend `rokesh`'s mock-rclpy suite): mock gains `create_subscription`,
  sensor message classes, publishers on the mock robots (scan at 7.5 Hz, image, `ros_data` echo of
  each received Twist), and a parameter service. New checks: SSE delivers scans; MJPEG delivers
  frames; subscription is created on first viewer and destroyed after last; stop confirmed via
  `ros_data` echo; unconfirmed stop keeps resending and reports it; `KeepLast(1)`; 10 Hz rate; stop
  burst; RealSense params set on connect and re-set on reconnect; RMW env pinned; drive tick stays
  within ±20 ms while a 15 fps image stream is converted; robots on different domains never receive
  each other's commands.
- **Frontend** (add Vitest): registry resolves kind, then type, then fallback; LiDAR polar-to-canvas
  geometry; stop-not-confirmed state logic.
- **Real hardware checklist** in `docs/robot-setup.md`: per car connect, drive, stop confirmed, sensor
  visible, measured MB/s, two cars at once with no cross-talk, Wi-Fi drop mid-drive (observe
  behaviour: the car keeps its last command, which is a known robot-side limit).

## Known limits (robot side, not fixable from Hive)

- Camera and LiDAR car firmware and `motor_commands` have no command timeout: if Wi-Fi drops
  mid-drive the car keeps its last command. (The newer gripper-bot branches do have a firmware drive
  timeout plus a 10 Hz bridge heartbeat. But the heartbeat resends the last command, so a dropped link
  still keeps that car moving until the bridge itself stops.) Hive mitigates (hold-to-drive, KeepLast(1), stop burst, confirmed stop, low
  default speed) but cannot guarantee a stop over a dead link.
- lid-bot `motor_commands` hard-codes `/dev/ttyUSB0`, which can collide with the LiDAR depending on
  boot order (existing Pi-side workaround `~/lidbot_fix_ports.sh`).
- gripper-bot `main` launch file references a `gripper_controller` package that is not on `main`, so
  `start_rc.launch.py` fails as written. Which branch actually runs on the gripper Pi is unknown.
  Hive only depends on `/gripper_open` (Bool) being subscribed.

## Open points (my calls unless the operator says otherwise)

- Default camera profile Medium (424x240 @ 15). Lower if three cars share weak Wi-Fi.
- Domains 42 / 43 / 44 for camera / LiDAR / gripper car.
- 10 Hz drive stream, 5-message stop burst, 500 ms confirmation window.
- Gripper interface auto-detected (services, then position topic, then Bool).
- Camera blocked (with explicit override) if RealSense refuses runtime profile change.
- Second operator on a car: warning only, no lock.
- Coverage: 80% of new and changed code.

## Left to the builder

Threading inside the worker, exact SSE event schema, panel styling details within the redesign
tokens, whether `PointCloud2` ships now, base64 vs binary worker frames, Vitest setup details.
