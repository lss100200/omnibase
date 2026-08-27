"""Shared fail-closed endpoint resolution and pinned HTTPS transport."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import ssl
from dataclasses import dataclass
from threading import Lock
from urllib.parse import urlsplit

import httpcore
import httpx


class ProviderEndpointPolicyError(ValueError):
    """A user-owned Provider endpoint is unsafe or cannot be resolved."""


def _digest(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class ResolvedProviderEndpoint:
    base_url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]
    allowlist_digest: str
    policy_digest: str


def resolve_provider_endpoint(
    base_url: str,
    *,
    allowed_hosts: tuple[str, ...],
) -> ResolvedProviderEndpoint:
    candidate = base_url.strip().rstrip("/")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port or 443
    except ValueError as exc:
        raise ProviderEndpointPolicyError("provider_base_url_invalid") from exc
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port != 443
    ):
        raise ProviderEndpointPolicyError("provider_base_url_invalid")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ProviderEndpointPolicyError("provider_base_url_ip_literal_forbidden")
    allowlist = tuple(sorted({item.lower().rstrip(".") for item in allowed_hosts}))
    if hostname not in allowlist:
        raise ProviderEndpointPolicyError("provider_host_not_allowed")
    try:
        answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ProviderEndpointPolicyError("provider_host_unreachable") from exc
    addresses = tuple(sorted({str(item[4][0]) for item in answers}))
    if not addresses:
        raise ProviderEndpointPolicyError("provider_host_unreachable")
    for address in addresses:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ProviderEndpointPolicyError("provider_dns_address_invalid") from exc
        if not parsed_address.is_global:
            raise ProviderEndpointPolicyError("provider_dns_private_address_forbidden")
    allowlist_digest = _digest({"allowed_hosts": allowlist})
    return ResolvedProviderEndpoint(
        base_url=candidate,
        hostname=hostname,
        port=port,
        addresses=addresses,
        allowlist_digest=allowlist_digest,
        policy_digest=_digest(
            {
                "base_url": candidate,
                "hostname": hostname,
                "port": port,
                "addresses": addresses,
                "allowlist_digest": allowlist_digest,
                "transport": "pinned-https-no-proxy-no-redirect-v1",
            }
        ),
    )


class _PinnedNetworkBackend(httpcore.SyncBackend):
    def __init__(self, endpoint: ResolvedProviderEndpoint) -> None:
        self._endpoint = endpoint
        self._cursor = 0
        self._lock = Lock()

    def connect_tcp(  # type: ignore[no-untyped-def]
        self,
        host,
        port,
        timeout=None,
        local_address=None,
        socket_options=None,
    ):
        if host.lower().rstrip(".") != self._endpoint.hostname or port != self._endpoint.port:
            raise httpcore.ConnectError("provider_endpoint_scope_drifted")
        with self._lock:
            address = self._endpoint.addresses[self._cursor % len(self._endpoint.addresses)]
            self._cursor += 1
        return super().connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class PinnedHTTPSRuntimeTransport(httpx.HTTPTransport):
    """Connect only to the endpoint's verified addresses while preserving TLS SNI."""

    def __init__(self, endpoint: ResolvedProviderEndpoint) -> None:
        super().__init__(verify=True, trust_env=False, retries=0)
        self._pool.close()  # type: ignore[attr-defined]
        self._pool = httpcore.ConnectionPool(  # type: ignore[attr-defined]
            ssl_context=ssl.create_default_context(),
            retries=0,
            network_backend=_PinnedNetworkBackend(endpoint),
        )


def create_hardened_provider_client(
    endpoint: ResolvedProviderEndpoint,
    *,
    timeout: float,
) -> httpx.Client:
    return httpx.Client(
        base_url=endpoint.base_url,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        transport=PinnedHTTPSRuntimeTransport(endpoint),
    )


__all__ = [
    "ProviderEndpointPolicyError",
    "ResolvedProviderEndpoint",
    "create_hardened_provider_client",
    "resolve_provider_endpoint",
]
