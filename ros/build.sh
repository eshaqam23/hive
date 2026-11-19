#!/usr/bin/env bash
# Build the HIVE ROS 2 packages in a workspace outside OneDrive. Ubuntu 24.04 + Jazzy.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS=${HIVE_ROS_WS:-$HOME/hive_ros_ws}
mkdir -p "$WS/src"
for p in hive_interfaces hive_fake_robots; do
  rm -rf "$WS/src/$p"; cp -r "$HERE/$p" "$WS/src/$p"
done
set +u; source /opt/ros/jazzy/setup.bash; set -u
cd "$WS" && colcon build --packages-select hive_interfaces hive_fake_robots 2>&1 | tail -5
echo "Built. Use: source $WS/install/setup.bash"
