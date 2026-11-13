class Vector3:
    def __init__(self):
        self.x = self.y = self.z = 0.0


class Twist:
    _type = "geometry_msgs/msg/Twist"

    def __init__(self):
        self.linear, self.angular = Vector3(), Vector3()

    def _yaml(self):
        return f"{{linear: {{x: {float(self.linear.x)}}}, angular: {{z: {float(self.angular.z)}}}}}"
