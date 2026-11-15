# Hive Live Data and Reliable Multi-Car Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive each of three RC cars from its own Hive page over Wi-Fi with confirmed stops, and show any topic each car publishes live (camera, LiDAR, values, generic fallback), without touching Pi code.

**Architecture:** Each car keeps one persistent rclpy worker process (from `rokesh`). The worker gains on-demand raw subscriptions, per-type adapters, `ros_data`-based stop confirmation, gripper interface detection and RealSense runtime tuning. The stdlib HTTP backend fans worker payloads out to the browser over SSE (data) and MJPEG (camera). The React redesign gets a viewer registry, a Live view stage, a live Topics list and a power slider.

**Tech Stack:** Python 3.12 stdlib + rclpy (ROS 2 Jazzy) + numpy + OpenCV (laptop only); React 19 + Vite 7 + Tailwind 4; pytest + coverage; Vitest.

**Spec:** `docs/superpowers/specs/2026-11-13-hive-live-data-design.md` (read it first; this plan argues from it).

## Global Constraints

- No changes to the three robot repos. Pi-side changes are env/firewall only, documented in `docs/robot-setup.md`.
- Backend Python deps: stdlib, rclpy and ROS message packages, numpy, cv2. Nothing else (no `websockets`, no Flask).
- Worker env pins `RMW_IMPLEMENTATION=rmw_fastrtps_cpp` unless `HIVE_RMW` is set.
- Ports: frontend 5173 (dev) / 4173 (preview), backend 8787. Origins allow-list unchanged (`HIVE_ALLOWED_ORIGINS`).
- `/cmd_vel` publisher: reliable, `KeepLast(1)`. Drive stream 10 Hz. Deadman 1.0 s (unchanged).
- Stop: publish immediately + 4 repeats at 50 ms spacing. Confirmation window 500 ms; then resend every 200 ms for up to 5 s.
- Power: float 0.2–1.0, default 0.5. `twist_for`: forward/back `±0.5·p` m/s; turn `±5.0·p` rad/s.
- Camera profiles: low `424x240x6`, medium `424x240x15` (default), high `640x480x15`; `enable_depth=false` by default.
- Sensor subscriptions: best effort, `KeepLast(1)`, raw (serialized) callbacks. Emission caps: image 15 fps, scan 10 Hz, value/generic 5 Hz. `/ros_data` and `/esp_data`: reliable, depth 10.
- Node ≥ 20.19. All Python tests run inside WSL Ubuntu-24.04 (or native Ubuntu) from the repo root.
- Commits: `type: description` (feat|fix|refactor|docs|test|chore|perf|ci). No `Co-Authored-By` or other attribution trailer.
- TDD for every task. ≥ 80% coverage of new and changed code. Frontend state updates are immutable.

## Review Focus

1. Car running without `motor_commands` (no `/ros_data` publisher): stop state must read `unreported`, never `confirmed`. Test in Task 4 (`t_stop_unreported`).
2. Browser tab closed mid-stream: the worker must destroy the subscription within 2 s, so Wi-Fi traffic stops. Test in Task 5 (`t_unwatch_on_disconnect`).
3. Car drops off Wi-Fi mid-stream: the SSE stream emits `status {"stale": true}` after 2 s without data, and the UI shows a stale overlay. Test in Task 5 (`t_stale_on_drop`).
4. Unexpected image encoding or NaN-filled depth: no worker crash. Fall back to a generic payload carrying `note`. Test in Task 1 (`test_unknown_encoding_falls_back`).
5. Two cars publishing the same topic names on different domains: payloads never cross. Test in Task 5 (`t_no_cross_talk_streams`).

---

### Task 1: Message adapters (pure)

**Files:**
- Create: `backend/adapters.py`, `backend/tests/__init__.py`, `backend/tests/test_adapters.py`, `backend/pytest.ini`, `.gitattributes`

**Interfaces:**
- Produces:
  - `kind_for_type(type_name: str) -> str` returns one of `"image" | "scan" | "value" | "generic"`.
  - `adapt(type_name: str, msg: Any) -> dict` returns a payload body: `{"kind", "data"}` for scan/value/generic; `{"kind": "image", "jpeg": bytes, "width", "height", "encoding"}` for images. Never raises: any exception gives `{"kind": "generic", "data": {...}, "note": "<reason>"}`.
  - `IMAGE_TYPES = {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}`.
  - `VALUE_TYPES`: Twist, Odometry, Pose, PoseStamped, and std_msgs Bool/String/Int*/UInt*/Float*.
- Adapters read messages only through attributes and `get_fields_and_field_types()`. They never import ROS, so tests use fake objects.

- [ ] **Step 1: Line endings first.** Create `.gitattributes` with `* text=auto`, `*.sh text eol=lf`, `*.bash text eol=lf`, `*.py text eol=lf`. Run `git add --renormalize . && git commit -m "chore: force LF for scripts run in WSL"`. Expected: `backend/test/mock_ros/setup.bash` has no `\r` (`grep -c $'\r' backend/test/mock_ros/setup.bash` prints 0 after `git checkout -- .`).
- [ ] **Step 2: Write failing tests** in `backend/tests/test_adapters.py`, using `types.SimpleNamespace` fakes plus a helper `fake_msg(fields: dict)` that adds `get_fields_and_field_types`:
  - `test_kind_for_type`: Image→image, CompressedImage→image, LaserScan→scan, `geometry_msgs/msg/Twist`→value, `std_msgs/msg/String`→value, `tf2_msgs/msg/TFMessage`→generic.
  - `test_rgb8_to_jpeg`: 4×2 rgb8 → `cv2.imdecode(payload["jpeg"])` has shape (2, 4, 3); `width == 4`, `height == 2`.
  - `test_bgr8_mono8_mono16_depth`: each of `bgr8`, `mono8`, `mono16`, `16UC1`, `32FC1` (with NaN and inf) decodes to a 2×4 image.
  - `test_compressed_passthrough`: CompressedImage with `format="jpeg"` returns the same bytes.
  - `test_unknown_encoding_falls_back`: `encoding="weird8"` gives `kind == "generic"` and `"note" in payload`.
  - `test_scan_payload`: `ranges=[0.5, inf, nan, 12.3456]`, `range_max=12.0` → `data["ranges_mm"] == [500, None, None, None]`. `angle_min`, `angle_increment`, `range_min`, `range_max` are copied, and there is no intensities key.
  - `test_value_twist`: `data == {"linear.x": 0.25, "linear.y": 0.0, "linear.z": 0.0, "angular.x": 0.0, "angular.y": 0.0, "angular.z": 2.5}`.
  - `test_value_string_bool`: String → `{"data": "OK"}`, Bool → `{"data": True}`.
  - `test_generic_truncates`: a nested message with a 100-item float array → `{"items": [first 32], "total": 100}`; a `bytes` field becomes `{"bytes": <len>}`.
- [ ] **Step 3: Run and see it fail.** Run `python3 -m pytest backend/tests/test_adapters.py -q`. Expected: `ModuleNotFoundError: adapters` (add `pythonpath = backend` in `backend/pytest.ini`).
- [ ] **Step 4: Implement `backend/adapters.py`.**
  - Depth (`16UC1`, `mono16`, `32FC1`): replace NaN/inf with 0, normalise to 0–255, then `cv2.applyColorMap(..., cv2.COLORMAP_TURBO)`.
  - Encode with JPEG quality 70.
  - Range values that are `> range_max`, `< range_min`, inf or NaN become `None`.
- [ ] **Step 5: Run and pass.** Run `python3 -m pytest backend/tests -q --cov=backend --cov-report=term-missing` (use `python3 -m coverage run -m pytest` if pytest-cov is missing). Expected: all pass, `adapters.py` ≥ 90%.
- [ ] **Step 6: Commit.** `git commit -m "feat: add per-type message adapters for live data"`

### Task 2: Robot link logic (pure)

**Files:**
- Create: `backend/robot_link.py`, `backend/tests/test_robot_link.py`

**Interfaces:**
- Produces:
  - `parse_motor_line(line: str) -> MotorLine | None`, where `MotorLine = NamedTuple(kind: str, left_pwm: int, left_dir: int, right_pwm: int, right_dir: int, angles: tuple[float, ...])`.
    - Accepts `L,ld,R,rd`, `D,L,ld,R,rd`, `G,a` and `G,a,b`, with optional trailing `\n`/`\r`.
    - `kind` is `"drive"` or `"gripper"`.
    - Malformed input returns `None`.
  - `is_zero_drive(line: MotorLine | None) -> bool`.
  - `clamp_power(value: Any) -> float`: raises `ValueError` for non-numbers, NaN or bool; clamps to [0.2, 1.0]; `None` gives 0.5.
  - `twist_for(action: str, power: float) -> tuple[float, float]`, with values as in Global Constraints.
  - `detect_gripper(subscribed: set[str], services: set[str]) -> str | None` returns `"trigger"` (both `/gripper/grab` and `/gripper/release`), then `"position"` (`/gripper/position`), then `"bool"` (`/gripper_open`), else `None`.
  - `class StopConfirmer`, with time injected (`now: float` seconds):
    - `stop_requested(now)`.
    - `observe(line: MotorLine | None, now)`.
    - `set_reporting(reporting: bool)`.
    - `poll(now) -> tuple[str, bool]` returns `(state, resend_now)`.
    - `state` is one of `idle | pending | confirmed | unconfirmed | unreported`.
  - `CAMERA_PROFILES = {"low": "424x240x6", "medium": "424x240x15", "high": "640x480x15"}`.
  - `camera_params(quality: str, depth: bool) -> list[tuple[str, object]]` returns `[("enable_depth", depth), ("rgb_camera.color_profile", CAMERA_PROFILES[quality])]`; an unknown quality raises `ValueError`.
  - `profile_variants(profile: str) -> list[str]`, for example `["424x240x15", "424,240,15"]`.

- [ ] **Step 1: Write failing tests.** Exact values:
  - `parse_motor_line("0,1,0,1\n")` → drive (0,1,0,1). `"D,127,1,127,0"` → drive. `"G,120"` → gripper angles (120.0,). `"G,-20,90"` → (-20.0, 90.0). `"OK"` → None.
  - `twist_for("forward", 0.5) == (0.25, 0.0)`, `twist_for("left", 0.5) == (0.0, 2.5)`, `twist_for("right", 1.0) == (0.0, -5.0)`, `twist_for("stop", 0.7) == (0.0, 0.0)`.
  - `clamp_power(0.05) == 0.2`, `clamp_power(3) == 1.0`, `clamp_power(None) == 0.5`. `clamp_power("x")`, `clamp_power(True)` and `clamp_power(float("nan"))` raise `ValueError`.
  - `detect_gripper({"/gripper/position", "/gripper_open"}, {"/gripper/grab", "/gripper/release"}) == "trigger"`. `detect_gripper({"/gripper_open"}, set()) == "bool"`. Empty sets → `None`.
  - StopConfirmer, with reporting on:
    - `stop_requested(0)`, then `poll(0.1)` → `("pending", False)`.
    - A zero line at 0.2 → `poll(0.21)` → `("confirmed", False)`.
    - Without a zero line: `poll(0.5)` → `("unconfirmed", True)`, `poll(0.6)` → `("unconfirmed", False)`, `poll(0.7)` → `("unconfirmed", True)`.
    - `poll(5.1)` → `("unconfirmed", False)`, and resends stop after 5 s.
    - A zero line that arrived *before* `stop_requested` does not confirm.
    - With `set_reporting(False)`, after `stop_requested` → `("unreported", True)` for 5 s at 200 ms spacing.
  - `camera_params("medium", False) == [("enable_depth", False), ("rgb_camera.color_profile", "424x240x15")]`.
- [ ] **Step 2: Run and see it fail.** Run `python3 -m pytest backend/tests/test_robot_link.py -q`. Expected: import error.
- [ ] **Step 3: Implement `backend/robot_link.py`.** No ROS imports.
- [ ] **Step 4: Run and pass.** Run `python3 -m pytest backend/tests -q`. Expected: all pass, `robot_link.py` ≥ 95%.
- [ ] **Step 5: Commit.** `git commit -m "feat: add motor line parsing, stop confirmation and power logic"`

### Task 3: Mock ROS grows subscriptions, services, parameters and robot publishers

**Files:**
- Modify: `backend/test/mock_ros/python/rclpy/__init__.py`, `backend/test/mock_ros/robots.json`, `backend/test/README.md`
- Create: `backend/test/mock_ros/python/rclpy/qos.py`, `rclpy/serialization.py`, `sensor_msgs/__init__.py`, `sensor_msgs/msg.py`, `std_srvs/__init__.py`, `std_srvs/srv.py`, `rcl_interfaces/__init__.py`, `rcl_interfaces/srv.py`, `rcl_interfaces/msg.py`, `backend/tests/test_mock_ros.py`

**Interfaces:**
- Produces the mock API that Tasks 4–6 use:
  - `Node.create_subscription(msg_type, topic, callback, qos, raw=False)`. Delivery happens inside `spin_once`, as `bytes` when `raw=True`.
  - `Node.destroy_subscription(sub)`.
  - `Node.count_publishers(topic)`.
  - `Node.get_service_names_and_types()`.
  - `Node.create_client(srv_type, name)`, with `.wait_for_service(timeout_sec)` and `.call_async(req)` returning a future with `.done()` / `.result()`.
  - `rclpy.qos`: `QoSProfile`, `ReliabilityPolicy`, `HistoryPolicy`, `qos_profile_sensor_data`.
  - `rclpy.serialization.serialize_message` / `deserialize_message` (pickle).
- `robots.json` robot keys gain:
  - `"publishes": {"/scan": {"type": "sensor_msgs/msg/LaserScan", "hz": 7.5}, "/camera/camera/color/image_raw": {"type": "sensor_msgs/msg/Image", "hz": 30, "width": 640, "height": 480}}`.
  - `"echo_ros_data": true`: every received `/cmd_vel` produces a `/ros_data` String line computed with the robots' formula (wheelbase 0.2032, 0.5 m/s → 255).
  - `"services": {"/gripper/grab": "std_srvs/srv/Trigger", ...}`.
  - `"params": {"/camera/camera": {"rgb_camera.color_profile": "640x480x30", "enable_depth": true}}`.
  - `"param_runtime": true|false`.
  - `"extra_cmd_vel_publishers": 0`.
- The mock appends subscription create/destroy events to `robots/<ip>_<domain>.subs.jsonl` and parameter sets to `.params.jsonl`, so e2e tests can read them. `create_publisher` appends `{"topic", "depth", "reliability"}` to `robots/worker_pubs.jsonl`, and `create_node` writes `os.environ` to `robots/worker_env.json`.
- Robots: keep the three existing ones and set the camera car (192.168.1.20:42) to publish image + `ros_data`. Add `192.168.1.50:45` (LiDAR car: `/scan` + `ros_data` echo, `/cmd_vel` subscriber) and `192.168.1.60:46` (gripper car: Trigger services + `/gripper/position` subscriber + `ros_data` echo). Robot 192.168.1.44:44 publishes no `ros_data` (used for the unreported test).

- [ ] **Step 1: Write failing `backend/tests/test_mock_ros.py`.** Under a temporary `HIVE_MOCK_ROOT`:
  - A raw `/scan` subscription receives bytes that deserialize to a LaserScan within 1 s of spinning.
  - `destroy_subscription` stops delivery and logs a `destroy` event.
  - Publishing a Twist (0.25, 0) yields `/ros_data` `"127,1,127,1\n"`.
  - A SetParameters call is logged, and fails with `successful=False` when `param_runtime` is false.
- [ ] **Step 2: Run and see it fail.** Run `python3 -m pytest backend/tests/test_mock_ros.py -q`.
- [ ] **Step 3: Implement the mock extensions.** Generated messages are deterministic: the LaserScan has 360 ranges with `header.frame_id = "<ip>"`, and the Image is a solid colour derived from the IP, so cross-talk is detectable.
- [ ] **Step 4: Run.** Run `python3 -m pytest backend/tests -q`, and also the existing suite: start `HIVE_ROS_SETUP="$PWD/backend/test/mock_ros/setup.bash" python3 backend/server.py &`, then `python3 backend/test/test_e2e.py backend/test/mock_ros`. Expected: new tests pass, and the old e2e is still 20/20.
- [ ] **Step 5: Commit.** `git commit -m "test: extend mock rclpy with subscriptions, services and parameters"`

### Task 4: Reliable control in the worker and API

**Files:**
- Modify: `backend/server.py` (worker `RobotWorkerLoop`, `RobotWorker.start`, routes `handle_connect`, `handle_drive`, `handle_control`, `handle_keepalive`), `backend/test/test_e2e.py`

**Interfaces:**
- Consumes: Task 2 `twist_for`, `clamp_power`, `parse_motor_line`, `is_zero_drive`, `StopConfirmer`, `detect_gripper`; Task 3 mock API.
- Produces:
  - `RobotWorker.start` script exports `RMW_IMPLEMENTATION=${HIVE_RMW:-rmw_fastrtps_cpp}`.
  - `DRIVE_RATE_HZ = 10`.
  - The worker `drive` op accepts `power`.
  - New worker op `{"op": "gripper", "action": "open"|"close"|"position", "value": float|None}`.
  - Every 0.5 s and on change, the worker writes a stdout line `{"link": {"last_line": str|None, "last_line_age_ms": int|None, "stop": state, "reporting": bool, "other_publishers": int}}`. Backend-side `_read_replies` passes lines with a `link` key to `RobotWorker.on_link(link: dict)`, which stores `self.link`.
  - HTTP:
    - `POST /api/robots/drive` body adds `power` (optional). An invalid value gives 400 `{"code": "bad_power"}`.
    - Connect response adds `services: [{"name","type"}]`, `gripper_interface`, `other_publishers`, and topics gain `kind` (from `adapters.kind_for_type`).
    - Keepalive and drive replies include `link`.
    - `POST /api/robots/control` with `gripper_open`/`gripper_close` uses the detected interface (Trigger: open→`/gripper/release`, close→`/gripper/grab`; position: open→0.0, close→1.0; bool: open→true). New action `gripper_position` takes `value` in [0, 1]. With no interface → 409 `{"code": "no_gripper"}`.

- [ ] **Step 1: Write failing e2e checks** in `backend/test/test_e2e.py`. Update `t_stream_rate_and_latency` to expect 8 ≤ rate ≤ 12 Hz, then add:
  - `t_power_scales_twist`: drive forward with `power: 0.5` → the robot log shows `linear.x 0.25`; `power: "fast"` → 400 `bad_power`.
  - `t_keep_last_one`: the worker creates `/cmd_vel` with depth 1 (read from the mock's publisher log).
  - `t_stop_burst`: a stop yields ≥ 5 zero Twists within 300 ms.
  - `t_stop_confirmed`: on the LiDAR car, a stop leads to keepalive/drive `link.stop == "confirmed"` within 1 s, with `last_line` matching `0,1,0,1`.
  - `t_stop_unreported`: on 192.168.1.44:44 (no `ros_data`), `link.stop == "unreported"` and `reporting is False`.
  - `t_gripper_trigger`: on the gripper car, `gripper_close` hits the `/gripper/grab` service (mock log), and the connect response has `gripper_interface == "trigger"`.
  - `t_other_publishers`: with `extra_cmd_vel_publishers: 1`, connect returns `other_publishers == 1`.
  - `t_rmw_pinned`: the worker's environment shows `RMW_IMPLEMENTATION=rmw_fastrtps_cpp` (the mock writes `os.environ` to `robots/worker_env.json` at `create_node`).
- [ ] **Step 2: Run and see the new checks fail.** Restart the mock backend, then run `python3 backend/test/test_e2e.py backend/test/mock_ros`. Expected: the new checks FAIL and the old ones pass.
- [ ] **Step 3: Implement in `backend/server.py`.**
  - Subscribe `/ros_data` (reliable, depth 10) whenever the worker runs. Feed `StopConfirmer.observe(parse_motor_line(...))`.
  - Each tick: `set_reporting(count_publishers('/ros_data') > 0)`. `poll()` drives resends through `publish_twist("stop")`.
  - The stop burst is scheduled on the tick (no sleeps on the worker thread).
  - Drain callbacks with repeated `spin_once(timeout_sec=0)` until none are ready, at most 50 per tick.
  - `other_publishers = count_publishers('/cmd_vel') - 1`.
- [ ] **Step 4: Run and pass.** Run the e2e suite and `python3 -m pytest backend/tests -q`. Expected: all checks pass.
- [ ] **Step 5: Commit.** `git commit -m "fix: confirmed stops, 10 Hz KeepLast(1) drive, power and gripper detection"`

### Task 5: Live sensor streaming (worker → backend → browser)

**Files:**
- Create: `backend/sensor_watch.py` (worker side), `backend/streams.py` (backend side), `backend/tests/test_streams.py`
- Modify: `backend/server.py` (worker ops `watch`/`unwatch`, `_read_replies` routing, GET routes), `backend/test/test_e2e.py`

**Interfaces:**
- Consumes: Task 1 `adapt`, `kind_for_type`; Task 3 mock API; Task 4 `link` lines.
- Produces:
  - `sensor_watch.SensorWatcher(node, emit: Callable[[dict], None], caps: dict[str, float])` with `.watch(topic: str) -> dict` (returns `{"topic", "type", "kind"}`, or raises `KeyError` for an unknown topic), `.unwatch(topic)`, `.watched() -> set[str]`, `.stop()`.
    - The raw callback stores `(bytes, t)` and updates the Hz/bytes-per-second EMAs.
    - A converter thread deserializes the newest message at the capped rate and calls `emit({"stream": {"topic", "type", "kind", "hz", "bytes_per_s", "stamp", "data"|"jpeg_b64", "width", "height", "note"?}})`.
  - The worker's emit uses a module-level `threading.Lock` shared with `reply`/`fail`, so stdout lines never interleave.
  - `streams.StreamHub`:
    - `.subscribe(key, topic) -> Viewer`, `.unsubscribe(viewer)`, `.publish(key, payload)`, `.publish_link(key, link)`, `.viewer_count(key, topic) -> int`, `.latest_jpeg(key, topic) -> tuple[bytes, int] | None` (frame, seq), and `.wait_jpeg(key, topic, after_seq, timeout) -> tuple[bytes, int] | None`.
    - `Viewer.get(timeout) -> dict | None`.
    - Each viewer queue is bounded at 30 events and drops the oldest.
    - The first viewer on `(key, topic)` triggers `on_first(key, topic)`, and the last one leaving triggers `on_last(key, topic)`. The backend wires these to worker `watch`/`unwatch`.
  - `streams.sse_event(event: str, data: dict) -> bytes` and `streams.mjpeg_part(jpeg: bytes) -> bytes` (boundary `hiveframe`).
  - HTTP:
    - `GET /api/robots/stream?ip=&domain_id=&topics=/a,/b` (SSE) sends events `data` (payload), `link` (link dict), `status` (`{"stale": bool, "topic"}` after 2 s without data on a topic) and `: ping` every 10 s.
    - Image-kind topics in `topics` are rejected with 400 `{"code": "use_mjpeg"}`.
  - `GET /api/robots/camera.mjpg?ip=&domain_id=&topic=` (MJPEG).
  - Both GET routes check `Origin` when present, validate ip/domain like POST routes, require an existing connected worker (502 `worker_gone` otherwise), and unsubscribe when the client disconnects (`BrokenPipeError`/`ConnectionResetError`).

- [ ] **Step 1: Write failing unit tests** in `backend/tests/test_streams.py`:
  - `on_first` is called once for two viewers, and `on_last` once after both leave.
  - Queue overflow keeps the newest 30.
  - `wait_jpeg` returns a newer seq.
  - `sse_event("data", {"a": 1}) == b"event: data\ndata: {\"a\": 1}\n\n"`.
  - `mjpeg_part` starts with `b"--hiveframe\r\nContent-Type: image/jpeg\r\nContent-Length: "`.
- [ ] **Step 2: Write failing e2e checks:**
  - `t_sse_scan`: connect to the LiDAR car and open the SSE for `/scan` → a `data` event with `kind == "scan"` and `len(ranges_mm) == 360` within 3 s.
  - `t_mjpeg_frames`: on the camera car, ≥ 5 JPEG parts arrive in 2 s.
  - `t_unwatch_on_disconnect`: close the socket → `subs.jsonl` shows `destroy` within 2 s.
  - `t_stale_on_drop`: set the robot `online: false` mid-stream → `status {"stale": true}` within 3 s.
  - `t_no_cross_talk_streams`: watch `/scan` on 192.168.1.50:45 and on a second mock LiDAR car at 192.168.1.51:47 (add it to `robots.json`) → every payload's `frame_id` equals its own IP.
  - `t_drive_tick_under_load`: hold forward on the camera car with MJPEG open (640×480 at 30 Hz mock) → 95% of `/cmd_vel` intervals fall within 100 ± 20 ms.
  - `t_sse_rejects_image`: 400 `use_mjpeg`.
- [ ] **Step 3: Run and see them fail.** Run `python3 -m pytest backend/tests/test_streams.py -q`, then the e2e suite.
- [ ] **Step 4: Implement `streams.py`, `sensor_watch.py` and the wiring in `server.py`.** Load message classes via `importlib.import_module(pkg + ".msg")` from `"pkg/msg/Type"`. The worker op `watch` replies with the `watch()` dict; `unwatch` replies `{}`.
- [ ] **Step 5: Run and pass.** All unit and e2e checks pass, and `streams.py` + `sensor_watch.py` ≥ 80% (run the e2e under `COVERAGE_PROCESS_START`, set up with `backend/.coveragerc` and `parallel = True`).
- [ ] **Step 6: Commit.** `git commit -m "feat: stream any robot topic to the browser over SSE and MJPEG"`

### Task 6: RealSense runtime tuning

**Files:**
- Modify: `backend/server.py` (worker op `camera`, route `POST /api/robots/camera`, connect/reconnect hook), `backend/test/test_e2e.py`

**Interfaces:**
- Consumes: Task 2 `camera_params`, `profile_variants`; Task 3 parameter services.
- Produces:
  - Worker op `{"op": "camera", "quality": str, "depth": bool}` replies `{"camera": {"node": str|None, "applied": bool, "quality", "depth", "profile": str|None, "reason": str|None}}`.
  - The camera node is found by calling `<node>/list_parameters` with prefix `rgb_camera` on each discovered node that has a `<node>/set_parameters` service, and caching the first node that lists `rgb_camera.color_profile`.
  - Each `profile_variants` entry is tried in order. `enable_depth` is set first.
  - The backend remembers the last requested `(quality, depth)` per robot (default `("medium", False)`) and re-applies it on every successful `connect`, including the frontend's reconnects.
  - `POST /api/robots/camera` takes `{ip, domain_id, quality, depth}` and returns the camera dict.
  - The connect response includes `camera` (or `null` when no RealSense node is found).
  - When `applied` is false, MJPEG for that robot's image topics returns 409 `{"code": "camera_untuned", "reason"}` unless the query has `force_full=1`.

- [ ] **Step 1: Write failing e2e checks:**
  - `t_camera_tuned_on_connect`: connecting to the camera car logs `enable_depth=False` and `rgb_camera.color_profile=424x240x15` in `params.jsonl`.
  - `t_camera_quality_change`: POST quality `low` → `profile == "424x240x6"`.
  - `t_camera_reapplied_on_reconnect`: disconnect and connect → the parameters are set again.
  - `t_camera_refused_blocks_stream`: with `param_runtime: false`, `applied is False` and MJPEG returns 409 `camera_untuned`; with `force_full=1` it streams.
- [ ] **Step 2: Run and see them fail.** Run the e2e suite.
- [ ] **Step 3: Implement.** Service calls are made from the worker thread with futures polled during ticks (5 s timeout per call). The drive tick must not block.
- [ ] **Step 4: Run and pass.** Run the e2e suite plus `t_drive_tick_under_load`. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -m "feat: tune RealSense bandwidth from the laptop at connect time"`

### Task 7: Frontend foundations (pure modules + Vitest)

**Files:**
- Modify: `package.json` (devDependency `vitest@3.2.4`; script `"test": "vitest run"`, `"test:cov": "vitest run --coverage"` with `@vitest/coverage-v8@3.2.4`)
- Create: `src/live/api.js`, `src/live/link.js`, `src/viewers/registry.js`, `src/viewers/lidarGeometry.js`, `src/live/api.test.js`, `src/live/link.test.js`, `src/viewers/registry.test.js`, `src/viewers/lidarGeometry.test.js`

**Interfaces:**
- Produces:
  - `api.js`:
    - `streamUrl(base: string, robot: {host, domainId}, topics: string[]) -> string` (topics comma-joined, URL-encoded).
    - `cameraUrl(base, robot, topic, {forceFull = false} = {}) -> string`.
    - `postCamera(postApi, robot, {quality, depth}) -> Promise<camera>`.
  - `registry.js`:
    - `VIEWERS: Array<{match: {kind?, type?, topic?}, panel: string}>`, where `panel` is one of `"CameraPanel" | "LidarPanel" | "ValuePanel" | "GenericPanel"`.
    - `resolvePanel({kind, type, topic}) -> string` (topic match beats type, type beats kind, fallback `"GenericPanel"`).
    - `stageTopics(topics) -> topics` with kind `image` or `scan`, image first. When both `X` and `X/compressed` exist, only `X/compressed` is kept.
    - `liveListTopics(topics) -> topics` whose kind is not `image`, excluding `/rosout` and `/parameter_events`.
  - `lidarGeometry.js`:
    - `scanToPoints(scan: {angle_min, angle_increment, ranges_mm}, {size: number, maxRangeM: number}) -> Array<{x, y}>`. The canvas centre is the car, forward (angle 0) points up, and nulls are skipped.
    - `nearestObstacle(scan) -> {rangeM, angleDeg} | null`.
  - `link.js`: `describeLink(link: object|null, now: number) -> {level: "ok"|"warn"|"danger"|"muted", text: string}`. Exact copy:
    - `confirmed` → ok, `"Car confirmed <last_line> · <age> ms ago"`.
    - `pending` → muted, `"Stopping…"`.
    - `unconfirmed` → danger, `"Stop not confirmed: car may still be moving"`.
    - `unreported` → warn, `"Car is not reporting motor commands: is motor_commands running?"`.
    - `other_publishers > 0` appends `" · Someone else is also driving this car"`.
    - Also `ddsPorts(domainId: number) -> [number, number]` = `[7400 + 250·d, 7400 + 250·d + 249]` (`ddsPorts(42)` → `[17900, 18149]`), used in the connect-failure hint.
    - `null` → muted, `"No link data yet"`.

- [ ] **Step 1: Install Vitest.** Run `npm install -D vitest@3.2.4 @vitest/coverage-v8@3.2.4` and add the scripts.
- [ ] **Step 2: Write failing tests** with the exact values above. For `scanToPoints`, a scan with `angle_min=0`, `angle_increment=π/2`, `ranges_mm=[1000, null, 1000, 1000]`, `size=200`, `maxRangeM=2` → `[{x:100,y:50},{x:100,y:150},{x:150,y:100}]` (forward up; +angle is counter-clockwise, i.e. left on screen). `nearestObstacle` returns `{rangeM: 1, angleDeg: 0}` for it.
- [ ] **Step 3: Run and see them fail.** Run `npm test`.
- [ ] **Step 4: Implement the four modules.** They are pure: no React, no fetch.
- [ ] **Step 5: Run and pass.** Run `npm run test:cov`. Expected: all pass, each file ≥ 90%.
- [ ] **Step 6: Commit.** `git commit -m "feat: add viewer registry, lidar geometry and link copy modules"`

### Task 8: Live view, panels and car-page integration

**Files:**
- Create: `src/live/useLiveStream.js`, `src/live/LiveView.jsx`, `src/viewers/CameraPanel.jsx`, `src/viewers/LidarPanel.jsx`, `src/viewers/ValuePanel.jsx`, `src/viewers/GenericPanel.jsx`, `src/viewers/panels.js` (maps panel name → component)
- Modify: `src/App.jsx` (`PRESET_ROBOTS` camera topic; `RobotView` hero; `TopicList`; `DrivePad`; drive/keepalive/connect handlers), `src/styles.css` (only if a token is missing)

**Interfaces:**
- Consumes: Task 7 modules; HTTP contracts from Tasks 4–6.
- Produces:
  - `useLiveStream({base, robot, topics, enabled}) -> {payloads: Record<topic, payload>, link, stale: Record<topic, boolean>, error}`. It opens one `EventSource` while `enabled && robot.status === "connected"`, closes it on unmount or change, and updates state immutably.
  - `<LiveView robot topics payloads stale base onForceFull />` renders tabs for `stageTopics(robot.discovery.topics)` plus `"3D model"`. When not connected it shows `RobotViewer`. The footer shows `hz` and `(bytes_per_s/1e6).toFixed(2) MB/s from car`.
  - `CameraPanel` shows `<img src={cameraUrl(...)}>`, a "Stale" overlay after 2 s, and on 409 `camera_untuned` the reason plus a "Stream full size anyway (~27 MB/s, may disrupt driving)" button.
  - `LidarPanel` draws on canvas with `scanToPoints`, range rings every 1 m, and a nearest-obstacle readout.
- `App.jsx` changes:
  - The camera preset topic becomes `/camera/camera/color/image_raw`.
  - Drive and keepalive send `power` from new state `power` (default 0.5).
  - The DrivePad gets:
    - a range input labelled "Power", 20–100%, step 10;
    - a `describeLink` line under the pad;
    - a red banner when `level === "danger"`;
    - a "Camera" Low/Medium/High segmented control plus a "Depth" toggle when `robot.camera` is not null (calls `postCamera`);
    - gripper buttons enabled only when `robot.gripperInterface` is set, with a position slider when it is `"position"`.
  - The `TopicList` rows show kind, Hz and the latest small value from `payloads`, and clicking a row selects it in the LiveView.
  - The connect handler stores `gripperInterface`, `camera`, `services` and `otherPublishers` on the robot, immutably.
  - A connect failure with `code === "not_found"` adds the hint `"Expected ROS domain <d>; the Pi and this laptop must allow UDP <a>–<b>. See docs/robot-setup.md."`, using `ddsPorts`.

- [ ] **Step 1: Write a failing test** in `src/live/useLiveStream.test.js` with a fake `EventSource` injected through a module-level `setEventSourceFactory(fn)`: a `data` event updates `payloads[topic]`; a `status` stale event sets `stale[topic]`; unmount closes the source. Add the devDependency `@testing-library/react@16.3.0` plus `jsdom@26.1.0`, and use `// @vitest-environment jsdom`.
- [ ] **Step 2: Run and see it fail.** Run `npm test`.
- [ ] **Step 3: Implement the hook, the panels, `LiveView` and the `App.jsx` changes** within the existing redesign tokens (bg-card, text-muted, coral accent, font-display headings).
- [ ] **Step 4: Run, build, check in a browser.**
  - `npm test` → pass.
  - `npm run build` → no errors.
  - Start the mock backend (WSL) and `npm run frontend` (Windows). In Chrome at `http://127.0.0.1:5173`, add the three mock cars, connect each, and check: camera frames on the camera car; radar plot on the LiDAR car; the link line reads "Car confirmed …" after a stop; the slider changes `linear.x` in the robot log; the gripper car's buttons hit `/gripper/grab`. Take a screenshot of each car page for the hand-off.
- [ ] **Step 5: Commit.** `git commit -m "feat: show live camera, lidar and topic data on each car page"`

### Task 9: Windows-aware launcher and setup docs

**Files:**
- Create: `scripts/backend-command.mjs`, `scripts/backend-command.test.js`, `docs/robot-setup.md`
- Modify: `scripts/hive.mjs`, `README.md`, `backend/test/README.md`, `package.json` (`"backend": "node scripts/hive.mjs --backend-only"`)

**Interfaces:**
- Produces: `backendCommand({platform, repoDir, env}) -> {cmd: string, args: string[]}`.
  - On Linux: `{cmd: env.HIVE_PYTHON || "python3", args: [<repoDir>/backend/server.py]}`.
  - On `win32`: `{cmd: "wsl.exe", args: [...(env.HIVE_WSL_DISTRO ? ["-d", env.HIVE_WSL_DISTRO] : []), "--cd", toWslPath(repoDir), "--exec", "python3", "backend/server.py"]}`.
  - `toWslPath("C:\\Users\\alice\\Projects\\hive") === "/mnt/c/Users/alice/Projects/hive"`.
- `hive.mjs` uses it, keeps `--backend-only` (from `exec` `9d585c3`), and on win32 does not pass `--open` failure through as fatal.

- [ ] **Step 1: Write failing Vitest cases** for both platforms, the distro override and `toWslPath` (drive letter lower-cased, backslashes converted).
- [ ] **Step 2: Run and see them fail.** Run `npm test`.
- [ ] **Step 3: Implement and wire into `hive.mjs`.** Write `docs/robot-setup.md` with:
  - The per-Pi steps: add `export ROS_DOMAIN_ID=<42|43|44>` to the Pi's start script, optionally `export ROS_STATIC_PEERS=<laptop ip>`, and `sudo ufw allow from <laptop ip> proto udp to any port <7400+250·d>:<7400+250·d+249>` (17900:18149, 18150:18399, 18400:18649).
  - The Windows Hyper-V firewall rule widened to those ranges (`New-NetFirewallHyperVRule`).
  - A note that a changed laptop DHCP IP breaks the Pi rule.
  - The hardware checklist from the spec's Testing section.

  Update the README's run section for Windows and Ubuntu.
- [ ] **Step 4: Run and pass.** Run `npm test`. Then on Windows run `npm run hive` and confirm the backend banner `Hive backend listening on http://127.0.0.1:8787` appears from WSL and the page loads.
- [ ] **Step 5: Commit.** `git commit -m "feat: start the backend in WSL from Windows and document robot setup"`

### Task 10: Real-car verification (with the operator)

**Files:**
- Modify: `docs/robot-setup.md` (record results), `CHANGES.md` (append a section for this branch)

- [ ] **Step 1: Pi setup.** The operator (or Claude over SSH, with the operator's approval) sets domains 42/43/44 and the ufw rules from `docs/robot-setup.md`. The laptop firewall rule is widened.
- [ ] **Step 2: Each car alone, in a clear area.** Connect, drive at 50% power, release, and confirm the link line reads "Car confirmed 0,1,0,1". Check that the car physically stopped. The sensor is visible, and the measured MB/s is recorded.
- [ ] **Step 3: Camera car.** Confirm the RealSense accepted the runtime profile (record the accepted string format). If it is refused, record the reason, and confirm the panel is blocked by default.
- [ ] **Step 4: All three connected at once.** Drive each car in turn and confirm no other car twitches. Note whether control stays responsive with the camera at Medium.
- [ ] **Step 5: Wi-Fi drop test (wheels off the ground).** Kill laptop Wi-Fi mid-drive and record the behaviour (expected: the camera and LiDAR cars keep their last command, a known robot-side limit).
- [ ] **Step 6: Commit.** `git commit -m "docs: record real-car verification results"`
