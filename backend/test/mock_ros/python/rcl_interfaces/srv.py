class SetParameters:
    _type = "rcl_interfaces/srv/SetParameters"

    class Request:
        def __init__(self):
            self.parameters = []

    class Response:
        def __init__(self):
            self.results = []


class GetParameters:
    _type = "rcl_interfaces/srv/GetParameters"

    class Request:
        def __init__(self):
            self.names = []

    class Response:
        def __init__(self):
            self.values = []


class ListParameters:
    _type = "rcl_interfaces/srv/ListParameters"

    class Request:
        def __init__(self):
            self.prefixes, self.depth = [], 0

    class Response:
        def __init__(self):
            from rcl_interfaces.msg import ListParametersResult
            self.result = ListParametersResult()
