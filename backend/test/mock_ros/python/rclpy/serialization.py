import pickle


def serialize_message(msg):
    return pickle.dumps(msg)


def deserialize_message(data, msg_type=None):
    return pickle.loads(bytes(data))
