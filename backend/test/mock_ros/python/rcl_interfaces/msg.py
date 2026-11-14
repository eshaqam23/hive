class ParameterType:
    PARAMETER_NOT_SET = 0
    PARAMETER_BOOL = 1
    PARAMETER_INTEGER = 2
    PARAMETER_DOUBLE = 3
    PARAMETER_STRING = 4


class ParameterValue:
    def __init__(self, type=0, bool_value=False, integer_value=0, double_value=0.0, string_value=""):
        self.type, self.bool_value, self.integer_value = type, bool_value, integer_value
        self.double_value, self.string_value = double_value, string_value

    def python(self):
        return {1: self.bool_value, 2: self.integer_value, 3: self.double_value, 4: self.string_value}.get(self.type)

    @classmethod
    def of(cls, value):
        if isinstance(value, bool):
            return cls(ParameterType.PARAMETER_BOOL, bool_value=value)
        if isinstance(value, int):
            return cls(ParameterType.PARAMETER_INTEGER, integer_value=value)
        if isinstance(value, float):
            return cls(ParameterType.PARAMETER_DOUBLE, double_value=value)
        return cls(ParameterType.PARAMETER_STRING, string_value=str(value))


class Parameter:
    def __init__(self, name="", value=None):
        self.name, self.value = name, value or ParameterValue()


class SetParametersResult:
    def __init__(self, successful=False, reason=""):
        self.successful, self.reason = successful, reason
