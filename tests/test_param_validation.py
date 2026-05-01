import pytest

from pynq_instrument.param_parser import extract_args


def handler_int(x: int) -> str:
    return str(x)


def handler_float(x: float) -> str:
    return str(x)


def handler_bool(x: bool) -> str:
    return str(x)


def handler_str(x: str) -> str:
    return x


def handler_mixed(a: int, b: float, c: str) -> str:
    return f"{a},{b},{c}"


def handler_with_default(x: int, y: int = 99) -> str:
    return f"{x},{y}"


def handler_injected(ip, value: int) -> str:
    return str(value)


class TestExtractArgs:
    def test_int(self):
        assert extract_args(handler_int, ["42"], 0) == [42]

    def test_int_hex(self):
        assert extract_args(handler_int, ["0xFF"], 0) == [255]

    def test_float(self):
        result = extract_args(handler_float, ["3.14"], 0)
        assert abs(result[0] - 3.14) < 1e-9

    def test_bool_true_variants(self):
        for val in ("1", "ON", "TRUE", "True"):
            assert extract_args(handler_bool, [val], 0) == [True]

    def test_bool_false_variants(self):
        for val in ("0", "OFF", "FALSE", "False"):
            assert extract_args(handler_bool, [val], 0) == [False]

    def test_bool_invalid(self):
        with pytest.raises(ValueError, match="Invalid boolean"):
            extract_args(handler_bool, ["yes"], 0)

    def test_str_passthrough(self):
        assert extract_args(handler_str, ["RISING"], 0) == ["RISING"]

    def test_str_strips_quotes(self):
        assert extract_args(handler_str, ['"design.bit"'], 0) == ["design.bit"]

    def test_mixed_types(self):
        assert extract_args(handler_mixed, ["7", "2.5", "EDGE"], 0) == [7, 2.5, "EDGE"]

    def test_default_used_when_arg_missing(self):
        assert extract_args(handler_with_default, ["5"], 0) == [5, 99]

    def test_missing_required_raises(self):
        with pytest.raises(ValueError, match="Missing required parameter"):
            extract_args(handler_int, [], 0)

    def test_injected_count_offset(self):
        # First param (ip) is injected; extract only value: int from scpi_args
        assert extract_args(handler_injected, ["100"], 1) == [100]
