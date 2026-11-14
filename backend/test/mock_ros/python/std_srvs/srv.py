class Trigger:
    _type = "std_srvs/srv/Trigger"

    class Request:
        pass

    class Response:
        def __init__(self, success=False, message=""):
            self.success, self.message = success, message
