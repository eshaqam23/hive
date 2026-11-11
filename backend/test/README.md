# Testing Hive without robots

`mock_ros/` is a mock of the ROS 2 Jazzy `ros2` CLI, faithful at the interface Hive uses (flags and
messages follow ros2cli on the jazzy branch). A robot is visible only when `ROS_DOMAIN_ID` matches
and its IP is in `ROS_STATIC_PEERS`, exactly as DDS would scope discovery. `mock_ros/robots.json`
defines three robots:

| Robot | Address | Domain | State |
| --- | --- | --- | --- |
| camera car | 192.168.1.20 | 42 | on, driver listening on /cmd_vel, /gripper_open, /hive/command |
| lidar car | 192.168.1.31 | 43 | powered off |
| camera only | 192.168.1.44 | 44 | on, but nothing listens on /cmd_vel |

Every message a robot receives is written to `mock_ros/robots/<ip>_<domain>.jsonl` with a timestamp,
so tests check what the robot actually got. Put a number in `mock_ros/jitter` (seconds) to randomise
discovery time like real DDS.

```bash
HIVE_ROS_SETUP="$PWD/backend/test/mock_ros/setup.bash" python3 backend/server.py
```

```bash
python3 backend/test/test_e2e.py backend/test/mock_ros
```

The suite covers connect (including a powered-off robot and a robot with no driver), hold-to-drive,
the deadman stop, release, stop-after-forward ordering, the gripper, the command log, CORS and input
validation. It does not prove DDS behaviour on a real network: run the checklist in the main README
with the real robots before relying on it.
