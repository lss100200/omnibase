"""Safe service diagnostics for the local desktop launcher."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from omnibase.runtime.capabilities import CapabilityReport, ProductMode

_SECRET_KEY = re.compile(
    r"(secret|password|token|api[_-]?key|authorization|cookie|credential)", re.I
)


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    state: str
    detail: str | None = None
    exit_code: int | None = None


def select_mode(report: CapabilityReport, requested: ProductMode | None = None) -> ProductMode:
    """Select a mode without upgrading an unproven capability."""
    if requested is not None:
        if not report.supports(requested):
            raise ValueError(f"mode_not_available:{requested.value}")
        return requested
    return ProductMode.LOCAL if report.supports(ProductMode.LOCAL) else ProductMode.LITE


def redact_mapping(values: Mapping[str, object]) -> dict[str, object]:
    """Keep configuration shape while removing secret values recursively."""
    result: dict[str, object] = {}
    for key, value in values.items():
        if _SECRET_KEY.search(key):
            result[key] = "[REDACTED]"
        elif isinstance(value, Mapping):
            result[key] = redact_mapping(value)
        elif isinstance(value, (list, tuple)):
            result[key] = ["[REDACTED]"] if "secret" in key.lower() else list(value)
        else:
            result[key] = value
    return result


def diagnostics_payload(
    report: CapabilityReport,
    services: Iterable[ServiceStatus] = (),
    *,
    config_shape: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a safe diagnostic payload suitable for support bundles."""
    return {
        "capabilities": report.to_dict(),
        "services": [
            {
                "name": service.name,
                "state": service.state,
                "detail": service.detail,
                "exit_code": service.exit_code,
            }
            for service in services
        ],
        "config_shape": redact_mapping(config_shape or {}),
        "privacy": {
            "secrets_included": False,
            "user_documents_included": False,
            "provider_responses_included": False,
        },
    }


def diagnostics_json(*args: object, **kwargs: object) -> str:
    """Serialize diagnostics deterministically for a support bundle."""
    return json.dumps(diagnostics_payload(*args, **kwargs), sort_keys=True)


__all__ = [
    "ServiceStatus",
    "diagnostics_json",
    "diagnostics_payload",
    "redact_mapping",
    "select_mode",
]
