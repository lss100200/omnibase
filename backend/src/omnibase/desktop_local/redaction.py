"""Secret-free public error text for desktop-local Provider and conversation paths."""

from __future__ import annotations

import re

_BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_KEY_ASSIGNMENT_PATTERN = re.compile(
    r"\b(api[_-]?key|authorization|password|secret|token)\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
_USERINFO_PATTERN = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)
_QUERY_SECRET_PATTERN = re.compile(
    r"([?&](?:key|api[_-]?key|token|secret|password|access_token)=)[^&\s]+",
    re.IGNORECASE,
)
_SK_PREFIX_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")

_PUBLIC_STATUS_COPY = {
    "desktop_invocation_cancelled": "生成已停止",
    "desktop_invocation_failed": "调用失败",
    "desktop_invocation_interrupted": "调用状态未知",
    "desktop_provider_timeout": "Provider 读取超时",
    "desktop_provider_unreachable": "Provider 不可达",
    "desktop_provider_unauthorized": "凭据被拒绝",
    "desktop_provider_not_found": "模型或接口不存在",
    "desktop_provider_response_invalid": "Provider 返回了无法使用的响应",
    "desktop_provider_response_too_large": "Provider 响应超过上限",
    "desktop_provider_endpoint_invalid": "Provider 地址不符合安全边界",
}


def redact_public_text(value: str, *, extra_secrets: tuple[str, ...] = ()) -> str:
    output = _CONTROL_PATTERN.sub(" ", value)
    output = _BEARER_PATTERN.sub("Bearer [REDACTED]", output)
    output = _KEY_ASSIGNMENT_PATTERN.sub(r"\1=[REDACTED]", output)
    output = _USERINFO_PATTERN.sub(r"\1[REDACTED]@", output)
    output = _QUERY_SECRET_PATTERN.sub(r"\1[REDACTED]", output)
    output = _SK_PREFIX_PATTERN.sub("[REDACTED]", output)
    for secret in extra_secrets:
        if secret:
            output = output.replace(secret, "[REDACTED]")
    return re.sub(r"\s+", " ", output).strip()[:256]


def public_error_message(
    code: str, detail: str = "", *, extra_secrets: tuple[str, ...] = ()
) -> str:
    if code in _PUBLIC_STATUS_COPY:
        return _PUBLIC_STATUS_COPY[code]
    redacted = redact_public_text(detail, extra_secrets=extra_secrets)
    if not redacted:
        return "调用失败"
    return redacted
