from __future__ import annotations

import inspect
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest

from omnibase.control_plane.models import IdempotencyRecord, ResourceRecord
from omnibase.controlled_data import create_table_bootstrap as bootstrap
from omnibase.controlled_data.ddl_contracts import CreateTablePlanDefinition
from omnibase.controlled_data.models import AuthorizationContext
from omnibase.db.models import Tenant


def _definition() -> CreateTablePlanDefinition:
    return CreateTablePlanDefinition.model_validate(
        {
            "display_name": "Customer Notes",
            "columns": [
                {
                    "id": uuid4(),
                    "display_name": "Title",
                    "data_type": {"type": "string", "args": {"max_length": 200}},
                    "nullable": False,
                }
            ],
        }
    )


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object | None:
        return self.value

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> object | None:
        return self.value


class _NestedTransaction(AbstractContextManager[None]):
    def __init__(self, session: _Session) -> None:
        self.session = session
        self.added_length = len(session.added)

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc_type is not None:
            del self.session.added[self.added_length :]
            self.session.rolled_back = True
        return False


class _Session:
    def __init__(self, values: list[object]) -> None:
        self.values = values
        self.statements: list[object] = []
        self.added: list[object] = []
        self.rolled_back = False

    def begin_nested(self) -> _NestedTransaction:
        return _NestedTransaction(self)

    def execute(self, statement: object) -> _Result:
        self.statements.append(statement)
        return _Result(self.values.pop(0))

    def add(self, instance: object) -> None:
        self.added.append(instance)

    def flush(self) -> None:
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = str(uuid4())


def _tenant(tenant_id: UUID, *, active: bool = True) -> Tenant:
    return Tenant(
        id=str(tenant_id),
        name="Tenant",
        slug=f"tenant-{tenant_id.hex[:8]}",
        schema_name="tenant_1234abcd",
        is_default=False,
        is_active=active,
    )


def _workspace(tenant_id: UUID, workspace_id: UUID, *, state: str = "active") -> ResourceRecord:
    return ResourceRecord(
        id=str(workspace_id),
        tenant_id=str(tenant_id),
        kind="workspace",
        owner_type="user",
        owner_id=str(uuid4()),
        display_name="Workspace",
        state=state,
        version=1,
        policy_class="workspace_private",
    )


def _context(tenant_id: UUID, actor_id: UUID) -> bootstrap.TrustedCreateTableRequestContext:
    return bootstrap.TrustedCreateTableRequestContext(
        tenant_id=tenant_id,
        actor_user_id=actor_id,
        request_id="req-create-001",
        idempotency_key="create-table-001",
    )


def _idempotency(tenant_id: UUID, actor_id: UUID) -> IdempotencyRecord:
    return IdempotencyRecord(
        id=str(uuid4()),
        tenant_id=str(tenant_id),
        actor_scope=f"user:{actor_id}",
        operation_name=bootstrap.CREATE_TABLE_IDEMPOTENCY_OPERATION,
        key="create-table-001",
        request_hash="a" * 64,
        state="pending",
        version=1,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _install_idempotency_stubs(
    monkeypatch: pytest.MonkeyPatch,
    record: IdempotencyRecord,
    *,
    created: bool = True,
) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    def reserve(session: object, **kwargs: object) -> tuple[IdempotencyRecord, bool]:
        calls["reserve"] = kwargs
        record.request_hash = str(kwargs["request_hash"])
        return record, created

    def complete(session: object, **kwargs: object) -> IdempotencyRecord:
        calls["complete"] = kwargs
        record.state = "completed"
        record.version += 1
        record.response_ref = kwargs["response_ref"]  # type: ignore[assignment]
        record.operation_id = str(kwargs["operation_id"])
        return record

    monkeypatch.setattr(bootstrap, "reserve_idempotency", reserve)
    monkeypatch.setattr(bootstrap, "complete_idempotency", complete)
    return calls


def test_bootstrap_public_signature_has_no_tenant_or_generated_identifier_inputs() -> None:
    parameters = inspect.signature(bootstrap.bootstrap_create_table).parameters
    assert set(parameters) == {"session", "context", "workspace_id", "definition"}
    assert not {
        "tenant_id",
        "schema_name",
        "physical_table_name",
        "resource_id",
        "operation_id",
        "authorization_context_id",
    } & set(parameters)


def test_bootstrap_locks_scope_and_registers_server_owned_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, actor_id, workspace_id = uuid4(), uuid4(), uuid4()
    tenant = _tenant(tenant_id)
    workspace = _workspace(tenant_id, workspace_id)
    session = _Session(
        [
            tenant,
            {"id": str(actor_id), "is_active": True, "is_tenant_admin": True},
            workspace,
        ]
    )
    record = _idempotency(tenant_id, actor_id)
    calls = _install_idempotency_stubs(monkeypatch, record)

    result = bootstrap.bootstrap_create_table(
        session,  # type: ignore[arg-type]
        context=_context(tenant_id, actor_id),
        workspace_id=workspace_id,
        definition=_definition(),
    )

    assert not result.replayed
    assert result.registration is not None
    assert result.authorization is not None
    assert result.registration.resource.id == str(result.resource_id)
    assert result.registration.operation.id == str(result.operation_id)
    assert result.registration.plan.authorization_context_id == str(result.authorization_context_id)
    assert result.registration.table_binding.workspace_id == str(workspace_id)
    assert result.registration.table_binding.policy_class == "workspace_private"

    authorization = result.authorization
    assert authorization.source == "user_rbac"
    assert authorization.actions == ["data.schema.apply"]
    assert authorization.resource_ids == [str(result.resource_id)]
    assert authorization.role_snapshot == ["tenant_admin"]
    assert authorization.source_version == 1
    assert authorization.live_recheck_required is True
    assert len(authorization.snapshot_hash) == 64
    assert authorization.expires_at - authorization.created_at == timedelta(minutes=5)

    locked_entities = []
    for statement in session.statements:
        assert getattr(statement, "_for_update_arg", None) is not None
        entity = statement.column_descriptions[0].get("entity")  # type: ignore[attr-defined]
        if entity is Tenant:
            locked_entities.append("Tenant")
        elif entity is ResourceRecord:
            locked_entities.append("Workspace")
        else:
            locked_entities.append("User")
    assert locked_entities == ["Tenant", "User", "Workspace"]
    user_sql = str(session.statements[1])
    assert f"{tenant.schema_name}.users" in user_sql
    assert "reserve" in calls
    assert "complete" in calls
    assert calls["complete"]["operation_id"] == str(result.operation_id)


@pytest.mark.parametrize(
    "user",
    [
        None,
        {"id": "actor", "is_active": False, "is_tenant_admin": True},
        {"id": "actor", "is_active": True, "is_tenant_admin": False},
    ],
)
def test_bootstrap_requires_active_tenant_admin_and_writes_nothing(user: object) -> None:
    tenant_id, actor_id = uuid4(), uuid4()
    session = _Session([_tenant(tenant_id), user])
    with pytest.raises(bootstrap.CreateTableBootstrapDenied):
        bootstrap.bootstrap_create_table(
            session,  # type: ignore[arg-type]
            context=_context(tenant_id, actor_id),
            workspace_id=uuid4(),
            definition=_definition(),
        )
    assert session.added == []
    assert session.rolled_back


def test_bootstrap_rejects_inactive_or_cross_tenant_workspace_before_writes() -> None:
    tenant_id, actor_id, workspace_id = uuid4(), uuid4(), uuid4()
    other_tenant = uuid4()
    session = _Session(
        [
            _tenant(tenant_id),
            {"id": str(actor_id), "is_active": True, "is_tenant_admin": True},
            _workspace(other_tenant, workspace_id, state="stopped"),
        ]
    )
    with pytest.raises(bootstrap.CreateTableBootstrapConflict):
        bootstrap.bootstrap_create_table(
            session,  # type: ignore[arg-type]
            context=_context(tenant_id, actor_id),
            workspace_id=workspace_id,
            definition=_definition(),
        )
    assert session.added == []
    assert session.rolled_back


def test_bootstrap_rolls_back_authorization_when_registration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, actor_id, workspace_id = uuid4(), uuid4(), uuid4()
    session = _Session(
        [
            _tenant(tenant_id),
            {"id": str(actor_id), "is_active": True, "is_tenant_admin": True},
            _workspace(tenant_id, workspace_id),
        ]
    )
    _install_idempotency_stubs(monkeypatch, _idempotency(tenant_id, actor_id))

    def fail_registration(*args: object, **kwargs: object) -> None:
        assert any(isinstance(item, AuthorizationContext) for item in session.added)
        raise RuntimeError("registration failed")

    monkeypatch.setattr(bootstrap, "register_create_table", fail_registration)
    with pytest.raises(RuntimeError, match="registration failed"):
        bootstrap.bootstrap_create_table(
            session,  # type: ignore[arg-type]
            context=_context(tenant_id, actor_id),
            workspace_id=workspace_id,
            definition=_definition(),
        )
    assert session.added == []
    assert session.rolled_back


def test_completed_idempotency_replay_creates_no_new_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, actor_id, workspace_id = uuid4(), uuid4(), uuid4()
    resource_id, operation_id, authorization_id = uuid4(), uuid4(), uuid4()
    session = _Session(
        [
            _tenant(tenant_id),
            {"id": str(actor_id), "is_active": True, "is_tenant_admin": True},
            _workspace(tenant_id, workspace_id),
        ]
    )
    record = _idempotency(tenant_id, actor_id)
    record.state = "completed"
    record.operation_id = str(operation_id)
    record.response_ref = {
        "resource_id": str(resource_id),
        "operation_id": str(operation_id),
        "authorization_context_id": str(authorization_id),
    }
    _install_idempotency_stubs(monkeypatch, record, created=False)

    result = bootstrap.bootstrap_create_table(
        session,  # type: ignore[arg-type]
        context=_context(tenant_id, actor_id),
        workspace_id=workspace_id,
        definition=_definition(),
    )
    assert result.replayed
    assert result.registration is None
    assert result.resource_id == resource_id
    assert result.operation_id == operation_id
    assert result.authorization_context_id == authorization_id
    assert session.added == []
