"""Attack contracts for personal Provider endpoint pinning."""

from __future__ import annotations

import socket

import pytest

from omnibase.model_gateway.endpoint_policy import (
    ProviderEndpointPolicyError,
    resolve_provider_endpoint,
)


def test_endpoint_policy_binds_allowlist_and_sorted_public_dns(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ],
    )
    first = resolve_provider_endpoint(
        "https://api.example.com/v1",
        allowed_hosts=("api.example.com",),
    )
    second = resolve_provider_endpoint(
        "https://api.example.com/v1",
        allowed_hosts=("unused.example.com", "api.example.com"),
    )
    assert first.addresses == ("93.184.216.34", "93.184.216.35")
    assert first.policy_digest != second.policy_digest


def test_endpoint_policy_rejects_dns_rebinding_to_private(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ProviderEndpointPolicyError, match="private_address"):
        resolve_provider_endpoint(
            "https://api.example.com/v1",
            allowed_hosts=("api.example.com",),
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.example.com/v1",
        "https://user:secret@api.example.com/v1",
        "https://127.0.0.1/v1",
        "https://api.example.com:8443/v1",
    ],
)
def test_endpoint_policy_rejects_unsafe_url_shape(base_url: str) -> None:
    with pytest.raises(ProviderEndpointPolicyError):
        resolve_provider_endpoint(base_url, allowed_hosts=("api.example.com",))
