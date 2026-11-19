#!/usr/bin/env bash
# Start the three fake action servers + rosbridge on 127.0.0.1:9090 (Ubuntu 24.04 + Jazzy).
set -eo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS=${HIVE_ROS_WS:-$HOME/hive_ros_ws}
source /opt/ros/jazzy/setup.bash
source "$WS/install/setup.bash"
# Always domain 77. An inherited ROS_DOMAIN_ID (e.g. 42 from ~/.bashrc) is the real cars'
# domain, and the fake fleet must never share it.
if [ -n "${ROS_DOMAIN_ID:-}" ] && [ "$ROS_DOMAIN_ID" != "77" ]; then
  echo "run_fake_fleet: ignoring inherited ROS_DOMAIN_ID=$ROS_DOMAIN_ID; fake fleet uses 77" >&2
fi
export ROS_DOMAIN_ID=77
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
exec ros2 launch hive_fake_robots fake_fleet.launch.py \
  hive_backend_dir:="${HIVE_BACKEND_DIR:-$HERE/../backend}" time_scale:="${HIVE_SIM_TIME_SCALE:-1.0}"
