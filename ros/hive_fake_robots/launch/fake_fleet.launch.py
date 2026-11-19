"""Fake fleet + rosbridge (localhost only).

ros2 launch hive_fake_robots fake_fleet.launch.py hive_backend_dir:=/path/to/hive/backend time_scale:=0.25

rosbridge runs as rosbridge_local (the stock server, but refusing browser handshakes) with the
same nodes and settings as rosbridge_server's rosbridge_websocket_launch.xml.
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("hive_backend_dir", default_value=os.environ.get("HIVE_BACKEND_DIR", "")),
        DeclareLaunchArgument("time_scale", default_value="1.0"),
        DeclareLaunchArgument("rosbridge_port", default_value="9090"),
        Node(
            package="hive_fake_robots", executable="fake_fleet", name="hive_fake_fleet", output="screen",
            parameters=[{"time_scale": LaunchConfiguration("time_scale")}],
            additional_env={"HIVE_BACKEND_DIR": LaunchConfiguration("hive_backend_dir")},
        ),
        Node(
            package="hive_fake_robots", executable="rosbridge_local", name="rosbridge_websocket",
            output="screen",
            # Values where rosbridge_websocket_launch.xml (2.7.1) differs from the script defaults.
            parameters=[{
                "address": "127.0.0.1",
                "port": LaunchConfiguration("rosbridge_port"),
                "retry_startup_delay": 5.0,
                "max_message_size": 10000000,
            }],
        ),
        Node(package="rosapi", executable="rosapi_node", name="rosapi"),
    ])
