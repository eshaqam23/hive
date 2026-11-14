import enum


class ReliabilityPolicy(enum.Enum):
    RELIABLE = 1
    BEST_EFFORT = 2


class HistoryPolicy(enum.Enum):
    KEEP_LAST = 1
    KEEP_ALL = 2


class QoSProfile:
    def __init__(self, depth=10, reliability=ReliabilityPolicy.RELIABLE, history=HistoryPolicy.KEEP_LAST, **_):
        self.depth, self.reliability, self.history = depth, reliability, history


qos_profile_sensor_data = QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT)
