"""Control app network and path settings (Phase 31, ADR 0015).

Why: the app must be reachable only from the home LAN. It binds one loopback or
private address (never ``0.0.0.0``), accepts only allowlisted client networks
and Host names, and refuses anything public at start rather than serving it.

Contract: ``load_control_config`` raises ``ControlConfigError`` for an unsafe
or malformed value; the server does not start with a guessed fallback. Do not
add a "bind all interfaces" option: that is how a LAN tool becomes public.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_BIND = "CEC_CONTROL_BIND"
ENV_ALLOWED_NETS = "CEC_CONTROL_ALLOWED_NETS"
ENV_HOSTS = "CEC_CONTROL_HOSTS"
ENV_TLS_CERT = "CEC_CONTROL_TLS_CERT"
ENV_TLS_KEY = "CEC_CONTROL_TLS_KEY"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8730
LOOPBACK_NETS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class ControlConfigError(ValueError):
    """Unsafe or malformed control-app setting (message names the variable)."""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True, slots=True)
class ControlConfig:
    host: str
    port: int
    allowed_nets: tuple[IPNetwork, ...]
    allowed_hosts: frozenset[str]
    data_dir: Path
    env_path: Path
    log_dir: Path
    tls_cert: Path | None = None
    tls_key: Path | None = None
    demo: bool = False

    @property
    def https(self) -> bool:
        return self.tls_cert is not None and self.tls_key is not None

    @property
    def control_dir(self) -> Path:
        return self.data_dir / "control"

    @property
    def users_path(self) -> Path:
        return self.control_dir / "users.json"

    @property
    def audit_dir(self) -> Path:
        return self.control_dir / "audit"

    @property
    def backup_dir(self) -> Path:
        return self.control_dir / "env-backups"


def _ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    value = text.strip()
    if value.lower() == "localhost":
        value = DEFAULT_HOST
    return ipaddress.ip_address(value)


def is_lan_or_loopback(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if addr.is_unspecified or addr.is_multicast:
        return False
    return addr.is_loopback or addr.is_private


def parse_bind(raw: str | None) -> tuple[str, int]:
    """``host:port``, ``[v6]:port`` or ``host``. The host must be an IP literal."""
    text = (raw or "").strip()
    if not text:
        return DEFAULT_HOST, DEFAULT_PORT
    host, port_text = text, ""
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            raise ControlConfigError(f"{ENV_BIND} is not a valid address")
        host, rest = text[1:end], text[end + 1 :]
        port_text = rest.removeprefix(":")
    elif text.count(":") == 1:
        host, port_text = text.split(":")
    try:
        addr = _ip(host)
        port = int(port_text) if port_text else DEFAULT_PORT
    except ValueError as exc:
        raise ControlConfigError(
            f"{ENV_BIND} must be an IP address and port, for example 192.168.1.20:8730"
        ) from exc
    if not 1 <= port <= 65535:
        raise ControlConfigError(f"{ENV_BIND} port is out of range")
    if not is_lan_or_loopback(addr):
        raise ControlConfigError(
            f"{ENV_BIND} must be a loopback or private LAN address (never 0.0.0.0 or public)"
        )
    return str(addr), port


def default_allowed_nets(host: str) -> tuple[IPNetwork, ...]:
    """Loopback plus the bind address's /24 (IPv4) or /64 (IPv6) when private."""
    addr = ipaddress.ip_address(host)
    nets: list[IPNetwork] = list(LOOPBACK_NETS)
    if addr.is_private and not addr.is_loopback:
        prefix = 24 if addr.version == 4 else 64
        nets.append(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))
    return tuple(nets)


def parse_allowed_nets(raw: str | None, *, host: str) -> tuple[IPNetwork, ...]:
    text = (raw or "").strip()
    if not text:
        return default_allowed_nets(host)
    nets: list[IPNetwork] = []
    for part in text.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError as exc:
            raise ControlConfigError(f"{ENV_ALLOWED_NETS} has an invalid network") from exc
        too_wide = not net.is_loopback and net.num_addresses > 2**16
        if not (net.is_private or net.is_loopback) or too_wide:
            raise ControlConfigError(
                f"{ENV_ALLOWED_NETS} accepts loopback or private networks of /16 or smaller"
            )
        nets.append(net)
    if not nets:
        raise ControlConfigError(f"{ENV_ALLOWED_NETS} is empty")
    return tuple(nets)


def default_allowed_hosts(host: str, extra: str | None = None) -> frozenset[str]:
    names = {"localhost", "127.0.0.1", "::1", host.lower()}
    machine = socket.gethostname().strip().lower()
    if machine:
        names.add(machine)
        names.add(machine if machine.endswith(".local") else f"{machine}.local")
    for part in (extra or "").split(","):
        if part.strip():
            names.add(part.strip().lower())
    return frozenset(names)


def client_allowed(remote: str | None, nets: tuple[IPNetwork, ...]) -> bool:
    """True when the socket peer address is inside an allowlisted network."""
    if not remote:
        return False
    try:
        addr = ipaddress.ip_address(remote)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return any(addr.version == net.version and addr in net for net in nets)


def host_header_name(raw: str | None) -> str:
    """Lower-case host name from a Host header, without the port."""
    text = (raw or "").strip().lower()
    if text.startswith("["):
        end = text.find("]")
        return text[1:end] if end > 0 else ""
    return text.rsplit(":", 1)[0] if text.count(":") == 1 else text


def load_control_config(
    env: Mapping[str, str] | None = None,
    *,
    root: Path | None = None,
) -> ControlConfig:
    source = env if env is not None else os.environ
    base = root if root is not None else repo_root()
    host, port = parse_bind(source.get(ENV_BIND))
    nets = parse_allowed_nets(source.get(ENV_ALLOWED_NETS), host=host)
    cert = (source.get(ENV_TLS_CERT) or "").strip()
    key = (source.get(ENV_TLS_KEY) or "").strip()
    if bool(cert) != bool(key):
        raise ControlConfigError(f"Set both {ENV_TLS_CERT} and {ENV_TLS_KEY}, or neither")
    log_root = (source.get("CEC_LOG_DIR") or "").strip()
    return ControlConfig(
        host=host,
        port=port,
        allowed_nets=nets,
        allowed_hosts=default_allowed_hosts(host, source.get(ENV_HOSTS)),
        data_dir=base / "data",
        env_path=base / ".env",
        log_dir=Path(log_root).expanduser() if log_root else base / "logs",
        tls_cert=Path(cert) if cert else None,
        tls_key=Path(key) if key else None,
    )
