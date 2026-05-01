from pynq_instrument.response_helpers import (
    respond_bool,
    respond_enum,
    respond_error,
    respond_float,
    respond_float_array,
    respond_int,
)


def test_respond_float_basic():
    assert respond_float(3.14) == "3.14"
    assert respond_float(0.0) == "0"
    assert respond_float(1.0) == "1"
    assert respond_float(-42.5) == "-42.5"


def test_respond_float_precision():
    # %.6g formatting
    assert respond_float(1.23456789) == "1.23457"
    assert respond_float(0.000001) == "1e-06"


def test_respond_int():
    assert respond_int(42) == "42"
    assert respond_int(-1) == "-1"
    assert respond_int(0) == "0"


def test_respond_bool():
    assert respond_bool(True) == "1"
    assert respond_bool(False) == "0"


def test_respond_enum():
    assert respond_enum("RISING") == "RISING"
    assert respond_enum("OK") == "OK"
    assert respond_enum(None) == ""


def test_respond_float_array():
    assert respond_float_array([1.0, 2.5, 3.0]) == "1,2.5,3"
    assert respond_float_array([]) == ""
    assert respond_float_array([0.123456789]) == "0.123457"


def test_respond_error():
    assert respond_error(2, "bad pin") == "ERR:2:bad pin"
    assert respond_error(-200, "Hardware not ready") == "ERR:-200:Hardware not ready"
    assert respond_error(-100, "") == "ERR:-100:"
