from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Carbon PYNQ Instrument server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--overlay", metavar="FILE", help="Bitfile to load on startup")
    parser.add_argument("--port", type=int, default=4880, help="HiSLIP sync channel port")
    parser.add_argument("--async-port", type=int, default=4881, help="HiSLIP async channel port")
    parser.add_argument("--manufacturer", default="Carbon", help="Instrument manufacturer string")
    parser.add_argument("--model", default="PYNQ-Instrument", help="Instrument model string")
    parser.add_argument("--serial", default="SN-0001", help="Instrument serial number")
    parser.add_argument("--firmware", default="0.1.0", help="Firmware version string")
    parser.add_argument("--no-mdns", action="store_true", help="Disable mDNS advertisement")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    from .instrument import Instrument

    inst = Instrument(
        args.manufacturer,
        args.model,
        args.serial,
        args.firmware,
    )

    if args.overlay:
        inst.use_pynq_backend()
        inst.load_overlay(args.overlay)

    try:
        asyncio.run(
            inst.start_async(
                port=args.port,
                async_port=args.async_port,
                advertise=not args.no_mdns,
            )
        )
    except KeyboardInterrupt:
        print("\nServer stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
