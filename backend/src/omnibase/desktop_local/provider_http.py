"""Stdlib HTTPS Chat Completions adapter for desktop-local Providers.

The frozen desktop backend excludes openai and does not ship httpx. This
adapter talks to user-configured endpoints with timeout, cancel, response-size
caps, and secret-free errors.
"""

from __future__ import annotations

import http.client
import json
import select
import socket
import ssl
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from omnibase.desktop_local.endpoint import (
    ResolvedDesktopEndpoint,
    pinned_connect_addrs,
)
from omnibase.desktop_local.errors import DesktopLocalError
from omnibase.desktop_local.family import DesktopModelAdaptation
from omnibase.desktop_local.redaction import public_error_message, redact_public_text

_MAX_TEST_BYTES = 64 * 1024
_MAX_STREAM_BYTES = 8 * 1024 * 1024
_MAX_ERROR_BYTES = 8 * 1024


class DesktopProviderCallError(DesktopLocalError):
    def __init__(self, code: str, message: str = "") -> None:
        self.public_message = message or public_error_message(code)
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ProviderStreamEvent:
    kind: str
    text: str = ""
    actual_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


def _status_code_to_error(status: int) -> str:
    if status in {401, 403}:
        return "desktop_provider_unauthorized"
    if status == 404:
        return "desktop_provider_not_found"
    if status in {408, 504}:
        return "desktop_provider_timeout"
    return "desktop_provider_response_invalid"


def _connect_pinned(
    endpoint: ResolvedDesktopEndpoint,
    address: str,
    timeout_seconds: float,
) -> http.client.HTTPConnection:
    sock = socket.create_connection((address, endpoint.port), timeout_seconds)
    if endpoint.scheme == "http":
        connection = http.client.HTTPConnection(
            address,
            endpoint.port,
            timeout=timeout_seconds,
        )
        connection.sock = sock
        return connection
    context = ssl.create_default_context()
    connection = http.client.HTTPSConnection(
        address,
        endpoint.port,
        timeout=timeout_seconds,
        context=context,
    )
    connection.sock = context.wrap_socket(sock, server_hostname=endpoint.hostname)
    return connection


def _open_connection(
    endpoint: ResolvedDesktopEndpoint,
    timeout_seconds: float,
) -> http.client.HTTPConnection:
    last_error: OSError | ssl.SSLError | None = None
    for address in pinned_connect_addrs(endpoint):
        try:
            return _connect_pinned(endpoint, address, timeout_seconds)
        except (OSError, ssl.SSLError) as exc:
            last_error = exc
            continue
    raise DesktopProviderCallError("desktop_provider_unreachable") from last_error


def _read_capped(response: http.client.HTTPResponse, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        piece = response.read(4096)
        if not piece:
            break
        total += len(piece)
        if total > limit:
            raise DesktopProviderCallError("desktop_provider_response_too_large")
        chunks.append(piece)
    return b"".join(chunks)


def _parse_sse_block(block: str) -> tuple[str, str]:
    event_name = "message"
    data_lines: list[str] = []
    for line in block.split("\n"):
        if line.startswith("event:"):
            event_name = line[6:].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    return event_name, "\n".join(data_lines)


def _usage_from_payload(payload: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None, None

    def _int(name: str) -> int | None:
        value = usage.get(name)
        return value if isinstance(value, int) and value >= 0 else None

    return _int("prompt_tokens"), _int("completion_tokens"), _int("total_tokens")


def _peek_pending(fp: object) -> bytes:
    peek = getattr(fp, "peek", None)
    if not callable(peek):
        return b""
    try:
        pending = peek(1)
    except (OSError, ValueError):
        return b""
    return pending if isinstance(pending, (bytes, bytearray)) else b""


def _iter_sse_events(  # noqa: C901 - cancel, timeout and bounded SSE parse
    response: http.client.HTTPResponse,
    sock: socket.socket | None,
    *,
    secret: str,
    limit: int,
    cancelled: Callable[[], bool],
    deadline: float,
) -> Iterator[ProviderStreamEvent]:
    buffer = ""
    total = 0
    terminal_proof = False
    fp = getattr(response, "fp", None)
    while True:
        if cancelled():
            raise DesktopProviderCallError(
                "desktop_invocation_cancelled",
                public_error_message("desktop_invocation_cancelled"),
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DesktopProviderCallError("desktop_provider_timeout")
        pending = _peek_pending(fp) if fp is not None else b""
        if not pending and sock is not None:
            readable, _, _ = select.select([sock], [], [], min(0.25, remaining))
            if not readable:
                continue
        try:
            chunk = response.read(1024)
        except (TimeoutError, BlockingIOError):
            continue
        except ValueError:
            break
        except OSError as exc:
            if cancelled():
                raise DesktopProviderCallError(
                    "desktop_invocation_cancelled",
                    public_error_message("desktop_invocation_cancelled"),
                ) from None
            if "timed out" in str(exc).lower():
                continue
            raise
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise DesktopProviderCallError("desktop_provider_response_too_large")
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n\n" in buffer or "\r\n\r\n" in buffer:
            if "\r\n\r\n" in buffer and (
                "\n\n" not in buffer or buffer.find("\r\n\r\n") < buffer.find("\n\n")
            ):
                raw, buffer = buffer.split("\r\n\r\n", 1)
            else:
                raw, buffer = buffer.split("\n\n", 1)
            event_name, data = _parse_sse_block(raw.replace("\r\n", "\n"))
            if data == "[DONE]":
                terminal_proof = True
                continue
            if not data:
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                raise DesktopProviderCallError("desktop_provider_response_invalid") from None
            if not isinstance(payload, dict):
                raise DesktopProviderCallError("desktop_provider_response_invalid")
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                text = message if isinstance(message, str) else ""
                raise DesktopProviderCallError(
                    "desktop_provider_response_invalid",
                    public_error_message(
                        "desktop_provider_response_invalid",
                        text,
                        extra_secrets=(secret,),
                    ),
                )
            actual = payload.get("model")
            actual_model = actual if isinstance(actual, str) and actual else None
            input_tokens, output_tokens, total_tokens = _usage_from_payload(payload)
            if input_tokens is not None or output_tokens is not None:
                yield ProviderStreamEvent(
                    kind="usage",
                    actual_model=actual_model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                )
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield ProviderStreamEvent(
                        kind="delta",
                        text=content,
                        actual_model=actual_model,
                    )
            finish = choice.get("finish_reason")
            if isinstance(finish, str) and finish:
                terminal_proof = True
                yield ProviderStreamEvent(kind="finish", actual_model=actual_model)
            if event_name == "error":
                raise DesktopProviderCallError("desktop_provider_response_invalid")
    if not terminal_proof:
        raise DesktopProviderCallError("desktop_provider_stream_incomplete")


def _chat_payload(
    *,
    model_name: str,
    messages: tuple[ChatMessage, ...],
    adaptation: DesktopModelAdaptation,
    stream: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": model_name,
        "messages": [{"role": item.role, "content": item.content} for item in messages],
        "stream": stream,
    }
    if adaptation.family == "openai":
        payload["max_completion_tokens"] = adaptation.max_output_tokens
    else:
        payload["max_tokens"] = adaptation.max_output_tokens
    if adaptation.family == "generic-openai-compatible":
        payload["temperature"] = 0.2
    payload.update(adaptation.extra_payload)
    return payload


def _json_media_type(content_type: str | None) -> bool:
    media = (content_type or "").split(";", 1)[0].strip().lower()
    return media == "application/json"


def _usable_assistant_content(parsed: dict[str, Any]) -> str:
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        raise DesktopProviderCallError("desktop_provider_response_invalid")
    first = choices[0]
    if not isinstance(first, dict):
        raise DesktopProviderCallError("desktop_provider_response_invalid")
    message = first.get("message")
    if not isinstance(message, dict):
        raise DesktopProviderCallError("desktop_provider_response_invalid")
    role = message.get("role")
    if role is not None and role != "assistant":
        raise DesktopProviderCallError("desktop_provider_response_invalid")
    content = message.get("content")
    if not isinstance(content, str) or not content:
        raise DesktopProviderCallError("desktop_provider_response_invalid")
    return content


def test_provider_endpoint(
    endpoint: ResolvedDesktopEndpoint,
    *,
    secret: str,
    model_name: str,
    adaptation: DesktopModelAdaptation,
    timeout_seconds: float,
    cancelled: Callable[[], bool],
) -> dict[str, object]:
    started = time.monotonic()
    payload = _chat_payload(
        model_name=model_name,
        messages=(ChatMessage(role="user", content="ping"),),
        adaptation=adaptation,
        stream=False,
    )
    connection: http.client.HTTPConnection | None = None
    try:
        if cancelled():
            raise DesktopProviderCallError(
                "desktop_invocation_cancelled",
                public_error_message("desktop_invocation_cancelled"),
            )
        connection = _open_connection(endpoint, timeout_seconds)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Host": endpoint.hostname,
            "Connection": "close",
        }
        connection.request("POST", endpoint.chat_path, body=body, headers=headers)
        response = connection.getresponse()
        raw = _read_capped(response, _MAX_TEST_BYTES if response.status < 400 else _MAX_ERROR_BYTES)
        if cancelled():
            raise DesktopProviderCallError(
                "desktop_invocation_cancelled",
                public_error_message("desktop_invocation_cancelled"),
            )
        if response.status >= 400:
            raise DesktopProviderCallError(_status_code_to_error(response.status))
        if not _json_media_type(response.getheader("content-type")):
            raise DesktopProviderCallError("desktop_provider_response_invalid")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise DesktopProviderCallError("desktop_provider_response_invalid") from None
        if not isinstance(parsed, dict):
            raise DesktopProviderCallError("desktop_provider_response_invalid")
        _usable_assistant_content(parsed)
        actual = parsed.get("model")
        if isinstance(actual, str) and actual.strip():
            actual_model: str | None = actual.strip()
            identity_proven = True
        else:
            actual_model = None
            identity_proven = False
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": True,
            "requested_model": model_name,
            "actual_model": actual_model,
            "identity_proven": identity_proven,
            "family": adaptation.family,
            "latency_ms": latency_ms,
        }
    except DesktopProviderCallError:
        raise
    except TimeoutError:
        raise DesktopProviderCallError("desktop_provider_timeout") from None
    except (OSError, http.client.HTTPException, ssl.SSLError):
        raise DesktopProviderCallError("desktop_provider_unreachable") from None
    finally:
        if connection is not None:
            connection.close()


def stream_provider_chat(
    endpoint: ResolvedDesktopEndpoint,
    *,
    secret: str,
    model_name: str,
    messages: tuple[ChatMessage, ...],
    adaptation: DesktopModelAdaptation,
    timeout_seconds: float,
    cancelled: Callable[[], bool],
) -> Iterator[ProviderStreamEvent]:
    payload = _chat_payload(
        model_name=model_name,
        messages=messages,
        adaptation=adaptation,
        stream=True,
    )
    connection: http.client.HTTPConnection | None = None
    try:
        connection = _open_connection(endpoint, timeout_seconds)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Host": endpoint.hostname,
            "Connection": "close",
        }
        connection.request("POST", endpoint.chat_path, body=body, headers=headers)
        response = connection.getresponse()
        if response.status >= 400:
            raw = _read_capped(response, _MAX_ERROR_BYTES)
            detail = redact_public_text(
                raw.decode("utf-8", errors="replace"),
                extra_secrets=(secret,),
            )
            raise DesktopProviderCallError(_status_code_to_error(response.status), detail)
        encoding = (response.getheader("content-encoding") or "identity").lower()
        if encoding not in {"identity", ""}:
            raise DesktopProviderCallError("desktop_provider_response_invalid")
        yield from _iter_sse_events(
            response,
            connection.sock if connection is not None else None,
            secret=secret,
            limit=_MAX_STREAM_BYTES,
            cancelled=cancelled,
            deadline=time.monotonic() + timeout_seconds,
        )
    except DesktopProviderCallError:
        raise
    except TimeoutError:
        raise DesktopProviderCallError("desktop_provider_timeout") from None
    except OSError:
        if cancelled():
            raise DesktopProviderCallError(
                "desktop_invocation_cancelled",
                public_error_message("desktop_invocation_cancelled"),
            ) from None
        raise DesktopProviderCallError("desktop_provider_unreachable") from None
    except (http.client.HTTPException, ssl.SSLError):
        raise DesktopProviderCallError("desktop_provider_unreachable") from None
    finally:
        if connection is not None:
            connection.close()
