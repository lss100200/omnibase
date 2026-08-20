"""Fail-closed Provider URL policy for the personal desktop runtime."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from omnibase.desktop_local.errors import DesktopLocalError

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_IPV6_GLOBAL_UNICAST = ipaddress.IPv6Network("2000::/3")


class DesktopEndpointError(DesktopLocalError):
    """The configured Provider URL is unsafe or cannot be used."""


@dataclass(frozen=True, slots=True)
class ResolvedDesktopEndpoint:
    scheme: str
    hostname: str
    port: int
    path: str
    connect_host: str
    connect_addrs: tuple[str, ...]
    loopback: bool
    chat_path: str


def _reject(code: str) -> None:
    raise DesktopEndpointError(code)


def _hostname_is_loopback(hostname: str) -> bool:
    if hostname in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _parse_connect_address(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        _reject("desktop_provider_endpoint_invalid")
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


def is_global_unicast(address: str) -> bool:
    """Return whether *address* is a globally reachable unicast IP.

    Authority is CPython ``ipaddress`` IANA special-purpose registries
    (private/documentation/benchmark/CGNAT/reserved) plus unicast-only
    constraints: multicast, unspecified, loopback, link-local, and IPv6
    space outside ``2000::/3`` fail closed. IPv4-mapped IPv6 is unwrapped.
    """

    try:
        parsed = _parse_connect_address(address)
    except DesktopEndpointError:
        return False
    if (
        parsed.is_unspecified
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
    ):
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed not in _IPV6_GLOBAL_UNICAST:
        return False
    return bool(parsed.is_global)


def _require_public_or_loopback(address: str, *, allow_loopback: bool) -> None:
    parsed = _parse_connect_address(address)
    if parsed.is_loopback:
        if not allow_loopback:
            _reject("desktop_provider_endpoint_invalid")
        return
    if not is_global_unicast(str(parsed)):
        _reject("desktop_provider_endpoint_invalid")


def _unique_addresses(answers: list[tuple[object, ...]]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in answers:
        target = item[4]
        if not isinstance(target, tuple) or not target:
            continue
        address = str(target[0])
        if address in seen:
            continue
        seen.add(address)
        ordered.append(address)
    return tuple(ordered)


def pinned_connect_addrs(endpoint: ResolvedDesktopEndpoint) -> tuple[str, ...]:
    """Return the already-validated connect set; never re-resolve the hostname."""

    if not endpoint.connect_addrs:
        _reject("desktop_provider_endpoint_invalid")
    for address in endpoint.connect_addrs:
        _require_public_or_loopback(address, allow_loopback=endpoint.loopback)
    return endpoint.connect_addrs


def resolve_provider_endpoint(  # noqa: C901 - fail-closed URL, DNS and SSRF checks
    base_url: str,
    *,
    allow_loopback_http: bool,
) -> ResolvedDesktopEndpoint:
    candidate = base_url.strip()
    if not candidate or any(ch in candidate for ch in "\r\n\t"):
        _reject("desktop_provider_endpoint_invalid")
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        _reject("desktop_provider_endpoint_invalid")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not hostname
        or parsed.scheme not in {"http", "https"}
    ):
        _reject("desktop_provider_endpoint_invalid")
    loopback = _hostname_is_loopback(hostname)
    if parsed.scheme == "http":
        if not allow_loopback_http or not loopback:
            _reject("desktop_provider_endpoint_invalid")
        port = parsed.port or 80
        connect_host = "127.0.0.1" if hostname in {"127.0.0.1", "localhost"} else hostname
        connect_addrs = (connect_host,)
    else:
        if loopback and not allow_loopback_http:
            _reject("desktop_provider_endpoint_invalid")
        port = parsed.port or 443
        if loopback:
            connect_host = "127.0.0.1" if hostname != "::1" else "::1"
            connect_addrs = (connect_host,)
        else:
            try:
                ipaddress.ip_address(hostname)
            except ValueError:
                connect_host = hostname
            else:
                _require_public_or_loopback(hostname, allow_loopback=False)
                connect_host = hostname
            connect_addrs = (connect_host,)
    if not 1 <= port <= 65535:
        _reject("desktop_provider_endpoint_invalid")
    if not loopback:
        try:
            answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except OSError:
            _reject("desktop_provider_unreachable")
        addresses = _unique_addresses(answers)
        if not addresses:
            _reject("desktop_provider_unreachable")
        for address in addresses:
            _require_public_or_loopback(address, allow_loopback=False)
        connect_host = addresses[0]
        connect_addrs = addresses
    path = parsed.path or "/"
    chat_path = path.rstrip("/")
    if not chat_path.endswith("/chat/completions"):
        chat_path = f"{chat_path}/chat/completions"
    return ResolvedDesktopEndpoint(
        scheme=parsed.scheme,
        hostname=hostname,
        port=port,
        path=path,
        connect_host=connect_host,
        connect_addrs=connect_addrs,
        loopback=loopback,
        chat_path=chat_path or "/chat/completions",
    )
