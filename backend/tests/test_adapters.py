import struct
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import adapters

INF = float("inf")
NAN = float("nan")


def fake_msg(fields: dict):
    ns = SimpleNamespace(**fields)
    ns.get_fields_and_field_types = lambda: {k: "x" for k in fields}
    return ns


def image_msg(encoding, data, width=4, height=2, step=None):
    return SimpleNamespace(
        encoding=encoding, width=width, height=height, data=data,
        step=step if step is not None else len(data) // height, is_bigendian=0,
    )


def decode(jpeg):
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_UNCHANGED)


def test_kind_for_type():
    assert adapters.kind_for_type("sensor_msgs/msg/Image") == "image"
    assert adapters.kind_for_type("sensor_msgs/msg/CompressedImage") == "image"
    assert adapters.kind_for_type("sensor_msgs/msg/LaserScan") == "scan"
    assert adapters.kind_for_type("geometry_msgs/msg/Twist") == "value"
    assert adapters.kind_for_type("std_msgs/msg/String") == "value"
    assert adapters.kind_for_type("tf2_msgs/msg/TFMessage") == "generic"


def test_rgb8_to_jpeg():
    payload = adapters.adapt("sensor_msgs/msg/Image", image_msg("rgb8", bytes(range(24))))
    assert payload["kind"] == "image"
    assert decode(payload["jpeg"]).shape == (2, 4, 3)
    assert (payload["width"], payload["height"]) == (4, 2)
    assert payload["encoding"] == "rgb8"


def test_rgba_bgra_to_jpeg():
    for enc in ("rgba8", "bgra8"):
        payload = adapters.adapt("sensor_msgs/msg/Image", image_msg(enc, bytes(32)))
        assert payload["kind"] == "image"
        assert decode(payload["jpeg"]).shape[:2] == (2, 4)


def test_bgr8_mono8_mono16_depth():
    f32 = struct.pack("<8f", 1.0, NAN, INF, 2.0, 0.5, 1.5, 3.0, -INF)
    cases = {
        "bgr8": bytes(range(24)),
        "mono8": bytes(range(8)),
        "mono16": struct.pack("<8H", *range(0, 8000, 1000)),
        "16UC1": struct.pack("<8H", *range(0, 8000, 1000)),
        "32FC1": f32,
    }
    for enc, data in cases.items():
        payload = adapters.adapt("sensor_msgs/msg/Image", image_msg(enc, data))
        assert payload["kind"] == "image", enc
        assert decode(payload["jpeg"]).shape[:2] == (2, 4), enc


def test_constant_depth_does_not_fail():
    payload = adapters.adapt("sensor_msgs/msg/Image", image_msg("16UC1", bytes(16)))
    assert payload["kind"] == "image"


def test_compressed_passthrough():
    msg = SimpleNamespace(format="jpeg", data=b"\xff\xd8jpegbytes")
    payload = adapters.adapt("sensor_msgs/msg/CompressedImage", msg)
    assert payload["kind"] == "image"
    assert payload["jpeg"] == b"\xff\xd8jpegbytes"


def test_unknown_encoding_falls_back():
    payload = adapters.adapt("sensor_msgs/msg/Image", image_msg("weird8", bytes(8)))
    assert payload["kind"] == "generic"
    assert "note" in payload


def test_short_image_data_never_raises():
    payload = adapters.adapt("sensor_msgs/msg/Image", image_msg("rgb8", b"\x00", step=12))
    assert payload["kind"] == "generic"
    assert "note" in payload


def test_scan_payload():
    msg = SimpleNamespace(
        ranges=[0.5, INF, NAN, 12.3456], intensities=[1.0] * 4,
        angle_min=-1.0, angle_increment=0.5, range_min=0.1, range_max=12.0,
    )
    payload = adapters.adapt("sensor_msgs/msg/LaserScan", msg)
    assert payload["kind"] == "scan"
    data = payload["data"]
    assert data["ranges_mm"] == [500, None, None, None]
    assert data["angle_min"] == -1.0
    assert data["angle_increment"] == 0.5
    assert data["range_min"] == 0.1
    assert data["range_max"] == 12.0
    assert "intensities" not in data


def test_scan_below_range_min_is_none():
    msg = SimpleNamespace(
        ranges=[0.05, 1.0], angle_min=0.0, angle_increment=0.1,
        range_min=0.1, range_max=12.0,
    )
    assert adapters.adapt("sensor_msgs/msg/LaserScan", msg)["data"]["ranges_mm"] == [None, 1000]


def test_value_twist():
    v = lambda x, y, z: fake_msg({"x": x, "y": y, "z": z})
    msg = fake_msg({"linear": v(0.25, 0.0, 0.0), "angular": v(0.0, 0.0, 2.5)})
    payload = adapters.adapt("geometry_msgs/msg/Twist", msg)
    assert payload["kind"] == "value"
    assert payload["data"] == {
        "linear.x": 0.25, "linear.y": 0.0, "linear.z": 0.0,
        "angular.x": 0.0, "angular.y": 0.0, "angular.z": 2.5,
    }


def test_value_string_bool():
    s = adapters.adapt("std_msgs/msg/String", fake_msg({"data": "OK"}))
    b = adapters.adapt("std_msgs/msg/Bool", fake_msg({"data": True}))
    assert s == {"kind": "value", "data": {"data": "OK"}}
    assert b == {"kind": "value", "data": {"data": True}}


def test_value_pose_flattens_nested():
    pos = fake_msg({"x": 1.0, "y": 2.0, "z": 0.0})
    pose = fake_msg({"position": pos})
    payload = adapters.adapt("geometry_msgs/msg/Pose", pose)
    assert payload["data"] == {"position.x": 1.0, "position.y": 2.0, "position.z": 0.0}


def test_generic_truncates():
    inner = fake_msg({"values": [float(i) for i in range(100)], "blob": b"abcde", "name": "n"})
    msg = fake_msg({"inner": inner, "small": [1, 2, 3]})
    payload = adapters.adapt("tf2_msgs/msg/TFMessage", msg)
    assert payload["kind"] == "generic"
    assert "note" not in payload
    inner_data = payload["data"]["inner"]
    assert inner_data["values"] == {"items": [float(i) for i in range(32)], "total": 100}
    assert inner_data["blob"] == {"bytes": 5}
    assert inner_data["name"] == "n"
    assert payload["data"]["small"] == [1, 2, 3]


def test_generic_message_list_and_nonfinite():
    child = fake_msg({"v": NAN})
    msg = fake_msg({"children": [child, child], "f": INF})
    data = adapters.adapt("x/msg/Y", msg)["data"]
    assert data["children"] == [{"v": None}, {"v": None}]
    assert data["f"] is None


def test_adapt_never_raises():
    payload = adapters.adapt("sensor_msgs/msg/LaserScan", object())
    assert payload["kind"] == "generic"
    assert "note" in payload
    payload = adapters.adapt("std_msgs/msg/String", object())
    assert payload["kind"] == "generic"
    assert "note" in payload


def test_value_types_cover_scalars():
    for t in ("Bool", "String", "Int8", "UInt64", "Float32", "Float64"):
        assert f"std_msgs/msg/{t}" in adapters.VALUE_TYPES
    assert adapters.IMAGE_TYPES == {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"}
