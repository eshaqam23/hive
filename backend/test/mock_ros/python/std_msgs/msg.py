import json


class Bool:
    _type = "std_msgs/msg/Bool"

    def __init__(self):
        self.data = False

    def _yaml(self):
        return f"{{data: {'true' if self.data else 'false'}}}"


class String:
    _type = "std_msgs/msg/String"

    def __init__(self):
        self.data = ""

    def _yaml(self):
        return f"{{data: {json.dumps(self.data)}}}"


class Float32:
    _type = "std_msgs/msg/Float32"

    def __init__(self):
        self.data = 0.0

    def _yaml(self):
        return f"{{data: {float(self.data)}}}"


class Float32MultiArray:
    _type = "std_msgs/msg/Float32MultiArray"

    def __init__(self):
        self.data = []

    def _yaml(self):
        return f"{{data: {[float(v) for v in self.data]}}}"
