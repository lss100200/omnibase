"""P34.6 transaction helper and no-auto-replay state-machine tests."""

from __future__ import annotations

from uuid import uuid4

import pytest

from omnibase.workspace_data.models import WorkspaceDataEffect
from omnibase.workspace_data.service import (
    WorkspaceDataConflict,
    WorkspaceDataDenied,
    WorkspaceDataNotFound,
    canonical_digest,
    operation_request_hash,
    require_digest,
    transition_effect,
)


def _effect() -> WorkspaceDataEffect:
    return WorkspaceDataEffect(
        id=str(uuid4()),
        tenant_id=str(uuid4()),
        workspace_id=str(uuid4()),
        resource_id=str(uuid4()),
        operation_id=str(uuid4()),
        sequence=1,
        effect_kind="artifact_put",
        binding_digest="a" * 64,
        state="pending",
        version=1,
    )


def test_canonical_hashes_are_stable_and_kind_bound() -> None:
    assert canonical_digest({"b": 2, "a": 1}) == canonical_digest({"a": 1, "b": 2})
    first = operation_request_hash("artifact.stage", {"resource": "x"})
    second = operation_request_hash("artifact.delete", {"resource": "x"})
    assert len(first) == 64
    assert first != second
    require_digest(first, "request_hash")
    with pytest.raises(ValueError):
        require_digest("A" * 64, "request_hash")


def test_effect_moves_once_from_pending_to_committed() -> None:
    effect = _effect()
    transition_effect(effect, target_state="committed", receipt_digest="b" * 64)
    assert effect.state == "committed"
    assert effect.receipt_digest == "b" * 64
    assert effect.version == 2
    transition_effect(effect, target_state="committed", receipt_digest="b" * 64)
    with pytest.raises(WorkspaceDataConflict, match="receipt drift"):
        transition_effect(effect, target_state="committed", receipt_digest="c" * 64)
    with pytest.raises(WorkspaceDataConflict, match="terminal"):
        transition_effect(effect, target_state="failed", reason_code="provider.failed")


def test_unknown_effect_is_terminal_and_never_returns_to_pending() -> None:
    effect = _effect()
    transition_effect(
        effect,
        target_state="unknown",
        reason_code="provider.outcome_unknown",
    )
    assert effect.state == "unknown"
    with pytest.raises(WorkspaceDataConflict, match="terminal"):
        transition_effect(effect, target_state="pending")
    with pytest.raises(WorkspaceDataConflict, match="terminal"):
        transition_effect(effect, target_state="committed", receipt_digest="d" * 64)


def test_effect_rejects_unclosed_states_and_sensitive_reason_text() -> None:
    effect = _effect()
    with pytest.raises(ValueError, match="unsupported"):
        transition_effect(effect, target_state="retrying")
    with pytest.raises(ValueError, match="reason_code"):
        transition_effect(effect, target_state="failed", reason_code="raw SQL: password")


def test_authorization_denial_uses_idor_safe_not_found_semantics() -> None:
    assert issubclass(WorkspaceDataDenied, WorkspaceDataNotFound)
