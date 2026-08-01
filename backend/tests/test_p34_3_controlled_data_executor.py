"""Transaction, authorization, and replay tests for the controlled CRUD executor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from omnibase.control_plane.models import (
    IdempotencyRecord,
    OperationRecord,
    ResourceRecord,
)
from omnibase.controlled_data.crud import (
    MutationBudgetExceeded,
    MutationColumnBinding,
    TrustedMutationLocator,
    canonical_request_hash,
)
from omnibase.controlled_data.crud_contracts import (
    InsertMutationRequest,
    UpdateMutationRequest,
)
from omnibase.controlled_data.executor import (
    CONTROLLED_CRUD_LOCK_ORDER,
    ControlledCrudAuthorizationDenied,
    ControlledCrudCommand,
    ControlledCrudConflict,
    ControlledCrudDatabaseFailure,
    ControlledCrudExecutionError,
    ControlledCrudIdempotencyConflict,
    ControlledCrudSuccessAuditError,
    TrustedUserRbacDecision,
    execute_controlled_crud,
    execute_controlled_crud_in_transaction,
)
from omnibase.controlled_data.identifiers import column_identifier, table_identifier
from omnibase.controlled_data.models import (
    AuthorizationContext,
    DataColumnBinding,
    DataTableBinding,
)
from omnibase.db.models import Tenant

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
WORKSPACE_ID = UUID("20000000-0000-0000-0000-000000000001")
ACTOR_ID = UUID("30000000-0000-0000-0000-000000000001")
RESOURCE_ID = UUID("40000000-0000-0000-0000-000000000001")
BINDING_ID = UUID("50000000-0000-0000-0000-000000000001")
COLUMN_ID = UUID("60000000-0000-0000-0000-000000000001")
AUTH_ID = UUID("70000000-0000-0000-0000-000000000001")
OPERATION_ID = UUID("80000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)

_UNSET = object()


class _Result:
    def __init__(
        self,
        *,
        one: object = _UNSET,
        rows: list[object] | None = None,
        rowcount: int = 0,
    ) -> None:
        self._one = one
        self._rows = [] if rows is None else rows
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> object | None:
        return None if self._one is _UNSET else self._one

    def scalar_one(self) -> object:
        if self._one is _UNSET:
            raise AssertionError("test result has no scalar")
        return self._one

    def scalars(self) -> _Result:
        return self

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> object | None:
        return None if self._one is _UNSET else self._one

    def all(self) -> list[object]:
        return self._rows


def _locator() -> TrustedMutationLocator:
    column = MutationColumnBinding(
        logical_id=COLUMN_ID,
        physical_name=column_identifier(COLUMN_ID),
        data_type="string",
        type_args={"max_length": 500},
        nullable=False,
    )
    return TrustedMutationLocator(
        tenant_schema="tenant_deadbeef",
        table_binding_id=BINDING_ID,
        resource_id=RESOURCE_ID,
        resource_version=4,
        physical_table_name=table_identifier(RESOURCE_ID),
        columns={COLUMN_ID: column},
    )


def _update_request(*, max_rows: int = 2, value: str = "updated") -> UpdateMutationRequest:
    return UpdateMutationRequest.model_validate(
        {
            "resource_id": RESOURCE_ID,
            "resource_version": 4,
            "idempotency_key": "idem.executor-0001",
            "timeout_ms": 2_000,
            "max_rows": max_rows,
            "predicate": {
                "kind": "compare",
                "column_id": COLUMN_ID,
                "op": "eq",
                "value": "before",
            },
            "values": {COLUMN_ID: value},
        }
    )


def _insert_request() -> InsertMutationRequest:
    return InsertMutationRequest.model_validate(
        {
            "resource_id": RESOURCE_ID,
            "resource_version": 4,
            "idempotency_key": "idem.executor-0002",
            "timeout_ms": 2_000,
            "rows": [{COLUMN_ID: "inserted"}],
        }
    )


def _decision(action: str, **changes: object) -> TrustedUserRbacDecision:
    values: dict[str, object] = {
        "decision_id": uuid4(),
        "allowed": True,
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "actor_user_id": ACTOR_ID,
        "resource_id": RESOURCE_ID,
        "resource_version": 4,
        "action": action,
        "authorization_context_id": AUTH_ID,
        "source_version": 3,
        "snapshot_hash": "a" * 64,
        "roles": frozenset({"tenant_admin"}),
        "user_is_active": True,
        "tenant_is_active": True,
        "evaluated_at": NOW - timedelta(seconds=1),
        "expires_at": NOW + timedelta(seconds=29),
    }
    values.update(changes)
    return TrustedUserRbacDecision(**values)  # type: ignore[arg-type]


def _records(
    request_hash: str,
    action: str,
    *,
    operation_state: str = "queued",
) -> tuple[
    DataTableBinding,
    ResourceRecord,
    AuthorizationContext,
    OperationRecord,
    DataColumnBinding,
]:
    binding = DataTableBinding(
        id=str(BINDING_ID),
        tenant_id=str(TENANT_ID),
        resource_id=str(RESOURCE_ID),
        workspace_id=str(WORKSPACE_ID),
        display_name="Findings",
        policy_class="workspace_private",
        physical_table_name=table_identifier(RESOURCE_ID),
        state="active",
        resource_version=4,
        version=1,
        created_by_actor_id=str(ACTOR_ID),
    )
    resource = ResourceRecord(
        id=str(RESOURCE_ID),
        tenant_id=str(TENANT_ID),
        kind="controlled_table",
        owner_type="workspace",
        owner_id=str(WORKSPACE_ID),
        display_name="Findings",
        state="active",
        version=4,
        policy_class="workspace_private",
        resource_metadata={},
    )
    authorization = AuthorizationContext(
        id=str(AUTH_ID),
        tenant_id=str(TENANT_ID),
        workspace_id=str(WORKSPACE_ID),
        source="user_rbac",
        actor_user_id=str(ACTOR_ID),
        grant_id=None,
        role_snapshot=["tenant_admin"],
        actions=[action],
        resource_ids=[str(RESOURCE_ID)],
        source_version=3,
        snapshot_hash="a" * 64,
        live_recheck_required=True,
        created_at=NOW - timedelta(minutes=2),
        expires_at=NOW + timedelta(minutes=5),
    )
    operation = OperationRecord(
        id=str(OPERATION_ID),
        tenant_id=str(TENANT_ID),
        workspace_id=str(WORKSPACE_ID),
        actor_type="user",
        actor_id=str(ACTOR_ID),
        resource_id=str(RESOURCE_ID),
        resource_version=4,
        request_hash=request_hash,
        kind=action,
        state=operation_state,
        risk_level="R1",
        progress=0 if operation_state == "queued" else 100,
        attempt_count=0 if operation_state == "queued" else 1,
        version=1 if operation_state == "queued" else 3,
        operation_metadata={},
    )
    column = DataColumnBinding(
        id=str(COLUMN_ID),
        tenant_id=str(TENANT_ID),
        table_binding_id=str(BINDING_ID),
        display_name="Finding",
        physical_column_name=column_identifier(COLUMN_ID),
        data_type="string",
        type_args={"max_length": 500},
        nullable=False,
        ordinal=1,
        state="active",
        version=1,
    )
    return binding, resource, authorization, operation, column


def _idempotency(
    request_hash: str,
    *,
    state: str = "pending",
    response_ref: dict[str, object] | None = None,
) -> IdempotencyRecord:
    return IdempotencyRecord(
        id=str(uuid4()),
        tenant_id=str(TENANT_ID),
        actor_scope=f"user:{ACTOR_ID}:workspace:{WORKSPACE_ID}",
        operation_name="data.rows.update",
        key="idem.executor-0001",
        request_hash=request_hash,
        state=state,
        version=1,
        response_ref=response_ref,
        operation_id=str(OPERATION_ID),
        expires_at=NOW + timedelta(hours=24),
    )


def _command(
    request: UpdateMutationRequest | InsertMutationRequest,
    *,
    decision: TrustedUserRbacDecision | None = None,
) -> ControlledCrudCommand:
    action = "data.rows.insert" if request.kind == "insert" else "data.rows.update"
    return ControlledCrudCommand(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        actor_user_id=ACTOR_ID,
        authorization_context_id=AUTH_ID,
        operation_id=OPERATION_ID,
        locator=_locator(),
        request=request,
        decision=decision or _decision(action),  # type: ignore[arg-type]
        lock_timeout_ms=500,
    )


def _session(results: list[_Result]) -> tuple[MagicMock, MagicMock]:
    session = MagicMock(spec=Session)
    session.in_transaction.return_value = False
    session.execute.side_effect = results
    transaction = MagicMock()
    session.begin.return_value = transaction
    connection = MagicMock()
    session.connection.return_value = connection
    return session, connection


def _tenant(
    *,
    is_active: bool = True,
    schema_name: str = "tenant_deadbeef",
) -> Tenant:
    return Tenant(
        id=str(TENANT_ID),
        name="Executor tenant",
        schema_name=schema_name,
        slug="executor-tenant",
        is_default=False,
        is_active=is_active,
    )


def _user(
    *,
    user_id: UUID = ACTOR_ID,
    is_active: bool = True,
    is_tenant_admin: bool = True,
) -> dict[str, object]:
    return {
        "id": str(user_id),
        "is_active": is_active,
        "is_tenant_admin": is_tenant_admin,
    }


def _lock_results(
    records: tuple[object, object, object, object, object],
    *,
    tenant: Tenant | None = None,
    user: dict[str, object] | None = None,
) -> list[_Result]:
    binding, resource, authorization, operation, column = records
    return [
        _Result(one=tenant or _tenant()),
        _Result(one=user or _user()),
        _Result(one=resource),
        _Result(one=binding),
        _Result(rows=[column]),
        _Result(one=authorization),
        _Result(one=operation),
    ]


def _authorization_results(
    records: tuple[object, object, object, object, object],
    request_hash: str,
    *,
    user: dict[str, object] | None = None,
) -> list[_Result]:
    return [
        *_lock_results(records, user=user),
        _Result(one=NOW),
        _Result(one="new-idempotency-id"),
        _Result(one=_idempotency(request_hash)),
        _Result(one=NOW),
    ]


def test_update_executes_locks_preflight_version_recheck_and_apply_on_one_connection() -> None:
    request = _update_request()
    request_hash = canonical_request_hash(request)
    records = _records(request_hash, "data.rows.update")
    idempotency = _idempotency(request_hash)
    results = [
        *_lock_results(records),
        _Result(one=NOW),
        _Result(one="new-idempotency-id"),
        _Result(one=idempotency),
        _Result(one=NOW),
        _Result(rows=["(0,1)", "(0,2)"]),
        _Result(one=4),
        _Result(one=4),
        _Result(rowcount=2),
        _Result(one=NOW),
    ]
    session, connection = _session(results)

    result = execute_controlled_crud(
        session,
        _command(request),
        now=NOW + timedelta(days=365),
    )

    assert result.affected_rows == 2
    assert result.replayed is False
    assert result.as_safe_metadata() == records[3].result_ref
    assert records[3].state == "succeeded"
    assert idempotency.state == "completed"
    assert session.flush.call_count == 2
    assert session.connection.call_count == 2
    timeout_sql = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert timeout_sql == [
        "SET LOCAL statement_timeout = '2000ms'",
        "SET LOCAL lock_timeout = '500ms'",
    ]
    statements = [str(call.args[0]) for call in session.execute.call_args_list]
    assert CONTROLLED_CRUD_LOCK_ORDER == (
        "omnibase_meta.tenants",
        "tenant.users",
        "omnibase_meta.resource_registry",
        "omnibase_meta.data_table_bindings",
        "omnibase_meta.data_column_bindings",
        "omnibase_meta.authorization_contexts",
        "omnibase_meta.operations",
        "omnibase_meta.idempotency_records",
    )
    lock_targets = (
        "omnibase_meta.tenants",
        "tenant_deadbeef.users",
        "omnibase_meta.resource_registry",
        "omnibase_meta.data_table_bindings",
        "omnibase_meta.data_column_bindings",
        "omnibase_meta.authorization_contexts",
        "omnibase_meta.operations",
    )
    for statement, target in zip(statements[:7], lock_targets, strict=True):
        assert target in statement
        assert "FOR UPDATE" in statement
    assert all("FOR UPDATE" in statement for statement in statements[:7])
    assert "clock_timestamp" in statements[7]
    assert "idempotency_records" in statements[8]
    assert "idempotency_records" in statements[9]
    assert "FOR UPDATE" in statements[9]
    assert "clock_timestamp" in statements[10]
    assert "FOR UPDATE" in statements[11]
    assert table_identifier(RESOURCE_ID) in statements[14]
    assert "ctid" in statements[14]
    assert "clock_timestamp" in statements[15]
    safe_text = str(result.as_safe_metadata()).lower()
    for forbidden in ("ctid", "physical", "schema", "table_name", "row_token"):
        assert forbidden not in safe_text


def test_insert_rechecks_versions_and_updates_exact_row_count() -> None:
    request = _insert_request()
    request_hash = canonical_request_hash(request)
    records = _records(request_hash, "data.rows.insert")
    idempotency = _idempotency(request_hash)
    idempotency.operation_name = "data.rows.insert"
    idempotency.key = "idem.executor-0002"
    results = [
        *_lock_results(records),
        _Result(one=NOW),
        _Result(one="new-idempotency-id"),
        _Result(one=idempotency),
        _Result(one=NOW),
        _Result(one=4),
        _Result(one=4),
        _Result(rowcount=1),
        _Result(one=NOW),
    ]
    session, _ = _session(results)
    result = execute_controlled_crud(session, _command(request), now=NOW)
    assert result.action == "data.rows.insert"
    assert result.affected_rows == 1
    assert records[3].state == "succeeded"
    assert len(session.execute.call_args_list) == 15


def test_completed_exact_idempotency_replay_returns_safe_metadata_without_mutation() -> None:
    request = _update_request()
    request_hash = canonical_request_hash(request)
    records = _records(request_hash, "data.rows.update", operation_state="succeeded")
    metadata = {
        "operation_id": str(OPERATION_ID),
        "resource_id": str(RESOURCE_ID),
        "resource_version": 4,
        "action": "data.rows.update",
        "affected_rows": 2,
        "request_hash": request_hash,
        "status": "succeeded",
    }
    records[3].result_ref = metadata
    idempotency = _idempotency(request_hash, state="completed", response_ref=metadata)
    results = [
        *_lock_results(records),
        _Result(one=NOW),
        _Result(),
        _Result(one=idempotency),
        _Result(one=NOW),
    ]
    session, _ = _session(results)
    result = execute_controlled_crud(session, _command(request), now=NOW)
    assert result.replayed is True
    assert result.affected_rows == 2
    assert len(session.execute.call_args_list) == 11
    session.flush.assert_not_called()


def test_in_transaction_entry_requires_active_caller_transaction() -> None:
    session = MagicMock(spec=Session)
    session.in_transaction.return_value = False
    with pytest.raises(ControlledCrudExecutionError, match="active transaction"):
        execute_controlled_crud_in_transaction(session, _command(_update_request()))
    session.begin.assert_not_called()


def test_in_transaction_entry_uses_existing_transaction_without_commit(monkeypatch) -> None:
    session = MagicMock(spec=Session)
    session.in_transaction.return_value = True
    command = _command(_update_request())
    prepared = MagicMock()
    expected = MagicMock()
    core = MagicMock(return_value=expected)
    monkeypatch.setattr("omnibase.controlled_data.executor._prepare", lambda *_args: prepared)
    monkeypatch.setattr("omnibase.controlled_data.executor._execute_in_transaction", core)

    result = execute_controlled_crud_in_transaction(session, command)

    assert result is expected
    session.begin.assert_not_called()
    core.assert_called_once_with(
        session,
        command=command,
        prepared=prepared,
        action="data.rows.update",
        success_audit_hook=None,
    )


def test_completed_replay_invokes_atomic_success_audit_before_commit() -> None:
    request = _update_request()
    request_hash = canonical_request_hash(request)
    records = _records(request_hash, "data.rows.update", operation_state="succeeded")
    metadata = {
        "operation_id": str(OPERATION_ID),
        "resource_id": str(RESOURCE_ID),
        "resource_version": 4,
        "action": "data.rows.update",
        "affected_rows": 2,
        "request_hash": request_hash,
        "status": "succeeded",
    }
    records[3].result_ref = metadata
    idempotency = _idempotency(request_hash, state="completed", response_ref=metadata)
    session, _ = _session(
        [
            *_lock_results(records),
            _Result(one=NOW),
            _Result(),
            _Result(one=idempotency),
            _Result(one=NOW),
        ]
    )
    hook = MagicMock()

    result = execute_controlled_crud(
        session,
        _command(request),
        now=NOW,
        success_audit_hook=hook,
    )

    hook.assert_called_once_with(session, result, records[3], idempotency)
    session.flush.assert_called_once_with()


def test_same_idempotency_key_with_different_hash_or_operation_fails_closed() -> None:
    request = _update_request(value="new-input")
    request_hash = canonical_request_hash(request)
    records = _records(request_hash, "data.rows.update")
    idempotency = _idempotency("f" * 64)
    replay_prefix = [
        *_lock_results(records),
        _Result(one=NOW),
        _Result(),
        _Result(one=idempotency),
        _Result(one=NOW),
    ]
    session, _ = _session(replay_prefix)
    with pytest.raises(ControlledCrudIdempotencyConflict, match="different input"):
        execute_controlled_crud(session, _command(request), now=NOW)

    idempotency.request_hash = request_hash
    idempotency.operation_id = str(uuid4())
    session, _ = _session(
        [
            *_lock_results(records),
            _Result(one=NOW),
            _Result(),
            _Result(one=idempotency),
            _Result(one=NOW),
        ]
    )
    with pytest.raises(ControlledCrudIdempotencyConflict, match="another operation"):
        execute_controlled_crud(session, _command(request), now=NOW)

    idempotency.operation_id = str(OPERATION_ID)
    idempotency.expires_at = NOW
    session, _ = _session(
        [
            *_lock_results(records),
            _Result(one=NOW),
            _Result(),
            _Result(one=idempotency),
            _Result(one=NOW),
        ]
    )
    with pytest.raises(ControlledCrudIdempotencyConflict, match="expired"):
        execute_controlled_crud(session, _command(request), now=NOW)


@pytest.mark.parametrize(
    "decision",
    [
        _decision("data.rows.update", allowed=False),
        _decision("data.rows.update", actor_user_id=uuid4()),
        _decision("data.rows.update", workspace_id=uuid4()),
        _decision("data.rows.delete"),
        _decision(
            "data.rows.update",
            evaluated_at=NOW - timedelta(seconds=30),
            expires_at=NOW - timedelta(seconds=1),
        ),
        _decision("data.rows.update", user_is_active=False),
        _decision("data.rows.update", snapshot_hash="b" * 64),
    ],
)
def test_live_rbac_decision_must_bind_every_scope_and_be_unexpired(
    decision: TrustedUserRbacDecision,
) -> None:
    request = _update_request()
    request_hash = canonical_request_hash(request)
    records = _records(request_hash, "data.rows.update")
    session, _ = _session(_authorization_results(records, request_hash))
    with pytest.raises(ControlledCrudAuthorizationDenied):
        execute_controlled_crud(
            session,
            _command(request, decision=decision),
            now=NOW,
        )
    assert len(session.execute.call_args_list) == 11


@pytest.mark.parametrize(
    "snapshot_hash",
    ["A" * 64, "g" * 64, "a" * 63],
)
def test_trusted_decision_snapshot_hash_is_exact_lowercase_sha256(
    snapshot_hash: str,
) -> None:
    with pytest.raises(ValueError, match="snapshot_hash"):
        _decision("data.rows.update", snapshot_hash=snapshot_hash)


def test_trusted_decision_ttl_is_capped_at_live_auth_freshness() -> None:
    with pytest.raises(ValueError, match="30 seconds"):
        _decision(
            "data.rows.update",
            evaluated_at=NOW,
            expires_at=NOW + timedelta(seconds=30, microseconds=1),
        )


@pytest.mark.parametrize(
    "tenant",
    [
        _tenant(is_active=False),
        _tenant(schema_name="tenant_feedface"),
    ],
)
def test_tenant_route_is_locked_first_and_rejected_before_user_lookup(
    tenant: Tenant,
) -> None:
    session, _ = _session([_Result(one=tenant)])

    with pytest.raises(ControlledCrudAuthorizationDenied, match="tenant route"):
        execute_controlled_crud(session, _command(_update_request()), now=NOW)

    assert len(session.execute.call_args_list) == 1
    statement = str(session.execute.call_args.args[0])
    assert "omnibase_meta.tenants" in statement
    assert "FOR UPDATE" in statement


@pytest.mark.parametrize(
    "user",
    [None, _user(is_active=False), _user(user_id=uuid4())],
)
def test_current_tenant_user_is_locked_second_and_must_be_exact_and_active(
    user: dict[str, object] | None,
) -> None:
    user_result = _Result() if user is None else _Result(one=user)
    session, _ = _session([_Result(one=_tenant()), user_result])

    with pytest.raises(ControlledCrudAuthorizationDenied, match="missing or inactive"):
        execute_controlled_crud(session, _command(_update_request()), now=NOW)

    assert len(session.execute.call_args_list) == 2
    statement = str(session.execute.call_args_list[1].args[0])
    assert "tenant_deadbeef.users" in statement
    assert "FOR UPDATE" in statement


def test_current_member_role_must_exactly_match_live_rbac_decision() -> None:
    request = _update_request(max_rows=1)
    request_hash = canonical_request_hash(request)
    records = _records(request_hash, "data.rows.update")
    member = _user(is_tenant_admin=False)
    authorization = records[2]
    assert isinstance(authorization, AuthorizationContext)
    authorization.role_snapshot = ["workspace_member"]
    results = [
        *_authorization_results(records, request_hash, user=member),
        _Result(rows=["(0,1)", "(0,2)"]),
    ]
    session, _ = _session(results)

    with pytest.raises(MutationBudgetExceeded, match="more rows"):
        execute_controlled_crud(
            session,
            _command(
                request,
                decision=_decision(
                    "data.rows.update",
                    roles=frozenset({"workspace_member"}),
                ),
            ),
            now=NOW,
        )

    assert len(session.execute.call_args_list) == 12

    session, _ = _session(_authorization_results(records, request_hash, user=member))
    with pytest.raises(ControlledCrudAuthorizationDenied, match="role"):
        execute_controlled_crud(session, _command(request), now=NOW)


def test_authorization_context_role_action_and_expiry_cannot_be_expanded() -> None:
    request = _update_request()
    request_hash = canonical_request_hash(request)
    records = list(_records(request_hash, "data.rows.update"))
    authorization = records[2]
    assert isinstance(authorization, AuthorizationContext)
    authorization.actions = ["data.rows.insert"]
    session, _ = _session(_authorization_results(tuple(records), request_hash))
    with pytest.raises(ControlledCrudAuthorizationDenied):
        execute_controlled_crud(session, _command(request), now=NOW)

    authorization.actions = ["data.rows.update"]
    authorization.role_snapshot = ["workspace_member"]
    session, _ = _session(_authorization_results(tuple(records), request_hash))
    with pytest.raises(ControlledCrudAuthorizationDenied):
        execute_controlled_crud(session, _command(request), now=NOW)

    authorization.role_snapshot = ["tenant_admin"]
    authorization.expires_at = NOW
    session, _ = _session(_authorization_results(tuple(records), request_hash))
    with pytest.raises(ControlledCrudAuthorizationDenied):
        execute_controlled_crud(session, _command(request), now=NOW)


def test_preflight_overflow_rolls_back_before_version_recheck_or_apply() -> None:
    request = _update_request(max_rows=1)
    request_hash = canonical_request_hash(request)
    records = _records(request_hash, "data.rows.update")
    results = [
        *_lock_results(records),
        _Result(one=NOW),
        _Result(one="new-idempotency-id"),
        _Result(one=_idempotency(request_hash)),
        _Result(one=NOW),
        _Result(rows=["(0,1)", "(0,2)"]),
    ]
    session, _ = _session(results)
    with pytest.raises(MutationBudgetExceeded, match="more rows"):
        execute_controlled_crud(session, _command(request), now=NOW)
    assert len(session.execute.call_args_list) == 12
    assert records[3].state == "running"
    # The context manager owns rollback; no completed metadata is produced.
    assert records[3].result_ref is None


def test_second_resource_version_check_fails_before_apply() -> None:
    request = _update_request()
    request_hash = canonical_request_hash(request)
    records = _records(request_hash, "data.rows.update")
    results = [
        *_lock_results(records),
        _Result(one=NOW),
        _Result(one="new-idempotency-id"),
        _Result(one=_idempotency(request_hash)),
        _Result(one=NOW),
        _Result(rows=["(0,1)"]),
        _Result(one=5),
        _Result(one=4),
    ]
    session, _ = _session(results)
    with pytest.raises(ControlledCrudConflict, match="version changed"):
        execute_controlled_crud(session, _command(request), now=NOW)
    assert len(session.execute.call_args_list) == 14


def test_stale_expected_operation_version_fails_closed() -> None:
    request = _update_request()
    request_hash = canonical_request_hash(request)
    records = _records(request_hash, "data.rows.update")
    records[3].version = 2
    session, _ = _session(_authorization_results(records, request_hash))

    with pytest.raises(ControlledCrudConflict, match="operation does not bind"):
        execute_controlled_crud(session, _command(request), now=NOW)


def test_executor_refuses_nested_or_preexisting_transaction() -> None:
    session = MagicMock(spec=Session)
    session.in_transaction.return_value = True
    with pytest.raises(ControlledCrudExecutionError, match="transaction ownership"):
        execute_controlled_crud(session, _command(_update_request()), now=NOW)
    session.execute.assert_not_called()


def test_command_timeout_relationship_is_fail_closed() -> None:
    request = _update_request()
    with pytest.raises(ValueError, match="cannot exceed"):
        ControlledCrudCommand(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            actor_user_id=ACTOR_ID,
            authorization_context_id=AUTH_ID,
            operation_id=OPERATION_ID,
            locator=_locator(),
            request=request.model_copy(update={"timeout_ms": 100}),
            decision=_decision("data.rows.update"),
            lock_timeout_ms=500,
        )


def test_missing_locked_record_or_locator_column_mismatch_fails_before_idempotency() -> None:
    request = _update_request()
    request_hash = canonical_request_hash(request)
    records = _records(request_hash, "data.rows.update")
    missing = [
        _Result(one=_tenant()),
        _Result(one=_user()),
        _Result(one=records[1]),
        _Result(),
        _Result(rows=[records[4]]),
        _Result(one=records[2]),
        _Result(one=records[3]),
    ]
    session, _ = _session(missing)
    with pytest.raises(ControlledCrudConflict, match="record is missing"):
        execute_controlled_crud(session, _command(request), now=NOW)
    assert len(session.execute.call_args_list) == 7

    records[4].physical_column_name = column_identifier(uuid4())
    session, _ = _session(_lock_results(records))
    with pytest.raises(ControlledCrudConflict, match="column binding changed"):
        execute_controlled_crud(session, _command(request), now=NOW)


def _successful_insert_results(
    request_hash: str,
) -> tuple[list[_Result], tuple[object, object, object, object, object], IdempotencyRecord]:
    records = _records(request_hash, "data.rows.insert")
    idempotency = _idempotency(request_hash)
    idempotency.operation_name = "data.rows.insert"
    idempotency.key = "idem.executor-0002"
    return (
        [
            *_lock_results(records),
            _Result(one=NOW),
            _Result(one="new-idempotency-id"),
            _Result(one=idempotency),
            _Result(one=NOW),
            _Result(one=4),
            _Result(one=4),
            _Result(rowcount=1),
            _Result(one=NOW),
        ],
        records,
        idempotency,
    )


def test_success_audit_hook_runs_after_completion_and_before_final_flush() -> None:
    request = _insert_request()
    request_hash = canonical_request_hash(request)
    results, records, idempotency = _successful_insert_results(request_hash)
    session, _ = _session(results)
    observed: dict[str, object] = {}

    def success_hook(
        hook_session: Session,
        result: object,
        operation: OperationRecord,
        locked_idempotency: IdempotencyRecord,
    ) -> None:
        observed.update(
            {
                "session": hook_session,
                "result": result,
                "operation": operation,
                "idempotency": locked_idempotency,
                "flush_count": session.flush.call_count,
                "operation_state": operation.state,
                "idempotency_state": locked_idempotency.state,
            }
        )

    result = execute_controlled_crud(
        session,
        _command(request),
        now=NOW,
        success_audit_hook=success_hook,
    )

    assert observed == {
        "session": session,
        "result": result,
        "operation": records[3],
        "idempotency": idempotency,
        "flush_count": 1,
        "operation_state": "succeeded",
        "idempotency_state": "completed",
    }
    assert session.flush.call_count == 2


def test_success_audit_hook_failure_is_sanitized_and_rolls_back_transaction() -> None:
    request = _insert_request()
    request_hash = canonical_request_hash(request)
    results, _, _ = _successful_insert_results(request_hash)
    session, _ = _session(results)

    def failing_hook(*_args: object) -> None:
        raise RuntimeError("secret tenant table and bind values")

    with pytest.raises(ControlledCrudExecutionError) as caught:
        execute_controlled_crud(
            session,
            _command(request),
            now=NOW,
            success_audit_hook=failing_hook,
        )

    assert str(caught.value) == "controlled CRUD success audit hook failed"
    assert caught.value.__cause__ is None
    assert "secret" not in str(caught.value).lower()
    assert session.flush.call_count == 1
    transaction = session.begin.return_value
    assert transaction.__exit__.call_args.args[0] is ControlledCrudSuccessAuditError


@pytest.mark.parametrize("failure_point", ["timeout", "tenant_lock", "commit"])
def test_database_errors_are_sanitized_at_public_executor_boundary(
    failure_point: str,
) -> None:
    database_error = OperationalError(
        'SELECT secret FROM "tenant_sensitive".users WHERE ctid = :ctid',
        {"password": "secret", "ctid": "(1,2)"},
        Exception("driver exposed schema, SQL, and bind values"),
    )
    request = _insert_request()
    request_hash = canonical_request_hash(request)
    results, _, _ = _successful_insert_results(request_hash)
    session, connection = _session(results)
    if failure_point == "timeout":
        connection.exec_driver_sql.side_effect = database_error
    elif failure_point == "tenant_lock":
        session.execute.side_effect = database_error
    else:
        session.begin.return_value.__exit__.side_effect = database_error

    with pytest.raises(ControlledCrudExecutionError) as caught:
        execute_controlled_crud(session, _command(request), now=NOW)

    assert str(caught.value) == "controlled CRUD database operation failed"
    assert isinstance(caught.value, ControlledCrudDatabaseFailure)
    assert caught.value.code == "CONTROLLED_CRUD_DATABASE_ERROR"
    assert caught.value.__cause__ is None
    safe_message = str(caught.value).lower()
    for forbidden in (
        "tenant_sensitive",
        "users",
        "password",
        "secret",
        "ctid",
        "select",
        "bind",
    ):
        assert forbidden not in safe_message


def test_safe_result_metadata_has_exact_logical_schema() -> None:
    result = SimpleNamespace(
        keys={
            "operation_id",
            "resource_id",
            "resource_version",
            "action",
            "affected_rows",
            "request_hash",
            "status",
        }
    )
    request = _update_request()
    request_hash = canonical_request_hash(request)
    records = _records(request_hash, "data.rows.update", operation_state="succeeded")
    metadata = {
        "operation_id": str(OPERATION_ID),
        "resource_id": str(RESOURCE_ID),
        "resource_version": 4,
        "action": "data.rows.update",
        "affected_rows": 0,
        "request_hash": request_hash,
        "status": "succeeded",
    }
    assert set(metadata) == result.keys
    assert not {
        "ctid",
        "row_tokens",
        "physical_table_name",
        "physical_column_name",
        "tenant_schema",
        "locator",
    } & set(metadata)
    records[3].result_ref = metadata
