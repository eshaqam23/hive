import pytest

from robot_link import (
    CAMERA_PROFILES,
    MotorLine,
    StopConfirmer,
    camera_params,
    clamp_power,
    detect_gripper,
    is_zero_drive,
    parse_motor_line,
    profile_variants,
    twist_for,
)


def test_parse_drive_four_fields():
    assert parse_motor_line("0,1,0,1\n") == MotorLine("drive", 0, 1, 0, 1, ())


def test_parse_drive_prefixed_and_crlf():
    assert parse_motor_line("D,127,1,127,0\r\n") == MotorLine("drive", 127, 1, 127, 0, ())


def test_parse_gripper_one_and_two_angles():
    one = parse_motor_line("G,120")
    assert one.kind == "gripper" and one.angles == (120.0,)
    two = parse_motor_line("G,-20,90")
    assert two.angles == (-20.0, 90.0)


@pytest.mark.parametrize(
    "bad", ["OK", "", "1,2,3", "a,1,0,1", "D,1,2,3", "G", "G,x", "G,1,2,3", "1.5,1,0,1", "G,nan",
     "--5,1,0,1", "²,1,0,1", "D,--1,1,0,1", "٣,1,0,1",
     "9" * 5000 + ",1,0,1", None, 5]
)
def test_parse_malformed(bad):
    assert parse_motor_line(bad) is None


def test_is_zero_drive():
    assert is_zero_drive(parse_motor_line("0,1,0,1"))
    assert is_zero_drive(parse_motor_line("D,0,0,0,1"))
    assert not is_zero_drive(parse_motor_line("5,1,0,1"))
    assert not is_zero_drive(parse_motor_line("0,1,3,1"))
    assert not is_zero_drive(parse_motor_line("G,0"))
    assert not is_zero_drive(None)


def test_twist_values():
    assert twist_for("forward", 0.5) == (0.25, 0.0)
    assert twist_for("left", 0.5) == (0.0, 2.5)
    assert twist_for("right", 1.0) == (0.0, -5.0)
    assert twist_for("stop", 0.7) == (0.0, 0.0)
    assert twist_for("backward", 0.5) == (-0.25, 0.0)


def test_twist_unknown_action():
    with pytest.raises(ValueError):
        twist_for("jump", 0.5)


def test_clamp_power():
    assert clamp_power(0.05) == 0.2
    assert clamp_power(3) == 1.0
    assert clamp_power(None) == 0.5
    assert clamp_power(0.6) == 0.6
    for bad in ("x", True, float("nan")):
        with pytest.raises(ValueError):
            clamp_power(bad)


def test_detect_gripper():
    both = {"/gripper/grab", "/gripper/release"}
    assert detect_gripper({"/gripper/position", "/gripper_open"}, both) == "trigger"
    assert detect_gripper({"/gripper/position"}, set()) == "position"
    assert detect_gripper({"/gripper_open"}, set()) == "bool"
    assert detect_gripper(set(), set()) is None
    assert detect_gripper(set(), {"/gripper/grab"}) is None


def test_camera_params_and_profiles():
    assert camera_params("medium", False) == [
        ("enable_depth", False),
        ("rgb_camera.color_profile", "424x240x15"),
    ]
    assert CAMERA_PROFILES["high"] == "640x480x15"
    with pytest.raises(ValueError):
        camera_params("ultra", True)
    assert profile_variants("424x240x15") == ["424x240x15", "424,240,15"]


ZERO = parse_motor_line("0,1,0,1")


def test_stop_idle_before_request():
    c = StopConfirmer()
    assert c.poll(1.0) == ("idle", False)


def test_stop_pending_then_confirmed():
    c = StopConfirmer()
    c.set_reporting(True)
    c.stop_requested(0)
    assert c.poll(0.1) == ("pending", False)
    c.observe(ZERO, 0.2)
    assert c.poll(0.21) == ("confirmed", False)


def test_stop_unconfirmed_resend_cadence():
    c = StopConfirmer()
    c.set_reporting(True)
    c.stop_requested(0)
    assert c.poll(0.5) == ("unconfirmed", True)
    assert c.poll(0.6) == ("unconfirmed", False)
    assert c.poll(0.7) == ("unconfirmed", True)
    assert c.poll(5.1) == ("unconfirmed", False)


def test_stop_jump_resends_once():
    c = StopConfirmer()
    c.set_reporting(True)
    c.stop_requested(0)
    assert c.poll(1.0) == ("unconfirmed", True)
    assert c.poll(1.05) == ("unconfirmed", False)


def test_zero_before_request_does_not_confirm():
    c = StopConfirmer()
    c.set_reporting(True)
    c.observe(ZERO, 0.0)
    c.stop_requested(1.0)
    assert c.poll(1.1) == ("pending", False)
    c.observe(parse_motor_line("5,1,5,1"), 1.2)
    c.observe(None, 1.3)
    assert c.poll(1.5)[0] == "unconfirmed"


def test_stop_unreported_resends_for_five_seconds():
    c = StopConfirmer()
    c.set_reporting(False)
    c.stop_requested(0)
    assert c.poll(0.1) == ("unreported", False)
    assert c.poll(0.2) == ("unreported", True)
    assert c.poll(0.3) == ("unreported", False)
    assert c.poll(0.4) == ("unreported", True)
    assert c.poll(5.0) == ("unreported", True)
    assert c.poll(5.2) == ("unreported", False)


def test_new_request_resets():
    c = StopConfirmer()
    c.set_reporting(True)
    c.stop_requested(0)
    c.observe(ZERO, 0.1)
    assert c.poll(0.2)[0] == "confirmed"
    c.stop_requested(10)
    assert c.poll(10.1) == ("pending", False)
