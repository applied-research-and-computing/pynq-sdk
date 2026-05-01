from __future__ import annotations

import asyncio
import logging
import socket
from typing import Optional

logger = logging.getLogger(__name__)

_SERVICE_TYPE = "_hislip._tcp.local."
_USB_STATIC_IP = "192.168.2.1"


class MDNSAdvertiser:
    """
    Advertises the instrument over mDNS using Zeroconf.

    Service type: ``_hislip._tcp.local.``
    Hostname: ``{prefix}-{last4mac}.local``

    On USB-OTG only deployments (RNDIS/ECM gadget at 192.168.2.1) mDNS may
    not traverse the host stack reliably; a static-IP fallback message is
    always printed so clients can connect directly.
    """

    def __init__(
        self,
        manufacturer: str,
        model: str,
        port: int = 4880,
        hostname_prefix: Optional[str] = None,
    ) -> None:
        self.manufacturer = manufacturer
        self.model = model
        self.port = port
        self._prefix = hostname_prefix or (
            f"{manufacturer.lower().replace(' ', '-')}-{model.lower().replace(' ', '-')}"
        )
        self._zeroconf: Optional[object] = None
        self._service_info: Optional[object] = None

    async def start(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._register)

    async def stop(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._unregister)

    # ------------------------------------------------------------------
    # Sync helpers (run in executor)
    # ------------------------------------------------------------------

    def _register(self) -> None:
        last4 = self._last4_mac()
        hostname = f"{self._prefix}-{last4}"
        service_name = f"{hostname}.{_SERVICE_TYPE}"

        logger.info("mDNS: advertising %s port %d", service_name, self.port)
        print(f"Instrument available: {hostname}.local:{self.port}")
        print(f"USB mode: connect to {_USB_STATIC_IP}:{self.port}")

        try:
            from zeroconf import ServiceInfo, Zeroconf  # type: ignore[import]
            from zeroconf import IPVersion  # type: ignore[import]
        except ImportError:
            logger.warning("zeroconf not installed; mDNS disabled")
            return

        addresses = self._local_addresses()
        if not addresses:
            logger.warning("mDNS: no network addresses found; mDNS disabled")
            return

        try:
            info = ServiceInfo(
                _SERVICE_TYPE,
                service_name,
                addresses=addresses,
                port=self.port,
                properties={
                    b"manufacturer": self.manufacturer.encode(),
                    b"model": self.model.encode(),
                },
                server=f"{hostname}.local.",
            )
            zc = Zeroconf()
            zc.register_service(info)
            self._zeroconf = zc
            self._service_info = info
            logger.info("mDNS: registered %s", service_name)
        except Exception as exc:
            logger.warning("mDNS registration failed: %s", exc)

    def _unregister(self) -> None:
        if self._zeroconf and self._service_info:
            try:
                self._zeroconf.unregister_service(self._service_info)  # type: ignore[union-attr]
                self._zeroconf.close()  # type: ignore[union-attr]
            except Exception as exc:
                logger.debug("mDNS unregister error: %s", exc)
        self._zeroconf = None
        self._service_info = None

    @staticmethod
    def _last4_mac() -> str:
        try:
            import uuid
            mac = uuid.getnode()
            return f"{mac & 0xFFFFFF:06X}"[-4:]
        except Exception:
            return "0000"

    @staticmethod
    def _local_addresses() -> list:
        import ipaddress
        addrs = []
        try:
            # Get all non-loopback IPv4 addresses
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                ip = info[4][0]
                try:
                    addr = ipaddress.IPv4Address(ip)
                    if not addr.is_loopback:
                        addrs.append(socket.inet_aton(ip))
                except Exception:
                    pass
        except Exception:
            pass

        if not addrs:
            # Fallback: try connecting to public address to discover local IP
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                s.close()
                addrs.append(socket.inet_aton(ip))
            except Exception:
                pass

        return addrs
