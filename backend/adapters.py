"""Pure converters from ROS messages to browser-friendly payloads.

Messages are read only through attributes and get_fields_and_field_types(), so
nothing here imports ROS and tests can use fake objects.
"""
import array
import math
from typing import Any

import cv2
import numpy as np

IMAGE_TYPES = {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}
SCAN_TYPE = "sensor_msgs/msg/LaserScan"
_SCALARS = ("Bool", "String", "Int8", "Int16", "Int32", "Int64",
            "UInt8", "UInt16", "UInt32", "UInt64", "Float32", "Float64")
VALUE_TYPES = {
    "geometry_msgs/msg/Twist",
    "nav_msgs/msg/Odometry",
    "geometry_msgs/msg/Pose",
    "geometry_msgs/msg/PoseStamped",
    *(f"std_msgs/msg/{t}" for t in _SCALARS),
}
JPEG_QUALITY = 70
MAX_ITEMS = 32
_ENCODINGS = {  # encoding -> (numpy dtype, channels)
    "rgb8": ("<u1", 3), "bgr8": ("<u1", 3), "rgba8": ("<u1", 4), "bgra8": ("<u1", 4),
    "mono8": ("<u1", 1), "mono16": ("<u2", 1), "16UC1": ("<u2", 1), "32FC1": ("<f4", 1),
}


def kind_for_type(type_name: str) -> str:
    if type_name in IMAGE_TYPES:
        return "image"
    if type_name == SCAN_TYPE:
        return "scan"
    if type_name in VALUE_TYPES:
        return "value"
    return "generic"


def adapt(type_name: str, msg: Any) -> dict:
    """Convert a message to a payload body. Never raises."""
    try:
        if type_name == "sensor_msgs/msg/Image":
            return _image(msg)
        if type_name == "sensor_msgs/msg/CompressedImage":
            return {"kind": "image", "jpeg": bytes(msg.data), "width": None,
                    "height": None, "encoding": str(msg.format)}
        if type_name == SCAN_TYPE:
            return _scan(msg)
        if type_name in VALUE_TYPES:
            return {"kind": "value", "data": _flatten(_generic(msg))}
        return {"kind": "generic", "data": _generic(msg)}
    except Exception as exc:  # adapters must never take the worker down
        return _fallback(msg, f"{type(exc).__name__}: {exc}")


def _fallback(msg: Any, note: str) -> dict:
    try:
        data = _generic(msg)
    except Exception:
        data = {}
    return {"kind": "generic", "data": data, "note": note}


def _image(msg: Any) -> dict:
    spec = _ENCODINGS.get(msg.encoding)
    if spec is None:
        return _fallback(msg, f"unsupported encoding {msg.encoding!r}")
    dtype, channels = spec
    w, h = int(msg.width), int(msg.height)
    itemsize = np.dtype(dtype).itemsize
    raw = np.frombuffer(bytes(msg.data), np.uint8)
    step = int(msg.step) or w * channels * itemsize
    rows = raw[: h * step].reshape(h, step)[:, : w * channels * itemsize]
    img = np.ascontiguousarray(rows).view(dtype).reshape(h, w, channels)
    if msg.encoding in ("mono16", "16UC1", "32FC1"):
        img = _colormap_depth(img[:, :, 0])
    elif msg.encoding == "rgb8":
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    elif msg.encoding == "rgba8":
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    elif msg.encoding == "bgra8":
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        return _fallback(msg, "jpeg encode failed")
    return {"kind": "image", "jpeg": buf.tobytes(), "width": w, "height": h,
            "encoding": msg.encoding}


def _colormap_depth(depth: np.ndarray) -> np.ndarray:
    d = np.nan_to_num(depth.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    lo, hi = float(d.min()), float(d.max())
    scaled = (d - lo) / (hi - lo) * 255 if hi > lo else np.zeros_like(d)
    return cv2.applyColorMap(scaled.astype(np.uint8), cv2.COLORMAP_TURBO)


def _scan(msg: Any) -> dict:
    lo, hi = msg.range_min, msg.range_max

    def mm(r):
        if not math.isfinite(r) or r < lo or r > hi:
            return None
        return int(round(r * 1000))

    return {"kind": "scan", "data": {
        "angle_min": msg.angle_min, "angle_increment": msg.angle_increment,
        "range_min": lo, "range_max": hi, "ranges_mm": [mm(r) for r in msg.ranges],
    }}


def _generic(msg: Any) -> dict:
    return {name: _convert(getattr(msg, name)) for name in msg.get_fields_and_field_types()}


def _convert(value: Any) -> Any:
    if hasattr(value, "get_fields_and_field_types"):
        return _generic(value)
    if isinstance(value, (bytes, bytearray)) or (
            isinstance(value, array.array) and value.typecode == "B"):
        return {"bytes": len(value)}
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, (list, tuple, array.array)):
        items = [_convert(v) for v in value[:MAX_ITEMS]]
        return {"items": items, "total": len(value)} if len(value) > MAX_ITEMS else items
    if isinstance(value, (str, bool, int)) or value is None:
        return value
    return str(value)


def _flatten(data: dict, prefix: str = "") -> dict:
    out: dict = {}
    for key, val in data.items():
        if isinstance(val, dict) and "bytes" not in val and "items" not in val:
            out.update(_flatten(val, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = val
    return out
