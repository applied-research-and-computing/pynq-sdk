import pytest

from pynq_instrument.scpi_parser import is_query, normalize_scpi


class TestNormalize:
    def test_simple_query(self):
        assert normalize_scpi("*idn?") == ("*IDN?", [])

    def test_command_with_args(self):
        assert normalize_scpi("gpio:set 1 HIGH") == ("GPIO:SET", ["1", "HIGH"])

    def test_strip_leading_trailing_whitespace(self):
        # Only leading/trailing whitespace is stripped; the mnemonic ends at the
        # first internal space. SCPI mnemonics never contain spaces.
        assert normalize_scpi("  TEMP:READ?  ") == ("TEMP:READ?", [])

    def test_mnemonic_uppercased_args_preserved(self):
        mnem, args = normalize_scpi("DAC:OUT 1.5")
        assert mnem == "DAC:OUT"
        assert args == ["1.5"]

    def test_empty_string(self):
        assert normalize_scpi("") == ("", [])
        assert normalize_scpi("   ") == ("", [])

    def test_quoted_arg(self):
        mnem, args = normalize_scpi('OVERLAY:LOAD "design.bit"')
        assert mnem == "OVERLAY:LOAD"
        assert args == ['"design.bit"']

    def test_multiple_args(self):
        mnem, args = normalize_scpi("ADC:CONFIG 12 1000")
        assert mnem == "ADC:CONFIG"
        assert args == ["12", "1000"]

    def test_star_commands(self):
        assert normalize_scpi("*RST") == ("*RST", [])
        assert normalize_scpi("*OPC?") == ("*OPC?", [])

    def test_no_args(self):
        mnem, args = normalize_scpi("OVERLAY:STATUS?")
        assert mnem == "OVERLAY:STATUS?"
        assert args == []


class TestIsQuery:
    def test_query_commands(self):
        assert is_query("*IDN?")
        assert is_query("ADC:READ?")
        assert is_query("SYST:ERR?")

    def test_write_commands(self):
        assert not is_query("*RST")
        assert not is_query("GPIO:SET")
        assert not is_query("OVERLAY:LOAD")
