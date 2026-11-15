# Testing Hive without robots

`mock_ros/` is a mock of the parts of ROS 2 Jazzy's `rclpy` that Hive's robot worker uses (node,
publishers, graph queries, subscriber counts), plus `geometry_msgs` and `std_msgs`. Its `setup.bash`
puts them on `PYTHONPATH`, the way the real `/opt/ros/jazzy/setup.bash` does. A robot is visible only
when `ROS_DOMAIN_ID` matches and its IP is in `ROS_STATIC_PEERS`, and only after a discovery delay,
as DDS would scope it. `mock_ros/robots.json` defines three robots:

| Robot | Address | Domain | State |
| --- | --- | --- | --- |
| camera car | 192.168.1.20 | 42 | on, driver listening on /cmd_vel, /gripper_open, /hive/command |
| lidar car | 192.168.1.31 | 43 | powered off |
| camera only | 192.168.1.44 | 44 | on, but nothing listens on /cmd_vel |

Every message a robot receives is written to `mock_ros/robots/<ip>_<domain>.jsonl` with a timestamp (`t`, CLOCK_MONOTONIC seconds, the same clock in every process: WSL2 steps its wall clock by about a second every 30 s, so tests time with `time.monotonic()`),
so tests check what the robot actually got. Put a number in `mock_ros/jitter` (seconds) to randomise
discovery time like real DDS. `robots.json` is re-read on every call, so setting a robot's `online` to
`false` while Hive is running simulates it dropping off the network.

```bash
HIVE_ROS_SETUP="$PWD/backend/test/mock_ros/setup.bash" python3 backend/server.py
```

```bash
python3 backend/test/test_e2e.py backend/test/mock_ros
```

The suite covers connect (including a powered-off robot and a robot with no driver), hold-to-drive
and its 10 Hz rate, press latency, the deadman stop, release, stop-after-forward ordering, rejecting
an out-of-order command, the gripper, the command log, CORS and input validation. It does not prove
DDS behaviour on a real network: run the checklist in the main README with the real robots before
relying on it.

## Live data, services and parameters

The mock also supports what the live-data features need. Robots in `robots.json` gain these keys
(all optional):

| Key | Meaning |
| --- | --- |
| `publishes` | `{topic: {type, hz, width?, height?}}`: LaserScan or Image streams, delivered inside `spin_once` (at most one message per subscription per call, newest due wins). LaserScan has 360 ranges and `frame_id` is the robot IP; Image is a solid colour derived from the IP. |
| `echo_ros_data` | Every `/cmd_vel` Twist the robot receives produces a `/ros_data` String line (`lp,ld,rp,rd`, wheelbase 0.2032, 0.5 m/s is 255). A robot with `line_prefix` (gripper car, `"D"`) prefixes it `D,`. |
| `services` | `{name: srv type}`, e.g. `/gripper/grab` as `std_srvs/srv/Trigger`. |
| `params`, `param_runtime`, `param_revert`, `param_silent` | Declared parameters per node. A node in `params` gets `set_parameters`, `get_parameters` and `list_parameters` services. Setting a parameter fails unless `param_runtime` is true. With `param_revert` a set answers success but the node keeps its default (what `get_parameters` then reports), like a camera that quietly rejects a profile. With `param_silent` the parameter services never answer. Image size does not change when a camera parameter is set. |
| `extra_cmd_vel_publishers` | Added to `count_publishers("/cmd_vel")`, to simulate another controller. |
| (file reads) | `robots.json` is re-read at most every 30 ms. A file caught mid-rewrite (missing or half-written) keeps the last good copy, so tests write it atomically and wait about 0.3 s for a change to settle. |

Extra robots: `192.168.1.70:49` is a camera car with a dozen non-image topics (camera_info, metadata, extrinsics, tf, like the real camera car), for the stream topic cap. `192.168.1.51:48` is a second LiDAR car (same as :45, for cross-talk checks). `192.168.1.50:45` is a LiDAR car (`/scan`, `ros_data` echo) and `192.168.1.60:46` is a
gripper car (Trigger services, `/gripper/position`, prefixed `ros_data`). `192.168.1.44:44` publishes
no `ros_data`.

Mock API for the worker: `create_subscription(msg_type, topic, cb, qos, raw=False)` (bytes when `raw`),
`destroy_subscription`, `count_publishers`, `get_service_names_and_types`, `create_client` with
`wait_for_service` and `call_async`, `rclpy.qos` and `rclpy.serialization` (pickle). Files written under
`mock_ros/robots/`: `<ip>_<domain>.subs.jsonl` (subscription create/destroy), `.params.jsonl`
(parameter sets), `worker_pubs.jsonl` (publisher topic, depth, reliability) and `worker_env.json`
(the worker's environment). Unit tests for the mock: `python3 -m pytest backend/tests/test_mock_ros.py`.

## On Windows

The backend (and the mock) run inside WSL, not Windows Python. From PowerShell, `npm run backend`
starts the backend in WSL. To run the mock-backed suite, open a WSL shell in the repo and use the
commands above; the repo is under `/mnt/c/...` in WSL.
