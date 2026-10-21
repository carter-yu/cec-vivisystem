"""``python -m cec_vivisystem.control`` — serve, demo and account commands.

``serve`` runs the LAN app (needs the ``control`` extra). ``adduser``,
``passwd``, ``deluser`` and ``users`` manage ``data/control/users.json`` on the
Mini; there is no web sign-up. ``demo`` serves synthetic data on loopback for
screenshots and never reads the real ``.env``.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Callable, Sequence

from cec_vivisystem.control.auth import ROLES, UserStore, UserStoreError
from cec_vivisystem.control.config import ControlConfigError, load_control_config

Prompt = Callable[[str], str]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cec_vivisystem.control")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="serve the control app on CEC_CONTROL_BIND (LAN only)")
    add = sub.add_parser("adduser", help="create an account (prompts for the passphrase)")
    add.add_argument("name")
    add.add_argument("--role", choices=ROLES, required=True)
    pw = sub.add_parser("passwd", help="set a new passphrase for an account")
    pw.add_argument("name")
    rm = sub.add_parser("deluser", help="remove an account")
    rm.add_argument("name")
    sub.add_parser("users", help="list accounts (names and roles only)")
    demo = sub.add_parser("demo", help="serve synthetic data on loopback (screenshots)")
    demo.add_argument("--host", default="127.0.0.1")
    demo.add_argument("--port", type=int, default=8799)
    return parser


def _ask_passphrase(prompt: Prompt) -> str:
    first = prompt("Passphrase (12 or more characters): ")
    second = prompt("Type the passphrase again: ")
    if first != second:
        raise UserStoreError("The two passphrases are different")
    return first


def run(
    argv: Sequence[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    prompt: Prompt = getpass.getpass,
    out: Callable[[str], None] = print,
) -> int:
    args = _parser().parse_args(argv)
    source = env if env is not None else dict(os.environ)
    if args.command == "demo":
        return _demo(args.host, args.port, out)
    try:
        config = load_control_config(source)
    except ControlConfigError as exc:
        out(f"error: {exc}")
        return 2
    store = UserStore(config.users_path)
    try:
        if args.command == "adduser":
            user = store.add(args.name, args.role, _ask_passphrase(prompt))
            out(f"added {user.name} ({user.role}) to {config.users_path}")
        elif args.command == "passwd":
            store.set_password(args.name, _ask_passphrase(prompt))
            out(f"passphrase changed for {args.name}")
        elif args.command == "deluser":
            store.remove(args.name)
            out(f"removed {args.name}")
        elif args.command == "users":
            for user in sorted(store.load().values(), key=lambda u: u.name):
                out(f"{user.name}\t{user.role}\t{user.lang}")
        elif args.command == "serve":
            return _serve(config, out)
    except UserStoreError as exc:
        out(f"error: {exc}")
        return 1
    return 0


def _serve(config, out: Callable[[str], None]) -> int:
    import uvicorn

    from cec_vivisystem.control.app import create_app
    from cec_vivisystem.logging import get_logger

    get_logger(__name__).info(
        "control_starting",
        component="control",
        host=config.host,
        port=config.port,
        https=config.https,
        allowed_nets=[str(net) for net in config.allowed_nets],
    )
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        proxy_headers=False,  # the socket peer is the only trusted client address
        server_header=False,
        access_log=False,  # control_request logs path and status without query strings
        log_level="warning",
        ssl_certfile=str(config.tls_cert) if config.tls_cert else None,
        ssl_keyfile=str(config.tls_key) if config.tls_key else None,
    )
    return 0


def _demo(host: str, port: int, out: Callable[[str], None]) -> int:
    from cec_vivisystem.control.demo import (
        DEMO_PASSPHRASE,
        DEMO_USERS,
        DemoRefused,
        build_demo,
        require_loopback,
    )

    try:
        host = require_loopback(host)
    except DemoRefused as exc:
        out(f"error: {exc}")
        return 2
    import uvicorn

    app, root = build_demo(host, port)
    out(f"demo data in {root} (synthetic; delete when done)")
    for name, role in DEMO_USERS:
        out(f"  http://{host}:{port}/demo/signin/{role}?next=/   ({name}, {role})")
    out(f"  or sign in with passphrase: {DEMO_PASSPHRASE}")
    uvicorn.run(app, host=host, port=port, proxy_headers=False, server_header=False,
                access_log=False, log_level="warning")
    return 0


def main() -> None:
    from dotenv import load_dotenv

    from cec_vivisystem.logging import setup_logging

    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command != "demo":
        load_dotenv()
    setup_logging(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        enable_file_logging=False if command == "demo" else None,
    )
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
