"""``tradingagents-web``: serve the page and its API on this machine."""

from __future__ import annotations

import argparse
import logging
import sys

from . import LOOPBACK_HOSTS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tradingagents-web",
                                     description="Serve the TradingAgents page on this machine.")
    parser.add_argument("--host", default="127.0.0.1", help="a loopback address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="default 8765")
    args = parser.parse_args(argv)

    # No sign-in protects the API, and every run spends the configured model quota.
    if args.host not in LOOPBACK_HOSTS:
        print(f"Refusing to serve on {args.host}: the API has no sign-in, so it answers only on "
              "this machine (127.0.0.1, localhost or ::1).", file=sys.stderr)
        return 2

    try:
        import uvicorn
    except ImportError:
        print('The web server needs its extra: pip install "tradingagents[web]"', file=sys.stderr)
        return 2

    from .server import create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"TradingAgents page: http://{'localhost' if args.host != '::1' else '[::1]'}:{args.port}  "
          f"(API docs at /api/docs)")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
