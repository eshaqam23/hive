"""Fake fleet + rosbridge (localhost only).

ros2 launch hive_fake_robots fake_fleet.launch.py hive_backend_dir:=/path/to/hive/backend time_scale:=0.25
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import AnyLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    rosbridge = os.path.join(get_package_share_directory("rosbridge_server"), "launch",
                             "rosbridge_websocket_launch.xml")
    return LaunchDescription([
        DeclareLaunchArgument("hive_backend_dir", default_value=os.environ.get("HIVE_BACKEND_DIR", "")),
        DeclareLaunchArgument("time_scale", default_value="1.0"),
        DeclareLaunchArgument("rosbridge_port", default_value="9090"),
        Node(
            package="hive_fake_robots", executable="fake_fleet", name="hive_fake_fleet", output="screen",
            parameters=[{"time_scale": LaunchConfiguration("time_scale")}],
            additional_env={"HIVE_BACKEND_DIR": LaunchConfiguration("hive_backend_dir")},
        ),
        IncludeLaunchDescription(
            AnyLaunchDescriptionSource(rosbridge),
            launch_arguments={"address": "127.0.0.1", "port": LaunchConfiguration("rosbridge_port")}.items(),
        ),
    ])
