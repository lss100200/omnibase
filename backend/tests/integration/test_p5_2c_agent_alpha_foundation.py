"""Guarded PostgreSQL acceptance tests for the P5.2C engineering Agent Alpha.

The Gate uses the P5.2C disposable sentinel database and exercises the real
engineering composition seam: migration head 0013, a seeded live P34.4
run/lease/node chain, a sealed tool-free low-risk AgentVersion with an
installed binding, the deterministic fake Model Gateway injected at the
composition seam, and one real HTTP/SSE Alpha invocation.  Durable
Task/Run/Step/Attempt/Lease/Budget/Effect rows, model identity, usage,
cancellation scope, idempotency, unknown no-replay, tool-bearing rejection
and secret containment are all verified against the database.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from tests.integration.test_p5_1b_agent_registry_foundation import (
    ACTOR_ID,
    _binding_dto,
    _binding_mapping,
    _install,
    _seed_definition_version,
    _session,
    _template,
    _upgrade_head,
    _version_dto,
    _version_mapping,
    _workspace,
)

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P5.2C integration tests require OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration

_MODEL_ID = "deterministic-alpha-1"


@pytest.fixture(autouse=True)
def _restore_engineering_gateway_seam() -> Iterator[None]:
    """Keep direct test seam overrides from leaking into later cases."""

    from omnibase.agent_alpha import engineering as engineering_module

    original = engineering_module.configured_model_gateway
    try:
        yield
    finally:
        engineering_module.configured_model_gateway = original


class DeterministicFakeProvider:
    """Tool-free deterministic provider implementing the Model Gateway interface."""

    provider_id = "deterministic-fake"

    def __init__(
        self,
        *,
        fail: bool = False,
        controlled_failure: bool = False,
        slow: bool = False,
        missing_identity: bool = False,
        call_count: list[int] | None = None,
        started: Event | None = None,
        release: Event | None = None,
    ) -> None:
        self._fail = fail
        self._controlled_failure = controlled_failure
        self._slow = slow
        self._missing_identity = missing_identity
        self.call_count = call_count
        self.started = started
        self.release = release

    def _record_call(self) -> None:
        if self.call_count is not None:
            self.call_count[0] += 1

    def complete(self, request: object) -> object:
        raise AssertionError(request)

    def stream(self, request: object) -> Iterator[object]:
        from omnibase.model_gateway import ModelStreamChunk, ModelUsage
        from omnibase.model_gateway.providers import ModelProviderError

        del request
        self._record_call()
        if self.started is not None:
            self.started.set()
        if self.release is not None and not self.release.wait(timeout=10):
            raise AssertionError("deterministic provider release timed out")
        if self._controlled_failure:
            from omnibase.agent_alpha.service import AgentAlphaError

            raise AgentAlphaError("agent_alpha_deterministic_failure")
        if self._fail:
            # The provider outcome is ambiguous, never a deterministic result:
            # the service must record unknown + reconciliation, not a failure
            # it can retry or a success it can claim.
            raise ModelProviderError("deterministic provider failure")
        if self._slow:
            time.sleep(1.0)
        yield ModelStreamChunk(
            provider_id=self.provider_id,
            requested_model_id=_MODEL_ID,
            actual_model_id=None if self._missing_identity else _MODEL_ID,
            content="deterministic ",
        )
        yield ModelStreamChunk(
            provider_id=self.provider_id,
            requested_model_id=_MODEL_ID,
            actual_model_id=None if self._missing_identity else _MODEL_ID,
            content="answer",
            finish_reason="stop",
            usage=ModelUsage(input_tokens=11, output_tokens=4, total_tokens=15),
        )


@pytest.fixture(scope="module", autouse=True)
def p52c_schema(db_engine) -> None:  # type: ignore[no-untyped-def]
    del db_engine
    _upgrade_head()


def _seed_alpha_target(
    db_engine, run_owned_resources, label: str, *, tool_free: bool = True
) -> dict[str, object]:
    """Tenant + workspace + sealed version + live binding + P34.4 run chain."""
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine, run_owned_resources, label
    )
    # Build a tool-free version when required (the shared helper defaults to tools).
    if tool_free:
        version_mapping = _version_mapping(tenant_id, version.agent_definition_id)
        version_mapping["allowed_tool_ids"] = []
        version_mapping["manifest_digest"] = "0" * 64
        version_mapping["manifest_digest"] = _canonical_hash(
            {k: v for k, v in version_mapping.items() if k != "manifest_digest"}
        )
        version = _version_dto(version_mapping)
        with _session(db_engine, tenant_id) as session:
            from omnibase.agent_registry.service import RegistryPersistenceService

            RegistryPersistenceService(session).seal_version(
                tenant_id=tenant_id,
                actor_user_id=ACTOR_ID,
                request_id=str(uuid.uuid4()),
                version=version,
                idempotency_key=uuid.uuid4().hex,
            )
            session.commit()
    binding = _binding_dto(
        _binding_mapping(tenant_id, workspace_id, version.agent_definition_id, version)
    )
    with _session(db_engine, tenant_id) as session:
        _install(session, tenant_id=tenant_id, binding=binding, key=uuid.uuid4().hex)
        session.commit()
    with _session(db_engine, tenant_id) as session:
        session.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_memberships "
                "(tenant_id, workspace_id, user_id, role, state, version, "
                "created_by_user_id) VALUES "
                "(:tenant, :workspace, :actor, 'owner', 'active', 1, :actor) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "tenant": tenant_id,
                "workspace": workspace_id,
                "actor": ACTOR_ID,
            },
        )
        session.commit()
    with _session(db_engine, tenant_id) as session:
        from omnibase.onboarding import ensure_local_model_runtime_anchor
        from omnibase.workspaces.models import Workspace

        workspace = session.execute(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.tenant_id == tenant_id,
            )
        ).scalar_one()
        ensure_local_model_runtime_anchor(
            session,
            tenant_id=tenant_id,
            actor_user_id=ACTOR_ID,
            workspace=workspace,
        )
        session.commit()
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "agent_version_id": version.agent_version_id,
    }


def _canonical_hash(payload: dict[str, object]) -> str:
    import hashlib

    value = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _client(db_engine, tenant_id: str, user_id: str = ACTOR_ID) -> TestClient:
    from omnibase.agent_alpha.router import router
    from omnibase.tenants.context import reset_schema, set_current_schema

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides.clear()
    schema_name = _tenant_schema_name(db_engine, tenant_id)

    async def _override() -> AsyncIterator[SimpleNamespace]:
        token = set_current_schema(schema_name)
        try:
            yield SimpleNamespace(
                tenant_id=tenant_id,
                schema_name=schema_name,
                user_id=user_id,
            )
        finally:
            reset_schema(token)

    app.dependency_overrides[_tenant_dependency()] = _override
    return TestClient(app)


def _tenant_dependency():
    from omnibase.tenants.dependencies import get_current_tenant

    return get_current_tenant


def _tenant_schema_name(db_engine, tenant_id: str) -> str:
    with db_engine.connect() as connection:
        return str(
            connection.execute(
                text("SELECT schema_name FROM omnibase_meta.tenants WHERE id = :tenant"),
                {"tenant": tenant_id},
            ).scalar_one()
        )


def _count(db_engine, query: str, **params: object) -> int:
    with db_engine.connect() as connection:
        return int(connection.execute(text(query), params).scalar_one())


def _assert_live_runtime_binding(db_engine, *, tenant_id: str, task_id: str) -> None:
    """Prove the P34 WorkspaceRun and P5 AgentRun share one live identity."""
    with db_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT ar.state AS agent_state, ar.runtime_instance_id AS agent_runtime, "
                    "ar.workload_identity_digest AS agent_workload, ar.node_id AS agent_node, "
                    "ar.node_fencing_token AS agent_node_fencing, "
                    "ar.run_lease_id AS agent_lease, ar.run_fencing_token AS agent_fencing, "
                    "wr.observed_state AS workspace_state, "
                    "wr.runtime_instance_id AS workspace_runtime, "
                    "wr.workload_identity_digest AS workspace_workload, "
                    "rl.id AS lease_id, rl.node_id AS lease_node, "
                    "rl.node_fencing_token AS lease_node_fencing, "
                    "rl.fencing_token AS lease_fencing, rl.state AS lease_state "
                    "FROM omnibase_meta.agent_runs ar "
                    "JOIN omnibase_meta.workspace_runs wr "
                    "ON wr.id = ar.workspace_run_id AND wr.tenant_id = ar.tenant_id "
                    "JOIN omnibase_meta.run_leases rl "
                    "ON rl.run_id = wr.id AND rl.tenant_id = wr.tenant_id "
                    "WHERE ar.task_id = :task AND ar.tenant_id = :tenant"
                ),
                {"task": task_id, "tenant": tenant_id},
            )
            .mappings()
            .one()
        )
    assert row["agent_state"] == "running"
    assert row["workspace_state"] == "running"
    assert row["lease_state"] == "active"
    assert row["agent_runtime"] is not None
    assert row["agent_runtime"] == row["workspace_runtime"]
    assert row["agent_workload"] is not None
    assert row["agent_workload"] == row["workspace_workload"]
    assert row["agent_lease"] == row["lease_id"]
    assert row["agent_node"] == row["lease_node"]
    assert row["agent_node_fencing"] == row["lease_node_fencing"]
    assert row["agent_fencing"] == row["lease_fencing"]


def _assert_terminal_runtime_state(
    db_engine,
    *,
    tenant_id: str,
    task_id: str,
    agent_state: str,
    workspace_state: str,
    lease_state: str,
) -> None:
    """Prove both ledgers clear live bindings and leave no active P34 state."""
    with db_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT ar.state AS agent_state, ar.run_lease_id, ar.run_fencing_token, "
                    "ar.node_id, ar.node_fencing_token, ar.runtime_instance_id AS agent_runtime, "
                    "ar.workload_identity_digest AS agent_workload, "
                    "wr.observed_state AS workspace_state, "
                    "wr.runtime_instance_id AS workspace_runtime, "
                    "wr.workload_identity_digest AS workspace_workload, rl.state AS lease_state "
                    "FROM omnibase_meta.agent_runs ar "
                    "JOIN omnibase_meta.workspace_runs wr "
                    "ON wr.id = ar.workspace_run_id AND wr.tenant_id = ar.tenant_id "
                    "JOIN omnibase_meta.run_leases rl "
                    "ON rl.run_id = wr.id AND rl.tenant_id = wr.tenant_id "
                    "WHERE ar.task_id = :task AND ar.tenant_id = :tenant"
                ),
                {"task": task_id, "tenant": tenant_id},
            )
            .mappings()
            .one()
        )
        active_workspace_runs = connection.execute(
            text(
                "SELECT count(*) FROM omnibase_meta.workspace_runs "
                "WHERE tenant_id = :tenant AND observed_state IN "
                "('leased','starting','running','pausing','stopping')"
            ),
            {"tenant": tenant_id},
        ).scalar_one()
        active_run_leases = connection.execute(
            text(
                "SELECT count(*) FROM omnibase_meta.run_leases "
                "WHERE tenant_id = :tenant AND state = 'active'"
            ),
            {"tenant": tenant_id},
        ).scalar_one()
    assert row["agent_state"] == agent_state
    assert row["workspace_state"] == workspace_state
    assert row["lease_state"] == lease_state
    for field in (
        "run_lease_id",
        "run_fencing_token",
        "node_id",
        "node_fencing_token",
        "agent_runtime",
        "agent_workload",
        "workspace_runtime",
        "workspace_workload",
    ):
        assert row[field] is None
    assert int(active_workspace_runs) == 0
    assert int(active_run_leases) == 0


def _seed_ready_derived_chunk(
    db_engine,
    *,
    tenant_id: str,
    workspace_id: str,
    content: str,
) -> tuple[str, str]:
    from omnibase.control_plane.service import create_operation, register_resource
    from omnibase.workspace_data.models import WorkspaceDerivedIndex

    with _session(db_engine, tenant_id) as session:
        source = register_resource(
            session,
            tenant_id=tenant_id,
            kind="artifact",
            owner_type="workspace",
            owner_id=workspace_id,
            parent_id=workspace_id,
            display_name="alpha-source",
            policy_class="workspace_private",
            created_by_actor_id=ACTOR_ID,
        )
        derived = register_resource(
            session,
            tenant_id=tenant_id,
            kind="derived_index",
            owner_type="workspace",
            owner_id=workspace_id,
            parent_id=workspace_id,
            display_name="alpha-derived",
            policy_class="workspace_derived",
            created_by_actor_id=ACTOR_ID,
        )
        operation = create_operation(
            session,
            tenant_id=tenant_id,
            kind="rag.derived.create",
            risk_level="R1",
            actor_type="user",
            actor_id=ACTOR_ID,
            workspace_id=workspace_id,
            resource_id=source.id,
            resource_version=source.version,
            request_hash=_canonical_hash({"derived": derived.id}),
        )
        generation = str(uuid.uuid4())
        session.add(
            WorkspaceDerivedIndex(
                id=derived.id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                source_resource_id=source.id,
                source_version=source.version,
                operation_id=operation.id,
                generation=generation,
                index_profile_digest="d" * 64,
                manifest_digest="e" * 64,
                chunk_count=1,
                state="ready",
                version=1,
                created_by_actor_id=ACTOR_ID,
            )
        )
        session.flush()
        chunk_id = str(uuid.uuid4())
        session.execute(
            text(
                "INSERT INTO workspace_derived_chunks_v2 "
                "(id, workspace_id, derived_index_id, generation, source_resource_id, "
                "chunk_index, content, content_digest, tsv, metadata) VALUES "
                "(:id, :workspace, :derived, :generation, :source, 0, :content, :digest, "
                "to_tsvector('pg_catalog.simple', :content), '{\"page\": 1}'::jsonb)"
            ),
            {
                "id": chunk_id,
                "workspace": workspace_id,
                "derived": derived.id,
                "generation": generation,
                "source": source.id,
                "content": content,
                "digest": _canonical_hash({"content": content}),
            },
        )
        session.commit()
        return chunk_id, source.id


def _invoke_events(
    client: TestClient,
    workspace_id: str,
    agent_version_id: str,
    message: str,
    *,
    idempotency_key: str | None = None,
) -> tuple[int, list[tuple[str, dict[str, object]]], str]:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-alpha/invoke",
        headers={"Idempotency-Key": idempotency_key} if idempotency_key else {},
        json={"agent_version_id": agent_version_id, "message": message, "top_k": 3},
    )
    if response.status_code != 200:
        return response.status_code, [], response.text
    events: list[tuple[str, dict[str, object]]] = []
    for block in response.text.split("\n\n"):
        event = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            events.append((event, json.loads("\n".join(data_lines))))
    return 200, events, response.text


def test_local_runtime_fallback_identity_is_process_scoped() -> None:
    from omnibase.onboarding import _resolve_local_runtime_deployment_id

    first = _resolve_local_runtime_deployment_id(None)
    second = _resolve_local_runtime_deployment_id("   ")
    assert first.startswith("process-")
    assert second.startswith("process-")
    assert first != second
    assert _resolve_local_runtime_deployment_id(" deployment-a ") == "deployment-a"


def test_member_can_renew_server_owned_runtime_attestation(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    from omnibase.onboarding import ensure_local_model_runtime_anchor
    from omnibase.workspaces.models import Workspace, WorkspaceNode

    target = _seed_alpha_target(db_engine, run_owned_resources, "alpha-member-renew")
    member_id = str(uuid.uuid4())
    with _session(db_engine, str(target["tenant_id"])) as session:
        session.execute(
            text(
                "INSERT INTO users (id, email, password_hash, is_tenant_admin, is_active) "
                "VALUES (:id, :email, 'not-used', false, true)"
            ),
            {"id": member_id, "email": f"{member_id}@example.invalid"},
        )
        session.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_memberships "
                "(tenant_id, workspace_id, user_id, role, state, version, "
                "created_by_user_id) VALUES "
                "(:tenant, :workspace, :user, 'member', 'active', 1, :owner)"
            ),
            {
                "tenant": target["tenant_id"],
                "workspace": target["workspace_id"],
                "user": member_id,
                "owner": ACTOR_ID,
            },
        )
        node = session.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.tenant_id == target["tenant_id"],
                WorkspaceNode.workspace_id == target["workspace_id"],
            )
        ).scalar_one()
        node_id = str(node.id)
        session.execute(
            text(
                "UPDATE omnibase_meta.node_attestations "
                "SET expires_at = clock_timestamp() - interval '1 second' "
                "WHERE tenant_id = :tenant AND node_id = :node"
            ),
            {"tenant": target["tenant_id"], "node": node_id},
        )
        session.commit()

    with _session(db_engine, str(target["tenant_id"])) as session:
        workspace = session.execute(
            select(Workspace).where(
                Workspace.id == target["workspace_id"],
                Workspace.tenant_id == target["tenant_id"],
            )
        ).scalar_one()
        renewed = ensure_local_model_runtime_anchor(
            session,
            tenant_id=str(target["tenant_id"]),
            actor_user_id=member_id,
            workspace=workspace,
        )
        session.commit()
        assert str(renewed.id) == node_id

    assert (
        _count(
            db_engine,
            "SELECT count(*) FROM omnibase_meta.node_attestations "
            "WHERE tenant_id = :tenant AND node_id = :node "
            "AND state = 'verified' AND expires_at > clock_timestamp()",
            tenant=target["tenant_id"],
            node=node_id,
        )
        == 1
    )


def test_revoked_or_rejected_runtime_node_cannot_be_revived(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    from omnibase.onboarding import ensure_local_model_runtime_anchor
    from omnibase.workspaces.models import Workspace, WorkspaceNode
    from omnibase.workspaces.overlay import revoke_node
    from omnibase.workspaces.service import WorkspaceNotFound

    revoked_target = _seed_alpha_target(db_engine, run_owned_resources, "alpha-node-revoked")
    with _session(db_engine, str(revoked_target["tenant_id"])) as session:
        workspace = session.execute(
            select(Workspace).where(
                Workspace.id == revoked_target["workspace_id"],
                Workspace.tenant_id == revoked_target["tenant_id"],
            )
        ).scalar_one()
        node = session.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.tenant_id == revoked_target["tenant_id"],
                WorkspaceNode.workspace_id == revoked_target["workspace_id"],
            )
        ).scalar_one()
        revoke_node(
            session,
            tenant_id=str(revoked_target["tenant_id"]),
            workspace_id=str(revoked_target["workspace_id"]),
            node_id=str(node.id),
            actor_user_id=ACTOR_ID,
        )
        session.commit()
    with _session(db_engine, str(revoked_target["tenant_id"])) as session:
        workspace = session.execute(
            select(Workspace).where(
                Workspace.id == revoked_target["workspace_id"],
                Workspace.tenant_id == revoked_target["tenant_id"],
            )
        ).scalar_one()
        with pytest.raises(WorkspaceNotFound, match="revoked"):
            ensure_local_model_runtime_anchor(
                session,
                tenant_id=str(revoked_target["tenant_id"]),
                actor_user_id=ACTOR_ID,
                workspace=workspace,
            )
        session.rollback()

    rejected_target = _seed_alpha_target(db_engine, run_owned_resources, "alpha-node-rejected")
    with _session(db_engine, str(rejected_target["tenant_id"])) as session:
        workspace = session.execute(
            select(Workspace).where(
                Workspace.id == rejected_target["workspace_id"],
                Workspace.tenant_id == rejected_target["tenant_id"],
            )
        ).scalar_one()
        node = session.execute(
            select(WorkspaceNode).where(
                WorkspaceNode.tenant_id == rejected_target["tenant_id"],
                WorkspaceNode.workspace_id == rejected_target["workspace_id"],
            )
        ).scalar_one()
        node.attestation_state = "rejected"
        session.execute(
            text(
                "UPDATE omnibase_meta.node_attestations SET state = 'rejected' "
                "WHERE tenant_id = :tenant AND node_id = :node"
            ),
            {"tenant": rejected_target["tenant_id"], "node": node.id},
        )
        session.commit()
    with _session(db_engine, str(rejected_target["tenant_id"])) as session:
        workspace = session.execute(
            select(Workspace).where(
                Workspace.id == rejected_target["workspace_id"],
                Workspace.tenant_id == rejected_target["tenant_id"],
            )
        ).scalar_one()
        with pytest.raises(WorkspaceNotFound, match="not renewable"):
            ensure_local_model_runtime_anchor(
                session,
                tenant_id=str(rejected_target["tenant_id"]),
                actor_user_id=ACTOR_ID,
                workspace=workspace,
            )
        session.rollback()


def test_alpha_success_persists_durable_lifecycle(
    db_engine, run_owned_resources, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    from omnibase.agent_alpha import engineering as engineering_module
    from omnibase.model_gateway import ModelGateway

    target = _seed_alpha_target(db_engine, run_owned_resources, "alpha-success")
    os.environ["AGENT_LITE_ENGINEERING_ENABLED"] = "true"
    os.environ["AGENT_ALPHA_ENGINEERING_ENABLED"] = "true"
    os.environ["ENV"] = "development"
    os.environ.pop("AGENT_RUNTIME_ENABLED", None)
    os.environ.pop("AGENT_PLANNER_ENABLED", None)
    os.environ.pop("MULTI_AGENT_ENABLED", None)
    counts: list[int] = [0]
    rag_calls: list[int] = [0]
    from omnibase.agent_alpha.adapters import RagKnowledgeRetriever

    real_retrieve = RagKnowledgeRetriever.retrieve

    def counted_retrieve(self, **kwargs):  # type: ignore[no-untyped-def]
        rag_calls[0] += 1
        return real_retrieve(self, **kwargs)

    monkeypatch.setattr(RagKnowledgeRetriever, "retrieve", counted_retrieve)
    engineering_module.configured_model_gateway = lambda: ModelGateway(  # type: ignore[assignment]
        provider=DeterministicFakeProvider(call_count=counts),
        model_id=_MODEL_ID,
    )
    try:
        client = _client(db_engine, target["tenant_id"])
        status, events, _ = _invoke_events(
            client,
            str(target["workspace_id"]),
            str(target["agent_version_id"]),
            "What does the workspace know?",
            idempotency_key="alpha-success-key",
        )
        assert status == 200, events
        kinds = [kind for kind, _ in events]
        assert kinds == ["meta", "citations", "chunk", "chunk", "usage", "done"]
        done = events[-1][1]
        assert done["actual_model_id"] == _MODEL_ID
        assert done["usage"]["total_tokens"] == 15
        assert counts[0] == 1

        task_id = str(events[0][1]["task_id"])
        assert task_id
        with db_engine.connect() as connection:
            task_state = connection.execute(
                text(
                    "SELECT state FROM omnibase_meta.agent_tasks "
                    "WHERE id = :task AND tenant_id = :tenant"
                ),
                {"task": task_id, "tenant": target["tenant_id"]},
            ).scalar_one()
            run_count = connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.agent_runs "
                    "WHERE task_id = :task AND tenant_id = :tenant"
                ),
                {"task": task_id, "tenant": target["tenant_id"]},
            ).scalar_one()
            attempt_state = connection.execute(
                text(
                    "SELECT state FROM omnibase_meta.agent_attempts "
                    "WHERE task_id = :task AND tenant_id = :tenant"
                ),
                {"task": task_id, "tenant": target["tenant_id"]},
            ).scalar_one()
            effect_state = connection.execute(
                text(
                    "SELECT state FROM omnibase_meta.agent_task_effects "
                    "WHERE task_id = :task AND tenant_id = :tenant"
                ),
                {"task": task_id, "tenant": target["tenant_id"]},
            ).scalar_one()
            lease_count = connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.agent_task_leases "
                    "WHERE task_id = :task AND tenant_id = :tenant"
                ),
                {"task": task_id, "tenant": target["tenant_id"]},
            ).scalar_one()
            budget_count = connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.agent_task_budget_ledgers "
                    "WHERE task_id = :task AND tenant_id = :tenant"
                ),
                {"task": task_id, "tenant": target["tenant_id"]},
            ).scalar_one()
            reconciliation_count = connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.agent_reconciliation_cases "
                    "WHERE task_id = :task AND tenant_id = :tenant"
                ),
                {"task": task_id, "tenant": target["tenant_id"]},
            ).scalar_one()
        assert task_state == "succeeded"
        assert int(run_count) == 1
        assert attempt_state == "committed"
        assert effect_state == "committed"
        assert int(lease_count) == 1
        # create_task seeds one budget ledger row per bounded dimension.
        assert int(budget_count) == 12
        assert int(reconciliation_count) == 0
        _assert_terminal_runtime_state(
            db_engine,
            tenant_id=str(target["tenant_id"]),
            task_id=task_id,
            agent_state="succeeded",
            workspace_state="succeeded",
            lease_state="completed",
        )

        # idempotency exact replay: same key returns the same durable task
        status2, events2, _ = _invoke_events(
            client,
            str(target["workspace_id"]),
            str(target["agent_version_id"]),
            "What does the workspace know?",
            idempotency_key="alpha-success-key",
        )
        assert status2 == 200
        assert str(events2[0][1]["task_id"]) == task_id
        assert counts[0] == 1  # provider never called again
        assert rag_calls[0] == 1  # exact replay never re-runs mutable retrieval

        drift_status, _, drift_body = _invoke_events(
            client,
            str(target["workspace_id"]),
            str(target["agent_version_id"]),
            "A different caller message",
            idempotency_key="alpha-success-key",
        )
        assert drift_status == 409
        assert "task_replay_input_mismatch" in drift_body
        assert counts[0] == 1
        assert rag_calls[0] == 1
    finally:
        engineering_module.configured_model_gateway = None  # type: ignore[assignment]
        os.environ.pop("AGENT_LITE_ENGINEERING_ENABLED", None)
        os.environ.pop("AGENT_ALPHA_ENGINEERING_ENABLED", None)
        os.environ.pop("ENV", None)


def test_personal_runtime_canary_assembles_from_live_owner_and_persists_run(
    db_engine,
    run_owned_resources,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    from omnibase.agent_alpha import personal as personal_module
    from omnibase.agent_alpha.personal import PersonalCanaryAgentAlpha
    from omnibase.agent_alpha.service import AgentAlphaUnavailable
    from omnibase.core.config import get_settings
    from omnibase.model_gateway import ModelGateway
    from omnibase.production.personal_runtime_activation import (
        PersonalRuntimeCanaryConfig,
        activate_personal_runtime_canary,
        kill_personal_runtime_canary,
    )
    from omnibase.tenants.context import reset_schema, set_current_schema

    target = _seed_alpha_target(db_engine, run_owned_resources, "personal-canary")
    # Keep the generic Workspace allowance deliberately wider than the personal
    # contract. The second fresh invocation must be rejected by the personal
    # single-slot admission seam before any Task row, not by max_active_runs.
    with _session(db_engine, str(target["tenant_id"])) as session:
        session.execute(
            text(
                "UPDATE omnibase_meta.workspaces "
                "SET quota = CAST(:quota AS jsonb) "
                "WHERE tenant_id = :tenant AND id = :workspace"
            ),
            {
                "quota": json.dumps({"max_active_runs": 8}),
                "tenant": target["tenant_id"],
                "workspace": target["workspace_id"],
            },
        )
        session.commit()
    repo_root = Path(__file__).resolve().parents[3]
    config_mapping = {
        "agent_planner_enabled": False,
        "agent_version_id": str(target["agent_version_id"]),
        "canary_id": str(uuid.uuid4()),
        "enterprise_approved_digest_present": False,
        "environment": "production",
        "external_side_effects": False,
        "invocation_mode": "no_tool",
        "max_canary_seconds": 900,
        "max_concurrent_invocations": 1,
        "max_top_k": 5,
        "migration_0013_created": True,
        "migration_head": "0013",
        "multi_agent_enabled": False,
        "network": {"default_deny": True, "destinations": []},
        "owner_readiness": {
            "path": "deployment/production/personal-single-owner.example.json",
            "sha256": "d71516d6a4c9ebd2e335c5e06e7507ce300ddc138e581b6dd34f9992933185de",
        },
        "owner_user_id": ACTOR_ID,
        "profile": "personal_single_owner",
        "schema_version": 1,
        "tenant_id": str(target["tenant_id"]),
        "workspace_id": str(target["workspace_id"]),
    }
    config = PersonalRuntimeCanaryConfig.from_mapping(config_mapping)
    config_path = (tmp_path / "personal-canary.json").resolve()
    config_path.write_text(
        json.dumps(config_mapping, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    state_dir = (tmp_path / "personal-state").resolve()
    activate_personal_runtime_canary(
        config,
        state_dir=state_dir,
        confirmed_plan_sha256=config.activation_plan().canonical_digest(),
    )

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("AGENT_PLANNER_ENABLED", "false")
    monkeypatch.setenv("MULTI_AGENT_ENABLED", "false")
    monkeypatch.setenv("AGENT_LITE_ENGINEERING_ENABLED", "false")
    monkeypatch.setenv("PERSONAL_RUNTIME_PROFILE", "personal_single_owner")
    monkeypatch.setenv("PERSONAL_RUNTIME_CANARY_CONFIG", str(config_path))
    monkeypatch.setenv("PERSONAL_RUNTIME_STATE_DIR", str(state_dir))
    monkeypatch.setenv("PERSONAL_RUNTIME_READINESS_ROOT", str(repo_root))
    get_settings.cache_clear()
    calls = [0]
    gateway = ModelGateway(
        provider=DeterministicFakeProvider(call_count=calls),
        model_id=_MODEL_ID,
    )
    monkeypatch.setattr(
        personal_module,
        "configured_model_gateway",
        lambda: gateway,
    )
    tenant_context_token = None
    try:
        client = _client(db_engine, str(target["tenant_id"]))
        posture = client.get(f"/api/v1/workspaces/{target['workspace_id']}/agent-alpha/status")
        assert posture.status_code == 200
        assert posture.json()["runtime_profile"] == "personal_single_owner"
        assert posture.json()["personal_runtime_active"] is True
        assert posture.json()["production_activation_allowed"] is True
        tenant_context_token = set_current_schema(
            _tenant_schema_name(db_engine, target["tenant_id"])
        )

        alpha = personal_module.build_personal_agent_alpha(
            tenant_id=str(target["tenant_id"]),
            workspace_id=str(target["workspace_id"]),
            actor_user_id=ACTOR_ID,
            profile="personal_single_owner",
            config_path=str(config_path),
            state_dir=str(state_dir),
            readiness_root=str(repo_root),
            gate_values=os.environ,
            settings=get_settings(),
            session_factory=sessionmaker(bind=db_engine, expire_on_commit=False),
            gateway=gateway,
        )
        assert isinstance(alpha, PersonalCanaryAgentAlpha)
        first_events = alpha.invoke(
            tenant_id=str(target["tenant_id"]),
            tenant_schema=_tenant_schema_name(db_engine, target["tenant_id"]),
            workspace_id=str(target["workspace_id"]),
            actor_user_id=ACTOR_ID,
            agent_version_id=str(target["agent_version_id"]),
            message="Hold the personal invocation slot",
            top_k=1,
            idempotency_key="personal-slot-first",
            retry_of=None,
        )
        with _session(db_engine, str(target["tenant_id"])) as session:
            task_count_before = int(
                session.execute(text("SELECT count(*) FROM agent_tasks")).scalar_one()
            )
        with pytest.raises(AgentAlphaUnavailable, match="invocation_slot_occupied"):
            alpha.invoke(
                tenant_id=str(target["tenant_id"]),
                tenant_schema=_tenant_schema_name(db_engine, target["tenant_id"]),
                workspace_id=str(target["workspace_id"]),
                actor_user_id=ACTOR_ID,
                agent_version_id=str(target["agent_version_id"]),
                message="A concurrent second invocation",
                top_k=1,
                idempotency_key="personal-slot-second",
                retry_of=None,
            )
        with _session(db_engine, str(target["tenant_id"])) as session:
            task_count_after = int(
                session.execute(text("SELECT count(*) FROM agent_tasks")).scalar_one()
            )
        assert task_count_after == task_count_before
        assert calls == [0]
        assert [event.kind for event in first_events][-1] == "done"
        assert calls == [1]

        status, events, _ = _invoke_events(
            client,
            str(target["workspace_id"]),
            str(target["agent_version_id"]),
            "Run the bounded personal canary",
            idempotency_key="personal-canary-live-owner",
        )
        assert status == 200, events
        assert [kind for kind, _ in events][-1] == "done"
        assert calls == [2]
        task_id = str(events[0][1]["task_id"])
        _assert_terminal_runtime_state(
            db_engine,
            tenant_id=str(target["tenant_id"]),
            task_id=task_id,
            agent_state="succeeded",
            workspace_state="succeeded",
            lease_state="completed",
        )

        killed_events = alpha.invoke(
            tenant_id=str(target["tenant_id"]),
            tenant_schema=_tenant_schema_name(db_engine, target["tenant_id"]),
            workspace_id=str(target["workspace_id"]),
            actor_user_id=ACTOR_ID,
            agent_version_id=str(target["agent_version_id"]),
            message="Reserve, then trip the independent kill marker",
            top_k=1,
            idempotency_key="personal-kill-after-reservation",
            retry_of=None,
        )
        kill_personal_runtime_canary(
            state_dir=state_dir,
            canary_id=config.canary_id,
            reason_code="integration_emergency_stop",
        )
        killed_result = list(killed_events)
        assert [event.kind for event in killed_result] == ["error"]
        assert killed_result[0].payload == {"code": "personal_runtime_control_state_unavailable"}
        assert calls == [2]
        with _session(db_engine, str(target["tenant_id"])) as session:
            tasks_before_rejected_admission = int(
                session.execute(text("SELECT count(*) FROM agent_tasks")).scalar_one()
            )
        with pytest.raises(AgentAlphaUnavailable, match="control_state_unavailable"):
            alpha.invoke(
                tenant_id=str(target["tenant_id"]),
                tenant_schema=_tenant_schema_name(db_engine, target["tenant_id"]),
                workspace_id=str(target["workspace_id"]),
                actor_user_id=ACTOR_ID,
                agent_version_id=str(target["agent_version_id"]),
                message="Must not reserve after kill",
                top_k=1,
                idempotency_key="personal-kill-rejected",
                retry_of=None,
            )
        with _session(db_engine, str(target["tenant_id"])) as session:
            assert (
                int(session.execute(text("SELECT count(*) FROM agent_tasks")).scalar_one())
                == tasks_before_rejected_admission
            )
    finally:
        if tenant_context_token is not None:
            reset_schema(tenant_context_token)
        get_settings.cache_clear()


def test_workspace_rag_never_returns_another_workspace_chunk(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    from omnibase.agent_alpha.adapters import RagKnowledgeRetriever

    target = _seed_alpha_target(db_engine, run_owned_resources, "alpha-rag-scope")
    tenant_id = str(target["tenant_id"])
    workspace_a = str(target["workspace_id"])
    with db_engine.begin() as connection:
        template_id = _template(connection, tenant_id)
        workspace_b = _workspace(connection, tenant_id, template_id, "alpha-rag-other")

    chunk_a, source_a = _seed_ready_derived_chunk(
        db_engine,
        tenant_id=tenant_id,
        workspace_id=workspace_a,
        content="sharedterm workspace alpha private fact",
    )
    chunk_b, source_b = _seed_ready_derived_chunk(
        db_engine,
        tenant_id=tenant_id,
        workspace_id=workspace_b,
        content="sharedterm workspace bravo private fact",
    )

    retriever = RagKnowledgeRetriever(sessionmaker(bind=db_engine, expire_on_commit=False))
    results = retriever.retrieve(
        tenant_id=tenant_id,
        tenant_schema=_tenant_schema_name(db_engine, tenant_id),
        workspace_id=workspace_a,
        query="sharedterm",
        top_k=8,
    )
    assert [item.chunk_id for item in results] == [chunk_a]
    assert [item.document_id for item in results] == [source_a]
    assert chunk_b not in {item.chunk_id for item in results}
    assert source_b not in {item.document_id for item in results}


def test_alpha_cancellation_is_scope_bound_and_durable(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    # Cancellation needs real HTTP concurrency: the TestClient ASGI transport
    # serializes requests, so an in-flight SSE stream would complete (and
    # unregister) before a cancel request could be served.  Run uvicorn in a
    # thread exactly like the P34.5 gateway integration tests.
    import threading

    import httpx
    import uvicorn

    from omnibase.agent_alpha import engineering as engineering_module
    from omnibase.agent_alpha.router import router
    from omnibase.model_gateway import ModelGateway
    from omnibase.tenants.context import reset_schema, set_current_schema
    from omnibase.tenants.dependencies import get_current_tenant

    target = _seed_alpha_target(db_engine, run_owned_resources, "alpha-cancel")
    os.environ["AGENT_LITE_ENGINEERING_ENABLED"] = "true"
    os.environ["AGENT_ALPHA_ENGINEERING_ENABLED"] = "true"
    os.environ["ENV"] = "development"
    os.environ.pop("AGENT_RUNTIME_ENABLED", None)
    os.environ.pop("AGENT_PLANNER_ENABLED", None)
    os.environ.pop("MULTI_AGENT_ENABLED", None)
    provider_started = Event()
    provider_release = Event()
    engineering_module.configured_model_gateway = lambda: ModelGateway(  # type: ignore[assignment]
        provider=DeterministicFakeProvider(
            started=provider_started,
            release=provider_release,
        ),
        model_id=_MODEL_ID,
    )

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    schema_name = _tenant_schema_name(db_engine, target["tenant_id"])

    async def _tenant_override(request: Request) -> AsyncIterator[SimpleNamespace]:
        # The real JWT principal path is covered by the API test suites; this
        # suite overrides tenant resolution and varies the actor through a
        # test-only header so the cancellation scope checks still run over
        # real HTTP with real concurrency.
        token = set_current_schema(schema_name)
        try:
            yield SimpleNamespace(
                tenant_id=target["tenant_id"],
                schema_name=schema_name,
                user_id=request.headers.get("X-Test-Actor", ACTOR_ID),
            )
        finally:
            reset_schema(token)

    app.dependency_overrides[get_current_tenant] = _tenant_override
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="off",
            ws="none",  # no WebSocket protocol import: pytest -W error chokes on
            # the websockets.legacy deprecation warning otherwise
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    base_url = f"http://127.0.0.1:{port}"
    try:
        with (
            httpx.Client(base_url=base_url, timeout=20, trust_env=False) as client,
            client.stream(
                "POST",
                f"/api/v1/workspaces/{target['workspace_id']}/agent-alpha/invoke",
                headers={"Idempotency-Key": "alpha-cancel-key"},
                json={
                    "agent_version_id": str(target["agent_version_id"]),
                    "message": "cancel me",
                    "top_k": 1,
                },
            ) as response,
        ):
            assert response.status_code == 200
            lines = response.iter_lines()
            first_block = "\n".join([next(lines), next(lines)])
            invocation_id = None
            for line in first_block.split("\n"):
                if line.startswith("data:"):
                    invocation_id = str(json.loads(line[5:].strip())["invocation_id"])
            assert invocation_id
            assert provider_started.wait(timeout=5)
            _assert_live_runtime_binding(
                db_engine,
                tenant_id=str(target["tenant_id"]),
                task_id=invocation_id,
            )

            # wrong actor / workspace must not cancel
            assert (
                client.post(
                    f"/api/v1/workspaces/{target['workspace_id']}/agent-alpha/"
                    f"invocations/{invocation_id}/cancel",
                    headers={"X-Test-Actor": str(uuid.uuid4())},
                ).json()["cancellation_requested"]
                is False
            )
            other_workspace = str(uuid.uuid4())
            assert (
                client.post(
                    f"/api/v1/workspaces/{other_workspace}/agent-alpha/"
                    f"invocations/{invocation_id}/cancel"
                ).json()["cancellation_requested"]
                is False
            )
            # correct actor cancels
            cancel = client.post(
                f"/api/v1/workspaces/{target['workspace_id']}/agent-alpha/"
                f"invocations/{invocation_id}/cancel"
            )
            assert cancel.status_code == 200
            assert cancel.json()["cancellation_requested"] is True
            provider_release.set()

            remainder = "\n".join(lines)
            assert "event: cancelled" in remainder

        with db_engine.connect() as connection:
            attempt_state = connection.execute(
                text(
                    "SELECT a.state FROM omnibase_meta.agent_attempts a "
                    "JOIN omnibase_meta.agent_tasks t ON t.id = a.task_id "
                    "WHERE t.id = :invocation AND a.tenant_id = :tenant"
                ),
                {"invocation": invocation_id, "tenant": target["tenant_id"]},
            ).scalar_one()
            task_state = connection.execute(
                text(
                    "SELECT state FROM omnibase_meta.agent_tasks "
                    "WHERE id = :invocation AND tenant_id = :tenant"
                ),
                {"invocation": invocation_id, "tenant": target["tenant_id"]},
            ).scalar_one()
        assert attempt_state == "cancelled"
        assert task_state == "cancelled"
        _assert_terminal_runtime_state(
            db_engine,
            tenant_id=str(target["tenant_id"]),
            task_id=invocation_id,
            agent_state="cancelled",
            workspace_state="cancelled",
            lease_state="revoked",
        )
    finally:
        provider_release.set()
        server.should_exit = True
        thread.join(timeout=10)
        engineering_module.configured_model_gateway = None  # type: ignore[assignment]
        os.environ.pop("AGENT_LITE_ENGINEERING_ENABLED", None)
        os.environ.pop("AGENT_ALPHA_ENGINEERING_ENABLED", None)
        os.environ.pop("ENV", None)


def test_alpha_rejects_tool_bearing_version(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    from omnibase.agent_alpha import engineering as engineering_module
    from omnibase.model_gateway import ModelGateway

    # Reuse the shared helper's default tool-bearing version (rag_search).
    target = _seed_alpha_target(db_engine, run_owned_resources, "alpha-tools", tool_free=False)
    os.environ["AGENT_LITE_ENGINEERING_ENABLED"] = "true"
    os.environ["AGENT_ALPHA_ENGINEERING_ENABLED"] = "true"
    os.environ["ENV"] = "development"
    engineering_module.configured_model_gateway = lambda: ModelGateway(  # type: ignore[assignment]
        provider=DeterministicFakeProvider(),
        model_id=_MODEL_ID,
    )
    try:
        client = _client(db_engine, target["tenant_id"])
        status, _, body = _invoke_events(
            client,
            str(target["workspace_id"]),
            str(target["agent_version_id"]),
            "hello",
            idempotency_key="alpha-tools-key",
        )
        assert status == 409
        assert "agent_alpha_tools_forbidden" in body
    finally:
        engineering_module.configured_model_gateway = None  # type: ignore[assignment]
        os.environ.pop("AGENT_LITE_ENGINEERING_ENABLED", None)
        os.environ.pop("AGENT_ALPHA_ENGINEERING_ENABLED", None)
        os.environ.pop("ENV", None)


def test_alpha_deterministic_failure_clears_both_runtime_ledgers(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    from omnibase.agent_alpha import engineering as engineering_module
    from omnibase.model_gateway import ModelGateway

    target = _seed_alpha_target(db_engine, run_owned_resources, "alpha-failed")
    os.environ["AGENT_LITE_ENGINEERING_ENABLED"] = "true"
    os.environ["AGENT_ALPHA_ENGINEERING_ENABLED"] = "true"
    os.environ["ENV"] = "development"
    engineering_module.configured_model_gateway = lambda: ModelGateway(  # type: ignore[assignment]
        provider=DeterministicFakeProvider(controlled_failure=True),
        model_id=_MODEL_ID,
    )
    try:
        client = _client(db_engine, target["tenant_id"])
        status, events, _ = _invoke_events(
            client,
            str(target["workspace_id"]),
            str(target["agent_version_id"]),
            "fail deterministically",
            idempotency_key="alpha-failed-key",
        )
        assert status == 200
        assert events[-1] == (
            "error",
            {"code": "agent_alpha_deterministic_failure"},
        )
        task_id = str(events[0][1]["task_id"])
        _assert_terminal_runtime_state(
            db_engine,
            tenant_id=str(target["tenant_id"]),
            task_id=task_id,
            agent_state="failed",
            workspace_state="failed",
            lease_state="revoked",
        )
    finally:
        engineering_module.configured_model_gateway = None  # type: ignore[assignment]
        os.environ.pop("AGENT_LITE_ENGINEERING_ENABLED", None)
        os.environ.pop("AGENT_ALPHA_ENGINEERING_ENABLED", None)
        os.environ.pop("ENV", None)


def test_alpha_unknown_provider_outcome_never_replays(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    from omnibase.agent_alpha import engineering as engineering_module
    from omnibase.model_gateway import ModelGateway

    target = _seed_alpha_target(db_engine, run_owned_resources, "alpha-unknown")
    os.environ["AGENT_LITE_ENGINEERING_ENABLED"] = "true"
    os.environ["AGENT_ALPHA_ENGINEERING_ENABLED"] = "true"
    os.environ["ENV"] = "development"
    counts: list[int] = [0]
    engineering_module.configured_model_gateway = lambda: ModelGateway(  # type: ignore[assignment]
        provider=DeterministicFakeProvider(fail=True, call_count=counts),
        model_id=_MODEL_ID,
    )
    try:
        client = _client(db_engine, target["tenant_id"])
        status, events, _ = _invoke_events(
            client,
            str(target["workspace_id"]),
            str(target["agent_version_id"]),
            "ambiguous",
            idempotency_key="alpha-unknown-key",
        )
        assert status == 200
        assert events[-1][0] == "error"
        task_id = str(events[0][1]["task_id"])
        with db_engine.connect() as connection:
            effect_state = connection.execute(
                text(
                    "SELECT state FROM omnibase_meta.agent_task_effects "
                    "WHERE task_id = :task AND tenant_id = :tenant"
                ),
                {"task": task_id, "tenant": target["tenant_id"]},
            ).scalar_one()
            attempt_state = connection.execute(
                text(
                    "SELECT state FROM omnibase_meta.agent_attempts "
                    "WHERE task_id = :task AND tenant_id = :tenant"
                ),
                {"task": task_id, "tenant": target["tenant_id"]},
            ).scalar_one()
            task_state = connection.execute(
                text(
                    "SELECT state FROM omnibase_meta.agent_tasks "
                    "WHERE id = :task AND tenant_id = :tenant"
                ),
                {"task": task_id, "tenant": target["tenant_id"]},
            ).scalar_one()
            reconciliation_count = connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.agent_reconciliation_cases "
                    "WHERE task_id = :task AND tenant_id = :tenant"
                ),
                {"task": task_id, "tenant": target["tenant_id"]},
            ).scalar_one()
        assert effect_state == "unknown"
        assert attempt_state == "unknown"
        assert task_state == "blocked_unknown"
        assert int(reconciliation_count) == 1
        assert counts[0] == 1
        _assert_terminal_runtime_state(
            db_engine,
            tenant_id=str(target["tenant_id"]),
            task_id=task_id,
            agent_state="failed",
            workspace_state="failed",
            lease_state="revoked",
        )
    finally:
        engineering_module.configured_model_gateway = None  # type: ignore[assignment]
        os.environ.pop("AGENT_LITE_ENGINEERING_ENABLED", None)
        os.environ.pop("AGENT_ALPHA_ENGINEERING_ENABLED", None)
        os.environ.pop("ENV", None)


def test_alpha_api_and_ledger_contain_no_secrets(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    from omnibase.agent_alpha import engineering as engineering_module
    from omnibase.model_gateway import ModelGateway

    target = _seed_alpha_target(db_engine, run_owned_resources, "alpha-secrets")
    os.environ["AGENT_LITE_ENGINEERING_ENABLED"] = "true"
    os.environ["AGENT_ALPHA_ENGINEERING_ENABLED"] = "true"
    os.environ["ENV"] = "development"
    engineering_module.configured_model_gateway = lambda: ModelGateway(  # type: ignore[assignment]
        provider=DeterministicFakeProvider(),
        model_id=_MODEL_ID,
    )
    try:
        client = _client(db_engine, target["tenant_id"])
        status, events, raw = _invoke_events(
            client,
            str(target["workspace_id"]),
            str(target["agent_version_id"]),
            "secrets?",
            idempotency_key="alpha-secrets-key",
        )
        assert status == 200
        assert "api_key" not in raw.lower()
        assert "authorization" not in raw.lower()
        assert "postgres" not in raw.lower()
        assert "omnibase_meta" not in raw.lower()
        task_id = str(events[0][1]["task_id"])
        with db_engine.connect() as connection:
            effect_digest = connection.execute(
                text(
                    "SELECT result_digest FROM omnibase_meta.agent_task_effects "
                    "WHERE task_id = :task AND tenant_id = :tenant"
                ),
                {"task": task_id, "tenant": target["tenant_id"]},
            ).scalar_one()
        assert effect_digest
    finally:
        engineering_module.configured_model_gateway = None  # type: ignore[assignment]
        os.environ.pop("AGENT_LITE_ENGINEERING_ENABLED", None)
        os.environ.pop("AGENT_ALPHA_ENGINEERING_ENABLED", None)
        os.environ.pop("ENV", None)
