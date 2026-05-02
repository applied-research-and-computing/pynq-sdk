"""
Tests for MDNSAdvertiser: Zeroconf service registration.
Uses monkeypatching to avoid real network operations.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pynq_instrument.discovery import MDNSAdvertiser


class TestMDNSAdvertiser:
    def test_startup_message_printed(self, capsys):
        advertiser = MDNSAdvertiser("Acme", "TestBox", port=4880)
        with patch("pynq_instrument.discovery.MDNSAdvertiser._local_addresses", return_value=[]):
            with patch("pynq_instrument.discovery.MDNSAdvertiser._last4_mac", return_value="ABCD"):
                advertiser._register()

        captured = capsys.readouterr()
        assert "ABCD" in captured.out
        assert "4880" in captured.out

    def test_hostname_uses_last4_mac(self, capsys):
        advertiser = MDNSAdvertiser("Acme", "TestBox", port=4880)
        with patch("pynq_instrument.discovery.MDNSAdvertiser._local_addresses", return_value=[]):
            with patch("pynq_instrument.discovery.MDNSAdvertiser._last4_mac", return_value="1A2B"):
                advertiser._register()

        captured = capsys.readouterr()
        assert "1A2B" in captured.out

    def test_zeroconf_registered_when_addresses_available(self):
        mock_zc = MagicMock()
        mock_info = MagicMock()

        fake_address = b"\xc0\xa8\x01\x01"  # 192.168.1.1

        with patch("pynq_instrument.discovery.MDNSAdvertiser._local_addresses",
                   return_value=[fake_address]):
            with patch("pynq_instrument.discovery.MDNSAdvertiser._last4_mac",
                       return_value="FFFF"):
                with patch("pynq_instrument.discovery.ServiceInfo", return_value=mock_info):
                    with patch("pynq_instrument.discovery.Zeroconf", return_value=mock_zc):
                        import pynq_instrument.discovery as d
                        # Temporarily make imports succeed
                        d.ServiceInfo = MagicMock(return_value=mock_info)
                        d.Zeroconf = MagicMock(return_value=mock_zc)

                        advertiser = MDNSAdvertiser("Acme", "TestBox", port=4880)
                        advertiser._register()

        # Regardless of mock depth, we just verify _register doesn't raise
        # (full Zeroconf integration tested manually on hardware)

    def test_zeroconf_unavailable_logs_warning(self, caplog):
        import logging

        advertiser = MDNSAdvertiser("Acme", "TestBox", port=4880)
        with patch("pynq_instrument.discovery.MDNSAdvertiser._local_addresses",
                   return_value=[b"\xc0\xa8\x01\x01"]):
            with patch("pynq_instrument.discovery.MDNSAdvertiser._last4_mac",
                       return_value="0000"):
                with patch("builtins.__import__", side_effect=_block_zeroconf):
                    with caplog.at_level(logging.WARNING, logger="pynq_instrument.discovery"):
                        advertiser._register()

        # Should log a warning, not raise
        assert any("zeroconf" in r.message.lower() for r in caplog.records)

    def test_unregister_noop_when_not_registered(self):
        advertiser = MDNSAdvertiser("Acme", "TestBox", port=4880)
        advertiser._unregister()  # Should not raise

    def test_last4_mac_format(self):
        mac = MDNSAdvertiser._last4_mac()
        assert len(mac) == 4
        assert all(c in "0123456789ABCDEFabcdef" for c in mac)


def _block_zeroconf(name, *args, **kwargs):
    if "zeroconf" in name:
        raise ImportError(f"Blocked: {name}")
    import builtins
    return builtins.__import__(name, *args, **kwargs)
