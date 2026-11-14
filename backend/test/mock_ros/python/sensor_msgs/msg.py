class Time:
    def __init__(self):
        self.sec = self.nanosec = 0


class Header:
    def __init__(self):
        self.stamp, self.frame_id = Time(), ""


class LaserScan:
    _type = "sensor_msgs/msg/LaserScan"

    def __init__(self):
        self.header = Header()
        self.angle_min = self.angle_max = self.angle_increment = 0.0
        self.time_increment = self.scan_time = self.range_min = self.range_max = 0.0
        self.ranges, self.intensities = [], []

    def _yaml(self):
        return f"{{frame_id: {self.header.frame_id}, ranges: {len(self.ranges)}}}"


class Image:
    _type = "sensor_msgs/msg/Image"

    def __init__(self):
        self.header = Header()
        self.height = self.width = self.step = 0
        self.encoding, self.is_bigendian, self.data = "", 0, b""

    def _yaml(self):
        return f"{{frame_id: {self.header.frame_id}, width: {self.width}, height: {self.height}}}"
