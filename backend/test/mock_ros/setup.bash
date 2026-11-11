# Mock ROS 2 Jazzy environment: lets Hive run end to end without a ROS install.
# Use it by starting the backend with HIVE_ROS_SETUP pointing here (see backend/test/README.md).
HIVE_MOCK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export HIVE_MOCK_ROOT
export ROS_DISTRO=jazzy
export PATH="$HIVE_MOCK_ROOT/bin:$PATH"
mkdir -p "$HIVE_MOCK_ROOT/robots"
