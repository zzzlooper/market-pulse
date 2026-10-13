"""Start Market Pulse, optionally over HTTPS with its own certificates (see app/tls.py).

    python -m app.serve                                  this computer only, plain HTTP
    python -m app.serve --host 0.0.0.0 --https           your phone too, encrypted

The background service (scripts/install-service.sh) runs this. With --https the certificates
are created or renewed before every start, so a new address or a due renewal is picked up by a
restart. Access logs stay off, as in the service.
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.serve", description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--https", action="store_true", help="serve HTTPS with Market Pulse's own certificates")
    args = parser.parse_args(argv)

    import uvicorn

    from .config import TLS_DIR
    tls = {}
    if args.https:
        from .tls import ensure
        folder = TLS_DIR
        info = ensure(folder)
        tls = {"ssl_keyfile": str(folder / "server.key"), "ssl_certfile": str(folder / "server.crt")}
        where = ", ".join(f"https://{a}:{args.port}" for a in info["addresses"] if ":" not in a) or "localhost only"
        print(f"HTTPS on {where}; CA fingerprint {info['fingerprint']}", flush=True)
    uvicorn.run("app.main:app", host=args.host, port=args.port, access_log=False, **tls)
    return 0


if __name__ == "__main__":
    sys.exit(main())
