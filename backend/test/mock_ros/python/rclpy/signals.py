import enum


class SignalHandlerOptions(enum.Enum):
    NO = 0
    SIGINT = 1
    SIGTERM = 2
    ALL = 3
