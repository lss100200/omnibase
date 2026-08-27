from __future__ import annotations

import socket

import pytest

from omnibase.desktop_local.endpoint import (
    DesktopEndpointError,
    is_global_unicast,
    resolve_provider_endpoint,
)
from omnibase.desktop_local.provider_http import DesktopProviderCallError, _open_connection

_NON_GLOBAL = (
    "0.0.0.1",
    "10.1.2.3",
    "100.64.0.1",
    "127.0.0.1",
    "169.254.10.2",
    "172.16.0.4",
    "192.0.2.1",
    "192.168.1.20",
    "198.18.0.1",
    "198.51.100.1",
    "203.0.113.1",
    "224.0.0.1",
    "240.0.0.1",
    "255.255.255.255",
    "::1",
    "::",
    "fe80::1",
    "fc00::1",
    "fd12:3456::1",
    "2001:db8::1",
    "ff02::1",
    "::ffff:10.0.0.1",
    "::ffff:100.64.0.1",
    "::ffff:192.0.2.1",
    "::ffff:169.254.1.1",
)

_GLOBAL = (
    "8.8.8.8",
    "1.1.1.1",
    "93.184.216.34",
    "192.0.0.9",
    "192.0.0.10",
    "2001:4860:4860::8888",
)


@pytest.mark.parametrize("address", _NON_GLOBAL)
def test_reserved_cgnat_benchmark_docs_and_link_local_are_not_global_unicast(
    address: str,
) -> None:
    assert is_global_unicast(address) is False


@pytest.mark.parametrize("address", _GLOBAL)
def test_public_unicast_and_iana_nat64_exceptions_are_global(address: str) -> None:
    assert is_global_unicast(address) is True


def test_dns_rebind_to_non_global_ranges_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    rejected = (
        "100.64.0.1",
        "192.0.2.1",
        "198.51.100.1",
        "203.0.113.1",
        "198.18.0.1",
        "240.0.0.1",
        "224.0.0.1",
        "fe80::1",
        "fc00::1",
        "2001:db8::1",
    )
    for address in rejected:
        monkeypatch.setattr(
            "omnibase.desktop_local.endpoint.socket.getaddrinfo",
            lambda host, port, *args, target=address, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (target, port))
            ],
        )
        with pytest.raises(DesktopEndpointError, match="desktop_provider_endpoint_invalid"):
            resolve_provider_endpoint("https://rebind.example/v1", allow_loopback_http=False)


def test_mixed_public_and_cgnat_answer_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "omnibase.desktop_local.endpoint.socket.getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.0.1", port)),
        ],
    )
    with pytest.raises(DesktopEndpointError, match="desktop_provider_endpoint_invalid"):
        resolve_provider_endpoint("https://mixed.example/v1", allow_loopback_http=False)


def test_public_pin_keeps_the_original_hostname_and_does_not_reconnect_on_rebind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookups: list[str] = []
    connected: list[str] = []

    def fake_getaddrinfo(host: str, port: int, *args: object, **kwargs: object) -> list[tuple]:
        lookups.append(str(host))
        if lookups.count(str(host)) == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.0.1", port))]

    def fake_create_connection(
        address: tuple[object, ...], timeout: object = None, **kwargs: object
    ):
        host = str(address[0])
        connected.append(host)
        if host.startswith("100.64."):
            raise AssertionError("connected to CGNAT after DNS rebind")
        raise OSError("pinned public connect failed closed")

    monkeypatch.setattr("omnibase.desktop_local.endpoint.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(
        "omnibase.desktop_local.provider_http.socket.create_connection",
        fake_create_connection,
    )
    endpoint = resolve_provider_endpoint("https://api.example.test/v1", allow_loopback_http=False)
    assert endpoint.hostname == "api.example.test"
    assert endpoint.connect_addrs == ("8.8.8.8",)
    with pytest.raises(DesktopProviderCallError, match="desktop_provider_unreachable"):
        _open_connection(endpoint, 1.0)
    assert connected == ["8.8.8.8"]
    assert lookups.count("api.example.test") == 1
