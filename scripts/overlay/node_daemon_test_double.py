"""Disposable mTLS Node-Daemon used by the P34.5C integration Gate.

This is not a production daemon.  It implements the real HTTP/mTLS adapter
wire contract, durable operation replay and deterministic fault injection in
an isolated Compose project.  It never joins a real Overlay or receives a raw
Headscale/Tailscale credential.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import stat
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class HeadscaleProviderError(RuntimeError):
    """Redacted disposable-provider failure; never includes response bodies."""


class HeadscaleControlPlane:
    """Minimal Headscale 0.26 API client used only inside the disposable Gate."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key_file: Path,
        user_name: str,
        state: DurableState,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "headscale"
            or parsed.port != 8080
            or parsed.path not in {"", "/"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("disposable Headscale URL rejected")
        if not user_name or len(user_name) > 63:
            raise ValueError("disposable Headscale user rejected")
        self._base_url = base_url.rstrip("/")
        self._api_key_file = api_key_file
        self._user_name = user_name
        self._state = state
        self._user_id: str | None = None

    def _api_key(self) -> str:
        path = self._api_key_file
        try:
            file_stat = path.lstat()
        except FileNotFoundError as exc:
            raise HeadscaleProviderError("headscale_api_key_unavailable") from exc
        if (
            path.is_symlink()
            or not path.is_file()
            or file_stat.st_uid != os.geteuid()
            or stat.S_IMODE(file_stat.st_mode) & 0o077
        ):
            raise HeadscaleProviderError("headscale_api_key_file_rejected")
        value = path.read_text(encoding="utf-8").strip()
        if (
            not value
            or len(value) > 512
            or any(character.isspace() for character in value)
        ):
            raise HeadscaleProviderError("headscale_api_key_rejected")
        return value

    def _request(
        self,
        *,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        mutation: bool = False,
    ) -> dict[str, Any]:
        encoded = None if payload is None else _canonical(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=encoded,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key()}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5.0) as response:
                raw = response.read(1_048_577)
                if len(raw) > 1_048_576:
                    raise HeadscaleProviderError("headscale_response_too_large")
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
        ) as exc:
            raise HeadscaleProviderError("headscale_request_failed") from exc
        try:
            decoded = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise HeadscaleProviderError("headscale_response_invalid") from exc
        if not isinstance(decoded, dict) or not all(
            isinstance(key, str) for key in decoded
        ):
            raise HeadscaleProviderError("headscale_response_invalid")
        counter = (
            "provider_api_mutation_count" if mutation else "provider_api_query_count"
        )
        self._state.value[counter] = int(self._state.value[counter]) + 1
        return decoded

    def _resolve_user_id(self) -> str:
        if self._user_id is not None:
            return self._user_id
        decoded = self._request(method="GET", path="/api/v1/user")
        users = decoded.get("users")
        if not isinstance(users, list):
            raise HeadscaleProviderError("headscale_user_response_invalid")
        matches = [
            item
            for item in users
            if isinstance(item, dict) and item.get("name") == self._user_name
        ]
        if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
            raise HeadscaleProviderError("headscale_user_not_unique")
        self._user_id = str(matches[0]["id"])
        return self._user_id

    def _records(self) -> list[dict[str, Any]]:
        user_id = self._resolve_user_id()
        decoded = self._request(
            method="GET",
            path=f"/api/v1/preauthkey?{urlencode({'user': user_id})}",
        )
        records = decoded.get("preAuthKeys")
        if not isinstance(records, list) or not all(
            isinstance(item, dict) for item in records
        ):
            raise HeadscaleProviderError("headscale_preauth_response_invalid")
        return records

    @staticmethod
    def _active(record: dict[str, Any]) -> bool:
        expiration = record.get("expiration")
        if not isinstance(expiration, str):
            return False
        try:
            expires_at = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
        except ValueError:
            return False
        return not bool(record.get("used")) and expires_at > datetime.now(UTC)

    def _record(self, record_id: str) -> dict[str, Any] | None:
        matches = [
            record for record in self._records() if str(record.get("id")) == record_id
        ]
        if len(matches) > 1:
            raise HeadscaleProviderError("headscale_record_not_unique")
        return matches[0] if matches else None

    def create_record(self) -> str:
        user_id = self._resolve_user_id()
        expiration = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        decoded = self._request(
            method="POST",
            path="/api/v1/preauthkey",
            payload={
                "aclTags": [],
                "ephemeral": True,
                "expiration": expiration,
                "reusable": False,
                "user": user_id,
            },
            mutation=True,
        )
        record = decoded.get("preAuthKey")
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise HeadscaleProviderError("headscale_create_response_invalid")
        return str(record["id"])

    def expire_record(self, record_id: str) -> None:
        record = self._record(record_id)
        if record is None:
            raise HeadscaleProviderError("headscale_record_missing")
        raw_key = record.get("key")
        if not isinstance(raw_key, str) or not raw_key:
            raise HeadscaleProviderError("headscale_record_key_missing")
        self._request(
            method="POST",
            path="/api/v1/preauthkey/expire",
            payload={"key": raw_key, "user": self._resolve_user_id()},
            mutation=True,
        )

    def state_for(self, record_id: str | None) -> str:
        if record_id is None:
            return "revoked"
        record = self._record(record_id)
        return "active" if record is not None and self._active(record) else "revoked"

    def redacted_records(self, record_ids: list[str]) -> list[dict[str, object]]:
        records = {str(record.get("id")): record for record in self._records()}
        return [
            {
                "active": self._active(records[record_id])
                if record_id in records
                else False,
                "record_id": record_id,
            }
            for record_id in record_ids
        ]


class DurableState:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self._path.exists():
            self.value = json.loads(self._path.read_text(encoding="utf-8"))
        else:
            self.value = {
                "credential_generation": 0,
                "highest_fencing": None,
                "operations": {},
                "provider_api_mutation_count": 0,
                "provider_api_query_count": 0,
                "provider_current_record_id": None,
                "provider_record_ids": [],
                "request_count": 0,
                "state": "revoked",
            }
            self.save()
        self.value.setdefault("provider_api_mutation_count", 0)
        self.value.setdefault("provider_api_query_count", 0)
        self.value.setdefault("provider_current_record_id", None)
        self.value.setdefault("provider_record_ids", [])

    def save(self) -> None:
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(_canonical(self.value), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._path)


class NodeDaemonHandler(BaseHTTPRequestHandler):
    server: NodeDaemonServer

    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args

    def _json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length < 1 or length > 1_048_576:
            raise ValueError("invalid body length")
        decoded = json.loads(self.rfile.read(length))
        if not isinstance(decoded, dict):
            raise ValueError("body must be an object")
        return decoded

    def _send(self, status: int, body: dict[str, Any]) -> None:
        encoded = _canonical(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._json_body()
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "invalid_request"})
            return
        if self.path == "/test/fault":
            mode = body.get("mode")
            if mode not in {"none", "drop_after_commit"}:
                self._send(400, {"error": "invalid_fault"})
                return
            self.server.fault_mode = str(mode)
            self._send(200, {"accepted": True})
            return
        if self.path == "/test/state":
            try:
                state = dict(self.server.state.value)
                state["provider_records"] = self.server.provider.redacted_records(
                    list(state["provider_record_ids"])
                )
                self.server.state.save()
            except HeadscaleProviderError:
                self.server.state.save()
                self._send(503, {"error": "provider_unavailable"})
                return
            self._send(200, state)
            return
        action = self.path.removeprefix("/v1/overlay/")
        if action not in {"activate", "rotate", "revoke", "status"}:
            self._send(404, {"error": "not_found"})
            return
        self._handle_overlay(action=action, body=body)

    def _handle_overlay(self, *, action: str, body: dict[str, Any]) -> None:
        binding = body.get("binding")
        publication = body.get("publication")
        if not isinstance(binding, dict) or not isinstance(publication, dict):
            self._send(400, {"error": "binding_required"})
            return
        serialized = _canonical(body).lower()
        if publication.get("mode") != "broker_logical_service" or any(
            forbidden in serialized
            for forbidden in (
                '"address"',
                '"route"',
                '"provider_handle"',
                '"auth_key"',
                "tskey-",
                '"direct_endpoint"',
            )
        ):
            self._send(409, {"error": "direct_endpoint_rejected"})
            return
        operation_id = body.get("operation_id")
        if not isinstance(operation_id, str):
            self._send(400, {"error": "operation_id_required"})
            return
        state = self.server.state.value
        state["request_count"] = int(state["request_count"]) + 1
        operations = state["operations"]
        if action != "status" and operation_id in operations:
            self.server.state.save()
            self._send(200, operations[operation_id])
            return
        fencing = (
            int(binding["workspace_generation"]),
            int(binding["service_generation"]),
            int(binding["peer_fencing_token"]),
            int(binding["network_fencing_token"]),
            int(binding["source_node_fencing_token"]),
            int(binding["target_node_fencing_token"]),
        )
        highest = state.get("highest_fencing")
        if highest is not None and fencing < tuple(highest):
            self.server.state.save()
            self._send(409, {"error": "stale_fencing"})
            return
        state["highest_fencing"] = list(fencing)
        credential = body.get("credential")
        credential_generation: int | None = None
        if action in {"activate", "rotate"}:
            if not isinstance(credential, dict):
                self._send(400, {"error": "credential_reference_required"})
                return
            reference = credential.get("reference")
            if not isinstance(reference, str) or not reference.startswith(
                "omnibase-secret://overlay/"
            ):
                self._send(409, {"error": "raw_credential_rejected"})
                return
            credential_generation = int(credential["rotation_generation"])
            if credential_generation <= int(state["credential_generation"]):
                self._send(409, {"error": "credential_generation_stale"})
                return
        try:
            current_record_id = state.get("provider_current_record_id")
            if current_record_id is not None and not isinstance(current_record_id, str):
                raise HeadscaleProviderError("headscale_record_state_invalid")
            if action == "activate":
                new_record_id = self.server.provider.create_record()
                state["provider_current_record_id"] = new_record_id
                state["provider_record_ids"].append(new_record_id)
                state["state"] = self.server.provider.state_for(new_record_id)
                if state["state"] != "active":
                    raise HeadscaleProviderError("headscale_activate_not_observed")
                state["credential_generation"] = credential_generation
            elif action == "rotate":
                if current_record_id is None:
                    raise HeadscaleProviderError("headscale_current_record_missing")
                new_record_id = self.server.provider.create_record()
                self.server.provider.expire_record(current_record_id)
                if self.server.provider.state_for(current_record_id) != "revoked":
                    raise HeadscaleProviderError("headscale_old_record_still_active")
                state["provider_current_record_id"] = new_record_id
                state["provider_record_ids"].append(new_record_id)
                state["state"] = self.server.provider.state_for(new_record_id)
                if state["state"] != "active":
                    raise HeadscaleProviderError("headscale_rotate_not_observed")
                state["credential_generation"] = credential_generation
            elif action == "revoke":
                if current_record_id is not None:
                    self.server.provider.expire_record(current_record_id)
                state["state"] = self.server.provider.state_for(current_record_id)
                if state["state"] != "revoked":
                    raise HeadscaleProviderError("headscale_revoke_not_observed")
            else:
                state["state"] = self.server.provider.state_for(current_record_id)
        except HeadscaleProviderError:
            self.server.state.save()
            self._send(503, {"error": "provider_unavailable"})
            return
        receipt_without_digest = {
            "action": action,
            "operation_id": operation_id,
            "provider": body.get("provider"),
            "state": state["state"],
            "network_lease_id": binding["network_lease_id"],
            "binding_digest": binding["verification_digest"],
            "workspace_generation": fencing[0],
            "service_generation": fencing[1],
            "peer_fencing_token": fencing[2],
            "network_fencing_token": fencing[3],
            "source_node_fencing_token": fencing[4],
            "target_node_fencing_token": fencing[5],
            "source_daemon_attestation_digest": body["source_daemon"][
                "attestation_digest"
            ],
            "target_daemon_attestation_digest": body["target_daemon"][
                "attestation_digest"
            ],
            "credential_generation": credential_generation,
            "observed_at": datetime.now(UTC).isoformat(),
        }
        receipt = dict(receipt_without_digest)
        receipt["receipt_digest"] = hashlib.sha256(
            _canonical(receipt_without_digest).encode("utf-8")
        ).hexdigest()
        if action != "status":
            operations[operation_id] = receipt
        self.server.state.save()
        if self.server.fault_mode == "drop_after_commit" and action != "status":
            self.server.fault_mode = "none"
            self.close_connection = True
            self.connection.shutdown(1)
            self.connection.close()
            return
        self._send(200, receipt)


class NodeDaemonServer(ThreadingHTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        state: DurableState,
        provider: HeadscaleControlPlane,
    ) -> None:
        super().__init__(address, NodeDaemonHandler)
        self.state = state
        self.provider = provider
        self.fault_mode = "none"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--ca", required=True)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--headscale-url", required=True)
    parser.add_argument("--headscale-api-key-file", required=True)
    parser.add_argument("--headscale-user", required=True)
    args = parser.parse_args()

    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=args.ca)
    context.load_cert_chain(certfile=args.cert, keyfile=args.key)

    state = DurableState(Path(args.state))
    provider = HeadscaleControlPlane(
        base_url=args.headscale_url,
        api_key_file=Path(args.headscale_api_key_file),
        user_name=args.headscale_user,
        state=state,
    )
    server = NodeDaemonServer((args.listen, args.port), state, provider)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
