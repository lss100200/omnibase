"""Injectable HTTP/CLI transports for a separately trusted Node Daemon.

The transports do not resolve credential references and never execute a shell.
Tests inject deterministic clients/runners; production wiring must inject an
authenticated mTLS client or a fixed-path daemon executor explicitly.
"""

from __future__ import annotations

import json
import os
import ssl
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from http.client import HTTPSConnection
from pathlib import Path, PurePath
from stat import S_IMODE
from typing import Protocol
from urllib.parse import urlsplit

from omnibase.workspaces.overlay_adapters.contracts import (
    OverlayAction,
    OverlayDaemonCommand,
    OverlayDaemonReceipt,
    OverlayOutcomeUnknown,
    OverlayRejected,
    OverlayState,
    OverlayUnavailable,
)


class OverlayDaemonTransport(Protocol):
    def activate(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt: ...

    def rotate(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt: ...

    def revoke(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt: ...

    def status(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt: ...


class UnavailableOverlayDaemonTransport:
    """Production-safe default: no socket, subprocess, route, or peer side effect."""

    def activate(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        del command
        raise OverlayUnavailable("overlay_node_daemon_transport_unavailable")

    def rotate(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        del command
        raise OverlayUnavailable("overlay_node_daemon_transport_unavailable")

    def revoke(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        del command
        raise OverlayUnavailable("overlay_node_daemon_transport_unavailable")

    def status(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        del command
        raise OverlayUnavailable("overlay_node_daemon_transport_unavailable")


@dataclass(frozen=True, slots=True)
class HttpJsonResponse:
    status_code: int
    body: Mapping[str, object]


class InjectedHttpJsonClient(Protocol):
    """An explicitly authenticated client; mTLS material stays outside payloads."""

    def request_json(
        self,
        *,
        method: str,
        url: str,
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> HttpJsonResponse: ...


class MtlsHttpJsonClient:
    """Bounded stdlib HTTPS client with an explicit CA and client identity."""

    def __init__(
        self,
        *,
        ca_file: str,
        certificate_file: str,
        private_key_file: str,
        max_response_bytes: int = 1_048_576,
    ) -> None:
        if max_response_bytes < 1 or max_response_bytes > 4_194_304:
            raise ValueError("max_response_bytes must be within [1, 4194304]")
        ca_path = self._require_file(ca_file, private=False)
        certificate_path = self._require_file(certificate_file, private=False)
        private_key_path = self._require_file(private_key_file, private=True)
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ca_path))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.load_cert_chain(
            certfile=str(certificate_path),
            keyfile=str(private_key_path),
        )
        self._context = context
        self._max_response_bytes = max_response_bytes

    @staticmethod
    def _require_file(value: str, *, private: bool) -> Path:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("mTLS material paths must be absolute")
        try:
            file_stat = path.lstat()
        except FileNotFoundError as exc:
            raise ValueError("mTLS material file is missing") from exc
        if path.is_symlink() or not path.is_file():
            raise ValueError("mTLS material must be a regular non-symlink file")
        if (
            private
            and os.name == "posix"
            and (file_stat.st_uid != os.geteuid() or S_IMODE(file_stat.st_mode) & 0o077)
        ):
            raise ValueError("mTLS private key permissions are too broad")
        return path

    def request_json(
        self,
        *,
        method: str,
        url: str,
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise OSError("mTLS request URL rejected")
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        connection = HTTPSConnection(
            parsed.hostname,
            parsed.port or 443,
            timeout=timeout_seconds,
            context=self._context,
        )
        try:
            connection.request(
                method,
                target,
                body=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            raw = response.read(self._max_response_bytes + 1)
            if len(raw) > self._max_response_bytes:
                raise OSError("mTLS response exceeded configured limit")
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise OSError("mTLS response was not JSON") from exc
            if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
                raise OSError("mTLS response JSON shape rejected")
            return HttpJsonResponse(status_code=response.status, body=decoded)
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class CliResult:
    exit_code: int
    stdout: str
    stderr: str


class InjectedCliRunner(Protocol):
    """A no-shell argv/stdin runner supplied by the trusted Node installation."""

    def run(
        self,
        *,
        argv: Sequence[str],
        stdin: str,
        timeout_seconds: float,
    ) -> CliResult: ...


def _binding_payload(command: OverlayDaemonCommand) -> dict[str, object]:
    binding = command.binding
    payload: dict[str, object] = {
        "schema_version": 1,
        "provider": command.provider,
        "action": command.action.value,
        "operation_id": command.operation_id,
        "requested_at": command.requested_at.isoformat(),
        "binding": {
            "tenant_id": binding.tenant_id,
            "workspace_id": binding.workspace_id,
            "peer_grant_id": binding.peer_grant_id,
            "service_id": binding.service_id,
            "network_lease_id": binding.network_lease_id,
            "source_node_id": binding.source_node_id,
            "target_node_id": binding.target_node_id,
            "workspace_generation": binding.workspace_generation,
            "service_generation": binding.service_generation,
            "peer_fencing_token": binding.peer_fencing_token,
            "network_fencing_token": binding.network_fencing_token,
            "source_node_fencing_token": binding.source_node_fencing_token,
            "target_node_fencing_token": binding.target_node_fencing_token,
            "service_logical_name": binding.service_logical_name,
            "service_protocol": binding.service_protocol,
            "service_transport_protocol": binding.service_transport_protocol,
            "service_port": binding.service_port,
            "live_credential_generation": binding.live_credential_generation,
            "expires_at": binding.expires_at.isoformat(),
            "verification_digest": binding.verification_digest,
        },
        "source_daemon": {
            "daemon_id": command.source_daemon.daemon_id,
            "node_id": command.source_daemon.node_id,
            "node_fencing_token": command.source_daemon.node_fencing_token,
            "identity_thumbprint_digest": (command.source_daemon.identity_thumbprint_digest),
            "attestation_digest": command.source_daemon.attestation_digest,
            "expires_at": command.source_daemon.expires_at.isoformat(),
        },
        "target_daemon": {
            "daemon_id": command.target_daemon.daemon_id,
            "node_id": command.target_daemon.node_id,
            "node_fencing_token": command.target_daemon.node_fencing_token,
            "identity_thumbprint_digest": (command.target_daemon.identity_thumbprint_digest),
            "attestation_digest": command.target_daemon.attestation_digest,
            "expires_at": command.target_daemon.expires_at.isoformat(),
        },
        "publication": {
            "mode": "broker_logical_service",
            "logical_service_id": binding.service_id,
            "logical_name": binding.service_logical_name,
            "protocol": binding.service_protocol,
            "transport_protocol": binding.service_transport_protocol,
            "logical_port": binding.service_port,
        },
    }
    if command.credential is not None:
        payload["credential"] = {
            "reference": command.credential.reference,
            "provider": command.credential.provider,
            "operation_id": command.credential.operation_id,
            "action": command.credential.action.value,
            "rotation_generation": command.credential.rotation_generation,
            "expires_at": command.credential.expires_at.isoformat(),
            "reference_digest": command.credential.reference_digest,
        }
    return payload


def _required_str(body: Mapping[str, object], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str):
        raise OverlayRejected("overlay_daemon_receipt_invalid")
    return value


def _required_int(body: Mapping[str, object], field: str) -> int:
    value = body.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise OverlayRejected("overlay_daemon_receipt_invalid")
    return value


def _optional_int(body: Mapping[str, object], field: str) -> int | None:
    value = body.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise OverlayRejected("overlay_daemon_receipt_invalid")
    return value


def _parse_receipt(body: Mapping[str, object]) -> OverlayDaemonReceipt:
    try:
        action = OverlayAction(_required_str(body, "action"))
        state = OverlayState(_required_str(body, "state"))
        observed_at = datetime.fromisoformat(_required_str(body, "observed_at"))
    except (ValueError, TypeError) as exc:
        raise OverlayRejected("overlay_daemon_receipt_invalid") from exc
    return OverlayDaemonReceipt(
        action=action,
        operation_id=_required_str(body, "operation_id"),
        provider=_required_str(body, "provider"),
        state=state,
        network_lease_id=_required_str(body, "network_lease_id"),
        binding_digest=_required_str(body, "binding_digest"),
        workspace_generation=_required_int(body, "workspace_generation"),
        service_generation=_required_int(body, "service_generation"),
        peer_fencing_token=_required_int(body, "peer_fencing_token"),
        network_fencing_token=_required_int(body, "network_fencing_token"),
        source_node_fencing_token=_required_int(body, "source_node_fencing_token"),
        target_node_fencing_token=_required_int(body, "target_node_fencing_token"),
        source_daemon_attestation_digest=_required_str(
            body,
            "source_daemon_attestation_digest",
        ),
        target_daemon_attestation_digest=_required_str(
            body,
            "target_daemon_attestation_digest",
        ),
        credential_generation=_optional_int(body, "credential_generation"),
        observed_at=observed_at,
        receipt_digest=_required_str(body, "receipt_digest"),
    )


def _path_for(action: OverlayAction) -> str:
    return f"/v1/overlay/{action.value}"


class HttpOverlayDaemonTransport:
    """JSON transport to a trusted Daemon; authentication is injected as mTLS."""

    def __init__(
        self,
        *,
        base_url: str,
        client: InjectedHttpJsonClient,
        timeout_seconds: float = 5.0,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Node Daemon base_url must be an HTTPS origin without credentials")
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("timeout_seconds must be within (0, 30]")
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout_seconds = timeout_seconds

    def _request(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        payload = _binding_payload(command)
        try:
            response = self._client.request_json(
                method="POST",
                url=f"{self._base_url}{_path_for(command.action)}",
                payload=payload,
                timeout_seconds=self._timeout_seconds,
            )
        except TimeoutError as exc:
            if command.action is OverlayAction.STATUS:
                raise OverlayUnavailable("overlay_node_daemon_offline") from exc
            raise OverlayOutcomeUnknown("overlay_node_daemon_outcome_unknown") from exc
        except OSError as exc:
            if command.action is OverlayAction.STATUS:
                raise OverlayUnavailable("overlay_node_daemon_offline") from exc
            raise OverlayOutcomeUnknown("overlay_node_daemon_outcome_unknown") from exc
        if response.status_code == 409:
            raise OverlayRejected("overlay_node_daemon_binding_conflict")
        if response.status_code in {408, 425, 500, 502, 503, 504}:
            if command.action is OverlayAction.STATUS:
                raise OverlayUnavailable("overlay_node_daemon_offline")
            raise OverlayOutcomeUnknown("overlay_node_daemon_outcome_unknown")
        if response.status_code in {401, 403}:
            raise OverlayRejected("overlay_node_daemon_identity_rejected")
        if response.status_code < 200 or response.status_code >= 300:
            raise OverlayUnavailable("overlay_node_daemon_unavailable")
        return _parse_receipt(response.body)

    def activate(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        return self._request(command=command)

    def rotate(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        return self._request(command=command)

    def revoke(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        return self._request(command=command)

    def status(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        return self._request(command=command)


class CliOverlayDaemonTransport:
    """Fixed-path, no-shell transport for a co-located trusted Node Daemon CLI."""

    def __init__(
        self,
        *,
        executable_path: str,
        runner: InjectedCliRunner,
        timeout_seconds: float = 5.0,
    ) -> None:
        if not PurePath(executable_path).is_absolute():
            raise ValueError("Node Daemon CLI path must be absolute")
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("timeout_seconds must be within (0, 30]")
        self._executable_path = executable_path
        self._runner = runner
        self._timeout_seconds = timeout_seconds

    def _request(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        payload = json.dumps(
            _binding_payload(command),
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            result = self._runner.run(
                argv=(
                    self._executable_path,
                    "overlay",
                    command.action.value,
                    "--input-json",
                    "-",
                ),
                stdin=payload,
                timeout_seconds=self._timeout_seconds,
            )
        except TimeoutError as exc:
            if command.action is OverlayAction.STATUS:
                raise OverlayUnavailable("overlay_node_daemon_offline") from exc
            raise OverlayOutcomeUnknown("overlay_node_daemon_outcome_unknown") from exc
        except OSError as exc:
            if command.action is OverlayAction.STATUS:
                raise OverlayUnavailable("overlay_node_daemon_offline") from exc
            raise OverlayOutcomeUnknown("overlay_node_daemon_outcome_unknown") from exc
        if result.exit_code == 23:
            raise OverlayRejected("overlay_node_daemon_binding_conflict")
        if result.exit_code == 24:
            raise OverlayRejected("overlay_node_daemon_identity_rejected")
        if result.exit_code == 75:
            if command.action is OverlayAction.STATUS:
                raise OverlayUnavailable("overlay_node_daemon_offline")
            raise OverlayOutcomeUnknown("overlay_node_daemon_outcome_unknown")
        if result.exit_code != 0:
            raise OverlayUnavailable("overlay_node_daemon_unavailable")
        try:
            decoded = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise OverlayRejected("overlay_daemon_receipt_invalid") from exc
        if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
            raise OverlayRejected("overlay_daemon_receipt_invalid")
        return _parse_receipt(decoded)

    def activate(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        return self._request(command=command)

    def rotate(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        return self._request(command=command)

    def revoke(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        return self._request(command=command)

    def status(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        return self._request(command=command)


__all__ = [
    "CliOverlayDaemonTransport",
    "CliResult",
    "HttpJsonResponse",
    "HttpOverlayDaemonTransport",
    "InjectedCliRunner",
    "InjectedHttpJsonClient",
    "MtlsHttpJsonClient",
    "OverlayDaemonTransport",
    "UnavailableOverlayDaemonTransport",
]
