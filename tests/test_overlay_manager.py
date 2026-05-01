import pytest

from pynq_instrument.overlay_manager import MockOverlayManager, OverlayManager


class TestMockOverlayManager:
    def test_initially_not_loaded(self):
        om = MockOverlayManager()
        assert not om.is_loaded()
        assert om.status() == "NONE"

    def test_load_sets_state(self):
        om = MockOverlayManager()
        om.load("adc_design.bit")
        assert om.is_loaded()
        assert om.status() == "LOADED:adc_design.bit"
        assert om.version() == "adc_design"

    def test_pre_populated(self):
        om = MockOverlayManager(["adc_0", "dma_0"])
        assert om.is_loaded()
        assert "adc_0" in om.inventory()
        assert "dma_0" in om.inventory()

    def test_unload(self):
        om = MockOverlayManager(["adc_0"])
        om.unload()
        assert not om.is_loaded()
        assert om.status() == "NONE"

    def test_missing_ips(self):
        om = MockOverlayManager(["adc_0", "dma_0"])
        missing = om.missing_ips(["adc_0", "dma_0", "gpio_0"])
        assert missing == ["gpio_0"]

    def test_no_missing_ips(self):
        om = MockOverlayManager(["adc_0", "dma_0"])
        assert om.missing_ips(["adc_0"]) == []

    def test_get_ip_when_loaded(self):
        om = MockOverlayManager(["adc_0"])
        om.load("test.bit")
        ip = om.get_ip("any_ip")
        assert ip is not None

    def test_get_ip_when_not_loaded_raises(self):
        om = MockOverlayManager()
        with pytest.raises(RuntimeError, match="No overlay loaded"):
            om.get_ip("adc_0")
