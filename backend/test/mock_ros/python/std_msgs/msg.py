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
