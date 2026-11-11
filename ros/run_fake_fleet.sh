#!/usr/bin/env bash
# Start the three fake action servers + rosbridge on 127.0.0.1:9090 (Ubuntu 24.04 + Jazzy).
set -eo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS=${HIVE_ROS_WS:-$HOME/hive_ros_ws}
source /opt/ros/jazzy/setup.bash
source "$WS/install/setup.bash"
export ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-77}
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
exec ros2 launch hive_fake_robots fake_fleet.launch.py \
  hive_backend_dir:="${HIVE_BACKEND_DIR:-$HERE/../backend}" time_scale:="${HIVE_SIM_TIME_SCALE:-1.0}"
