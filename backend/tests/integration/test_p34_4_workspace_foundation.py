"""Guarded PostgreSQL acceptance tests for the P34.4 Workspace foundation."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnibase.workspaces import overlay, service
from omnibase.workspaces.service import WorkspaceConflict

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P34.4 integration tests require OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=_BACKEND_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )


def _upgrade_head() -> None:
    result = _run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="module", autouse=True)
def p344_schema(db_engine) -> None:
    _upgrade_head()


def _tenant(connection, run_owned_resources, label: str, *, track: bool = True) -> str:
    tenant_id = str(uuid.uuid4())
    suffix = uuid.uuid4().hex[:8]
    schema_name = f"tenant_{suffix}"
    connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.tenants "
            "(id, name, slug, schema_name, is_default, is_active) "
            "VALUES (:id, :name, :slug, :schema, FALSE, TRUE)"
        ),
        {
            "id": tenant_id,
            "name": f"P34.4 {label}",
            "slug": f"p344-{label}-{suffix}",
            "schema": schema_name,
        },
    )
    if track:
        run_owned_resources.add(tenant_id, schema_name)
    return tenant_id


def _tenant_schema(connection, tenant_id: str) -> str:
    return str(
        connection.execute(
            text("SELECT schema_name FROM omnibase_meta.tenants WHERE id = :tenant"),
            {"tenant": tenant_id},
        ).scalar_one()
    )


def _tenant_user(connection, schema_name: str, label: str, *, admin: bool = False) -> str:
    assert schema_name.startswith("tenant_")
    user_id = str(uuid.uuid4())
    connection.execute(
        text(
            f'INSERT INTO "{schema_name}".users '  # noqa: S608
            "(id, email, password_hash, is_tenant_admin, is_active) "
            "VALUES (:id, :email, :password_hash, :admin, TRUE)"
        ),
        {
            "id": user_id,
            "email": f"{label}-{uuid.uuid4().hex[:8]}@example.invalid",
            "password_hash": uuid.uuid4().hex,
            "admin": admin,
        },
    )
    return user_id


def _set_tenant_search_path(session: Session, schema_name: str) -> None:
    assert schema_name.startswith("tenant_")
    session.execute(text(f'SET LOCAL search_path TO "{schema_name}", omnibase_meta, public'))


def _membership(
    connection,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    *,
    role: str = "owner",
) -> None:
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.workspace_memberships "
            "(tenant_id, workspace_id, user_id, role, state, created_by_user_id) "
            "VALUES (:tenant, :workspace, :user, :role, 'active', :user)"
        ),
        {
            "tenant": tenant_id,
            "workspace": workspace_id,
            "user": user_id,
            "role": role,
        },
    )


def _template(connection, tenant_id: str) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_templates "
                "(tenant_id, template_key, version, display_name, digest, "
                "template_spec, created_by_user_id) "
                "VALUES (:tenant, :key, 1, 'Synthetic', :digest, "
                '\'{"profile":"metadata-only"}\'::jsonb, :actor) RETURNING id'
            ),
            {
                "tenant": tenant_id,
                "key": f"synthetic-{uuid.uuid4().hex[:8]}",
                "digest": uuid.uuid4().hex + uuid.uuid4().hex,
                "actor": str(uuid.uuid4()),
            },
        ).scalar_one()
    )


def _workspace(connection, tenant_id: str, template_id: str, label: str) -> str:
    workspace_id = str(
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(tenant_id, kind, owner_type, display_name, state, policy_class) "
                "VALUES (:tenant, 'workspace', 'system', :label, 'stopped', "
                "'workspace_private') RETURNING id"
            ),
            {"tenant": tenant_id, "label": label},
        ).scalar_one()
    )
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.workspaces "
            "(id, tenant_id, template_id, owner_user_id, display_name, "
            "desired_state, observed_state, quota) "
            "VALUES (:id, :tenant, :template, :owner, :label, 'stopped', "
            "'stopped', CAST(:quota AS jsonb))"
        ),
        {
            "id": workspace_id,
            "tenant": tenant_id,
            "template": template_id,
            "owner": str(uuid.uuid4()),
            "label": label,
            "quota": '{"max_active_runs":1}',
        },
    )
    return workspace_id


def _node(connection, tenant_id: str, workspace_id: str, label: str) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_nodes "
                "(tenant_id, workspace_id, owner_user_id, display_name, identity_digest, "
                "state, attestation_state) VALUES (:tenant, :workspace, :owner, :label, "
                ":digest, 'active', 'verified') RETURNING id"
            ),
            {
                "tenant": tenant_id,
                "workspace": workspace_id,
                "owner": str(uuid.uuid4()),
                "label": label,
                "digest": uuid.uuid4().hex + uuid.uuid4().hex,
            },
        ).scalar_one()
    )


def _attest_node(connection, tenant_id: str, node_id: str) -> None:
    now = datetime.now(UTC)
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.node_attestations "
            "(tenant_id, node_id, nonce_digest, evidence_digest, verifier, state, "
            "verified_at, expires_at) VALUES (:tenant, :node, :nonce, :evidence, "
            "'synthetic-test', 'verified', :verified, :expires)"
        ),
        {
            "tenant": tenant_id,
            "node": node_id,
            "nonce": uuid.uuid4().hex + uuid.uuid4().hex,
            "evidence": uuid.uuid4().hex + uuid.uuid4().hex,
            "verified": now - timedelta(seconds=1),
            "expires": now + timedelta(minutes=5),
        },
    )


def _peer_grant(
    connection,
    tenant_id: str,
    workspace_id: str,
    source_node_id: str,
    target_node_id: str,
) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.peer_grants "
                "(tenant_id, workspace_id, source_node_id, target_node_id, actions, "
                "expires_at, created_by_user_id) VALUES (:tenant, :workspace, :source, "
                ":target, ARRAY['peer.connect','service.consume']::varchar[], :expires, "
                ":actor) RETURNING id"
            ),
            {
                "tenant": tenant_id,
                "workspace": workspace_id,
                "source": source_node_id,
                "target": target_node_id,
                "expires": datetime.now(UTC) + timedelta(minutes=5),
                "actor": str(uuid.uuid4()),
            },
        ).scalar_one()
    )


def _service_advertisement(
    connection,
    tenant_id: str,
    workspace_id: str,
    node_id: str,
) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.service_advertisements "
                "(tenant_id, workspace_id, node_id, service_key, protocol, logical_port, "
                "actions, generation, expires_at) VALUES (:tenant, :workspace, :node, "
                ":service_key, 'artifact', 443, ARRAY['service.consume']::varchar[], 1, "
                ":expires) RETURNING id"
            ),
            {
                "tenant": tenant_id,
                "workspace": workspace_id,
                "node": node_id,
                "service_key": f"synthetic-{uuid.uuid4().hex[:8]}",
                "expires": datetime.now(UTC) + timedelta(minutes=5),
            },
        ).scalar_one()
    )


def _run(
    connection,
    tenant_id: str,
    workspace_id: str,
    *,
    observed_state: str = "queued",
) -> str:
    run_id = str(
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(tenant_id, kind, owner_type, owner_id, parent_id, display_name, "
                "state, policy_class) VALUES (:tenant, 'run', 'workspace', :workspace, "
                ":workspace, 'Synthetic run', 'provisioning', 'workspace_private') "
                "RETURNING id"
            ),
            {"tenant": tenant_id, "workspace": workspace_id},
        ).scalar_one()
    )
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.workspace_runs "
            "(id, tenant_id, workspace_id, kind, generation, desired_state, "
            "observed_state, request_digest, created_by_user_id) "
            "VALUES (:id, :tenant, :workspace, 'batch', 1, 'running', :observed, "
            ":digest, :actor)"
        ),
        {
            "id": run_id,
            "tenant": tenant_id,
            "workspace": workspace_id,
            "observed": observed_state,
            "digest": uuid.uuid4().hex + uuid.uuid4().hex,
            "actor": str(uuid.uuid4()),
        },
    )
    return run_id


def test_0007_and_0008_keep_workspace_foundation_global_only(db_engine) -> None:
    with db_engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM omnibase_meta.alembic_version")
        ).scalar_one()
        tables = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'omnibase_meta'"
                )
            ).scalars()
        )
        tenant_p344_tables = connection.execute(
            text(
                "SELECT count(1) FROM information_schema.tables "
                "WHERE table_schema LIKE 'tenant\\_%' ESCAPE '\\' "
                "AND table_name IN ('workspaces', 'workspace_runs', 'run_leases', "
                "'workspace_nodes', 'network_leases')"
            )
        ).scalar_one()
        constraints = set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conname IN "
                    "('resource_registry_parent_tenant_fk', "
                    "'resource_lineage_source_tenant_fk', "
                    "'resource_lineage_derived_tenant_fk', "
                    "'run_leases_node_workspace_tenant_fk', "
                    "'resource_scope_bindings_workspace_tenant_fk', "
                    "'resource_scope_bindings_run_workspace_tenant_fk', "
                    "'workspaces_restored_snapshot_tenant_fk', "
                    "'run_leases_node_fencing_check', "
                    "'network_leases_requester_tenant_fk', "
                    "'network_lease_cursors_requester_tenant_fk', "
                    "'network_lease_cursors_tuple_uq', "
                    "'collaboration_events_artifact_workspace_tenant_fk', "
                    "'collaboration_events_parent_workspace_tenant_fk')"
                )
            ).scalars()
        )
    assert revision == "0008"
    assert {
        "workspaces",
        "workspace_memberships",
        "resource_scope_bindings",
        "workspace_runs",
        "run_leases",
        "workspace_nodes",
        "peer_grants",
        "service_advertisements",
        "network_lease_cursors",
        "network_leases",
        "workspace_authorities",
        "collaboration_artifacts",
        "collaboration_events",
    } <= tables
    assert tenant_p344_tables == 0
    assert len(constraints) == 13


def test_database_rejects_cross_workspace_node_run_and_peer_bindings(
    db_engine,
    run_owned_resources,
) -> None:
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        tenant_id = _tenant(
            connection,
            run_owned_resources,
            "cross-workspace",
            track=False,
        )
        template_id = _template(connection, tenant_id)
        workspace_a = _workspace(connection, tenant_id, template_id, "Workspace A")
        workspace_b = _workspace(connection, tenant_id, template_id, "Workspace B")
        node_a = _node(connection, tenant_id, workspace_a, "Node A")
        node_b = _node(connection, tenant_id, workspace_b, "Node B")
        run_a = _run(connection, tenant_id, workspace_a)
        now = datetime.now(UTC)

        with (
            pytest.raises(IntegrityError, match="run_leases_node_workspace_tenant_fk"),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.run_leases "
                    "(tenant_id, run_id, workspace_id, node_id, generation, "
                    "node_fencing_token, fencing_token, heartbeat_at, expires_at) VALUES "
                    "(:tenant, :run, :workspace, :node, 1, 1, 1, :now, :expires)"
                ),
                {
                    "tenant": tenant_id,
                    "run": run_a,
                    "workspace": workspace_a,
                    "node": node_b,
                    "now": now,
                    "expires": now + timedelta(minutes=1),
                },
            )

        with (
            pytest.raises(IntegrityError, match="peer_grants_target_tenant_fk"),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.peer_grants "
                    "(tenant_id, workspace_id, source_node_id, target_node_id, actions, "
                    "expires_at, created_by_user_id) VALUES (:tenant, :workspace, "
                    ":source, :target, ARRAY['peer.connect']::varchar[], :expires, :actor)"
                ),
                {
                    "tenant": tenant_id,
                    "workspace": workspace_a,
                    "source": node_a,
                    "target": node_b,
                    "expires": now + timedelta(minutes=1),
                    "actor": str(uuid.uuid4()),
                },
            )

        with (
            pytest.raises(
                IntegrityError,
                match="resource_scope_bindings_run_workspace_tenant_fk",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.resource_scope_bindings "
                    "(resource_id, tenant_id, scope_class, workspace_id, run_id) "
                    "VALUES (:run, :tenant, 'run_ephemeral', :workspace, :run)"
                ),
                {
                    "run": run_a,
                    "tenant": tenant_id,
                    "workspace": workspace_b,
                },
            )
    finally:
        transaction.rollback()
        connection.close()


def test_network_lease_requester_composite_foreign_keys_fail_closed(
    db_engine,
    run_owned_resources,
) -> None:
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        tenant_a = _tenant(connection, run_owned_resources, "network-fk-a", track=False)
        tenant_b = _tenant(connection, run_owned_resources, "network-fk-b", track=False)
        template_a = _template(connection, tenant_a)
        template_b = _template(connection, tenant_b)
        workspace_a = _workspace(connection, tenant_a, template_a, "Network A")
        workspace_other = _workspace(connection, tenant_a, template_a, "Network other")
        workspace_b = _workspace(connection, tenant_b, template_b, "Network B")
        source = _node(connection, tenant_a, workspace_a, "Source")
        target = _node(connection, tenant_a, workspace_a, "Target")
        wrong_workspace_node = _node(connection, tenant_a, workspace_other, "Wrong workspace")
        wrong_tenant_node = _node(connection, tenant_b, workspace_b, "Wrong tenant")
        peer_id = _peer_grant(connection, tenant_a, workspace_a, source, target)
        service_id = _service_advertisement(connection, tenant_a, workspace_a, source)

        cursor_sql = text(
            "INSERT INTO omnibase_meta.network_lease_cursors "
            "(tenant_id, workspace_id, peer_grant_id, service_id, requester_node_id) "
            "VALUES (:tenant, :workspace, :peer, :service, :requester)"
        )
        lease_sql = text(
            "INSERT INTO omnibase_meta.network_leases "
            "(tenant_id, workspace_id, peer_grant_id, service_id, requester_node_id, "
            "fencing_token, expires_at) VALUES (:tenant, :workspace, :peer, :service, "
            ":requester, 1, :expires)"
        )
        common = {
            "tenant": tenant_a,
            "workspace": workspace_a,
            "peer": peer_id,
            "service": service_id,
            "expires": datetime.now(UTC) + timedelta(minutes=1),
        }

        for requester in (wrong_workspace_node, wrong_tenant_node, str(uuid.uuid4())):
            with (
                pytest.raises(
                    IntegrityError,
                    match="network_lease_cursors_requester_tenant_fk",
                ),
                connection.begin_nested(),
            ):
                connection.execute(cursor_sql, {**common, "requester": requester})
            with (
                pytest.raises(
                    IntegrityError,
                    match="network_leases_requester_tenant_fk",
                ),
                connection.begin_nested(),
            ):
                connection.execute(lease_sql, {**common, "requester": requester})
    finally:
        transaction.rollback()
        connection.close()


def test_network_lease_cursor_allocates_monotonic_tokens_and_fences_old_lease(
    db_engine,
    run_owned_resources,
) -> None:
    tenant_id: str
    workspace_id: str
    source: str
    target: str
    peer_id: str
    service_id: str
    with db_engine.begin() as connection:
        tenant_id = _tenant(connection, run_owned_resources, "network-cursor")
        template_id = _template(connection, tenant_id)
        workspace_id = _workspace(connection, tenant_id, template_id, "Network cursor")
        source = _node(connection, tenant_id, workspace_id, "Source")
        target = _node(connection, tenant_id, workspace_id, "Target")
        _attest_node(connection, tenant_id, source)
        _attest_node(connection, tenant_id, target)
        peer_id = _peer_grant(connection, tenant_id, workspace_id, source, target)
        service_id = _service_advertisement(connection, tenant_id, workspace_id, source)

    with Session(db_engine) as session, session.begin():
        first = overlay.acquire_network_lease(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            peer_grant_id=peer_id,
            service_id=service_id,
            requester_node_id=target,
            ttl_seconds=30,
        )
        first_id = first.id
        first_token = first.fencing_token

    with db_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE omnibase_meta.network_leases SET expires_at = now() - interval '1 second' "
                "WHERE id = :lease"
            ),
            {"lease": first_id},
        )

    with Session(db_engine) as session, session.begin():
        second = overlay.acquire_network_lease(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            peer_grant_id=peer_id,
            service_id=service_id,
            requester_node_id=target,
            ttl_seconds=30,
        )
        second_id = second.id
        second_token = second.fencing_token

    assert second_token > first_token
    with Session(db_engine) as session:
        with pytest.raises(service.LeaseRejected):
            overlay.validate_network_lease(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                lease_id=first_id,
                requester_node_id=target,
                fencing_token=first_token,
            )
        assert (
            overlay.validate_network_lease(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                lease_id=second_id,
                requester_node_id=target,
                fencing_token=second_token,
            ).id
            == second_id
        )


def test_database_rejects_cross_workspace_collaboration_references_and_grant_actions(
    db_engine,
    run_owned_resources,
) -> None:
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        tenant_id = _tenant(
            connection,
            run_owned_resources,
            "collaboration-scope",
            track=False,
        )
        template_id = _template(connection, tenant_id)
        workspace_a = _workspace(connection, tenant_id, template_id, "Workspace A")
        workspace_b = _workspace(connection, tenant_id, template_id, "Workspace B")
        node_a = _node(connection, tenant_id, workspace_a, "Node A")
        node_b = _node(connection, tenant_id, workspace_b, "Node B")
        artifact_id = connection.execute(
            text(
                "INSERT INTO omnibase_meta.collaboration_artifacts "
                "(tenant_id, workspace_id, authority_epoch, content_digest, size_bytes, "
                "media_type, created_by_node_id) VALUES (:tenant, :workspace, 1, "
                ":digest, 0, 'application/octet-stream', :node) RETURNING id"
            ),
            {
                "tenant": tenant_id,
                "workspace": workspace_a,
                "digest": uuid.uuid4().hex + uuid.uuid4().hex,
                "node": node_a,
            },
        ).scalar_one()
        parent_event_id = connection.execute(
            text(
                "INSERT INTO omnibase_meta.collaboration_events "
                "(tenant_id, workspace_id, authority_node_id, authority_epoch, sequence, "
                "event_type, event_digest) VALUES (:tenant, :workspace, :node, 1, 1, "
                "'git_ref', :digest) RETURNING id"
            ),
            {
                "tenant": tenant_id,
                "workspace": workspace_a,
                "node": node_a,
                "digest": uuid.uuid4().hex + uuid.uuid4().hex,
            },
        ).scalar_one()

        with (
            pytest.raises(
                IntegrityError,
                match="collaboration_events_artifact_workspace_tenant_fk",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.collaboration_events "
                    "(tenant_id, workspace_id, authority_node_id, authority_epoch, sequence, "
                    "event_type, event_digest, artifact_id) VALUES (:tenant, :workspace, "
                    ":node, 1, 1, 'artifact_published', :digest, :artifact)"
                ),
                {
                    "tenant": tenant_id,
                    "workspace": workspace_b,
                    "node": node_b,
                    "digest": uuid.uuid4().hex + uuid.uuid4().hex,
                    "artifact": artifact_id,
                },
            )

        with (
            pytest.raises(
                IntegrityError,
                match="collaboration_events_parent_workspace_tenant_fk",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.collaboration_events "
                    "(tenant_id, workspace_id, authority_node_id, authority_epoch, sequence, "
                    "event_type, event_digest, parent_event_id) VALUES (:tenant, :workspace, "
                    ":node, 1, 1, 'git_ref', :digest, :parent)"
                ),
                {
                    "tenant": tenant_id,
                    "workspace": workspace_b,
                    "node": node_b,
                    "digest": uuid.uuid4().hex + uuid.uuid4().hex,
                    "parent": parent_event_id,
                },
            )

        with (
            pytest.raises(
                IntegrityError,
                match="workspace_scope_grants_actions_allowlist_check",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.workspace_scope_grants "
                    "(tenant_id, target_workspace_id, source_scope, source_owner_id, "
                    "resource_id, actions, created_by_user_id) VALUES (:tenant, :target, "
                    "'workspace_private', :source, :resource, "
                    "ARRAY['resource.write']::varchar[], :actor)"
                ),
                {
                    "tenant": tenant_id,
                    "target": workspace_b,
                    "source": workspace_a,
                    "resource": workspace_a,
                    "actor": str(uuid.uuid4()),
                },
            )
    finally:
        transaction.rollback()
        connection.close()


def test_partial_unique_indexes_prevent_double_runtime_lease_and_authority(
    db_engine,
    run_owned_resources,
) -> None:
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        tenant_id = _tenant(
            connection,
            run_owned_resources,
            "single-active",
            track=False,
        )
        template_id = _template(connection, tenant_id)
        workspace_id = _workspace(connection, tenant_id, template_id, "Single active")
        node_a = _node(connection, tenant_id, workspace_id, "Node A")
        node_b = _node(connection, tenant_id, workspace_id, "Node B")
        _run(connection, tenant_id, workspace_id, observed_state="running")
        with (
            pytest.raises(IntegrityError, match="workspace_runs_one_active_uq"),
            connection.begin_nested(),
        ):
            _run(connection, tenant_id, workspace_id, observed_state="starting")

        run_id = _run(connection, tenant_id, workspace_id)
        now = datetime.now(UTC)
        lease_sql = text(
            "INSERT INTO omnibase_meta.run_leases "
            "(tenant_id, run_id, workspace_id, node_id, node_fencing_token, generation, "
            "fencing_token, heartbeat_at, expires_at) VALUES (:tenant, :run, :workspace, "
            ":node, 1, 1, :token, :now, :expires)"
        )
        connection.execute(
            lease_sql,
            {
                "tenant": tenant_id,
                "run": run_id,
                "workspace": workspace_id,
                "node": node_a,
                "token": 1,
                "now": now,
                "expires": now + timedelta(minutes=1),
            },
        )
        with (
            pytest.raises(IntegrityError, match="run_leases_one_active_uq"),
            connection.begin_nested(),
        ):
            connection.execute(
                lease_sql,
                {
                    "tenant": tenant_id,
                    "run": run_id,
                    "workspace": workspace_id,
                    "node": node_b,
                    "token": 2,
                    "now": now,
                    "expires": now + timedelta(minutes=1),
                },
            )

        authority_sql = text(
            "INSERT INTO omnibase_meta.workspace_authorities "
            "(tenant_id, workspace_id, authority_node_id, epoch, state, lease_expires_at) "
            "VALUES (:tenant, :workspace, :node, :epoch, 'active', :expires)"
        )
        connection.execute(
            authority_sql,
            {
                "tenant": tenant_id,
                "workspace": workspace_id,
                "node": node_a,
                "epoch": 1,
                "expires": now + timedelta(minutes=1),
            },
        )
        with (
            pytest.raises(IntegrityError, match="workspace_authorities_one_active_uq"),
            connection.begin_nested(),
        ):
            connection.execute(
                authority_sql,
                {
                    "tenant": tenant_id,
                    "workspace": workspace_id,
                    "node": node_b,
                    "epoch": 2,
                    "expires": now + timedelta(minutes=1),
                },
            )
    finally:
        transaction.rollback()
        connection.close()


def test_two_owners_cannot_concurrently_demote_the_last_owner(
    db_engine,
    run_owned_resources,
) -> None:
    with db_engine.begin() as connection:
        tenant_id = _tenant(connection, run_owned_resources, "owner-race")
        tenant_schema = _tenant_schema(connection, tenant_id)
    _upgrade_head()
    with db_engine.begin() as connection:
        owner_a = _tenant_user(connection, tenant_schema, "owner-a")
        owner_b = _tenant_user(connection, tenant_schema, "owner-b")
        template_id = _template(connection, tenant_id)
        workspace_id = _workspace(connection, tenant_id, template_id, "Owner race")
        _membership(connection, tenant_id, workspace_id, owner_a)
        _membership(connection, tenant_id, workspace_id, owner_b)

    barrier = threading.Barrier(2)

    def demote_self(actor_id: str) -> str:
        try:
            with Session(db_engine) as session, session.begin():
                _set_tenant_search_path(session, tenant_schema)
                barrier.wait(timeout=10)
                service.upsert_membership(
                    session,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    actor_user_id=actor_id,
                    target_user_id=actor_id,
                    role="maintainer",
                    expected_version=1,
                    request_id=str(uuid.uuid4()),
                )
            return "demoted"
        except WorkspaceConflict:
            return "last-owner-blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(demote_self, (owner_a, owner_b)))

    assert sorted(results) == ["demoted", "last-owner-blocked"]
    with db_engine.connect() as connection:
        rows = list(
            connection.execute(
                text(
                    "SELECT user_id, role FROM omnibase_meta.workspace_memberships "
                    "WHERE tenant_id = :tenant AND workspace_id = :workspace "
                    "AND state = 'active' ORDER BY user_id"
                ),
                {"tenant": tenant_id, "workspace": workspace_id},
            )
        )
    assert sum(role == "owner" for _, role in rows) == 1
    assert sum(role == "maintainer" for _, role in rows) == 1


def test_concurrent_template_registration_is_idempotent_or_conflicting(
    db_engine,
    run_owned_resources,
) -> None:
    with db_engine.begin() as connection:
        tenant_id = _tenant(connection, run_owned_resources, "template-race")
        tenant_schema = _tenant_schema(connection, tenant_id)
    _upgrade_head()
    with db_engine.begin() as connection:
        admin_id = _tenant_user(connection, tenant_schema, "template-admin", admin=True)

    def register(
        barrier: threading.Barrier,
        display_name: str,
        profile: str,
        template_key: str,
    ) -> tuple[str, str]:
        try:
            with Session(db_engine) as session, session.begin():
                _set_tenant_search_path(session, tenant_schema)
                barrier.wait(timeout=10)
                record = service.register_template(
                    session,
                    tenant_id=tenant_id,
                    actor_user_id=admin_id,
                    template_key=template_key,
                    version=1,
                    display_name=display_name,
                    template_spec={"profile": profile},
                    request_id=str(uuid.uuid4()),
                )
                return "ok", record.id
        except WorkspaceConflict:
            return "conflict", ""

    replay_key = f"replay-{uuid.uuid4().hex[:8]}"
    replay_barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replay_results = list(
            pool.map(
                lambda _: register(replay_barrier, "Exact replay", "metadata-only", replay_key),
                range(2),
            )
        )
    assert [status for status, _ in replay_results] == ["ok", "ok"]
    assert len({record_id for _, record_id in replay_results}) == 1

    conflict_key = f"conflict-{uuid.uuid4().hex[:8]}"
    conflict_barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (
            pool.submit(register, conflict_barrier, "Variant A", "a", conflict_key),
            pool.submit(register, conflict_barrier, "Variant B", "b", conflict_key),
        )
        conflict_results = [future.result(timeout=20) for future in futures]
    assert sorted(status for status, _ in conflict_results) == ["conflict", "ok"]

    with db_engine.connect() as connection:
        counts = dict(
            connection.execute(
                text(
                    "SELECT template_key, count(*) FROM omnibase_meta.workspace_templates "
                    "WHERE tenant_id = :tenant AND template_key IN (:replay, :conflict) "
                    "GROUP BY template_key"
                ),
                {"tenant": tenant_id, "replay": replay_key, "conflict": conflict_key},
            ).all()
        )
    assert counts == {replay_key: 1, conflict_key: 1}


def test_tenant_admin_downgrade_commits_before_template_registration(
    db_engine,
    run_owned_resources,
) -> None:
    with db_engine.begin() as connection:
        tenant_id = _tenant(connection, run_owned_resources, "admin-downgrade")
        tenant_schema = _tenant_schema(connection, tenant_id)
    _upgrade_head()
    with db_engine.begin() as connection:
        admin_id = _tenant_user(connection, tenant_schema, "downgraded-admin", admin=True)

    assert tenant_schema.startswith("tenant_")
    blocker = db_engine.connect()
    transaction = blocker.begin()
    try:
        blocker.execute(
            text(
                f'UPDATE "{tenant_schema}".users '  # noqa: S608
                "SET is_tenant_admin = FALSE WHERE id = :actor"
            ),
            {"actor": admin_id},
        )
        started = threading.Event()

        def register_after_downgrade() -> str:
            try:
                with Session(db_engine) as session, session.begin():
                    _set_tenant_search_path(session, tenant_schema)
                    started.set()
                    service.register_template(
                        session,
                        tenant_id=tenant_id,
                        actor_user_id=admin_id,
                        template_key=f"downgrade-{uuid.uuid4().hex[:8]}",
                        version=1,
                        display_name="Must be denied",
                        template_spec={"profile": "metadata-only"},
                        request_id=str(uuid.uuid4()),
                    )
                return "unexpected-success"
            except service.WorkspacePolicyDenied:
                return "denied"

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(register_after_downgrade)
            assert started.wait(timeout=10)
            transaction.commit()
            assert future.result(timeout=20) == "denied"
    finally:
        if transaction.is_active:
            transaction.rollback()
        blocker.close()

    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.workspace_templates "
                    "WHERE tenant_id = :tenant AND created_by_user_id = :actor"
                ),
                {"tenant": tenant_id, "actor": admin_id},
            ).scalar_one()
            == 0
        )


def test_node_revocation_revokes_run_lease_and_old_holder_cannot_resume(
    db_engine,
    run_owned_resources,
) -> None:
    with db_engine.begin() as connection:
        tenant_id = _tenant(connection, run_owned_resources, "node-run-revoke")
        template_id = _template(connection, tenant_id)
        workspace_id = _workspace(connection, tenant_id, template_id, "Node run revoke")
        actor_id = str(uuid.uuid4())
        _membership(connection, tenant_id, workspace_id, actor_id)
        node_id = _node(connection, tenant_id, workspace_id, "Runner")
        _attest_node(connection, tenant_id, node_id)
        run_id = _run(connection, tenant_id, workspace_id)

    with Session(db_engine) as session, session.begin():
        lease = service.claim_run_lease(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            node_id=node_id,
        )
        lease_id = lease.id
        fencing_token = lease.fencing_token

    with Session(db_engine) as session, session.begin():
        overlay.revoke_node(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            node_id=node_id,
            actor_user_id=actor_id,
        )

    with db_engine.connect() as connection:
        lease_state = connection.execute(
            text("SELECT state FROM omnibase_meta.run_leases WHERE id = :lease"),
            {"lease": lease_id},
        ).scalar_one()
        run_state = connection.execute(
            text(
                "SELECT desired_state, observed_state, next_fencing_token, last_error_code "
                "FROM omnibase_meta.workspace_runs WHERE id = :run"
            ),
            {"run": run_id},
        ).one()
    assert lease_state == "revoked"
    assert run_state.observed_state == "queued"
    assert run_state.next_fencing_token > fencing_token + 1
    assert run_state.last_error_code == "node_revoked_before_start"

    with Session(db_engine) as session:
        with pytest.raises(service.LeaseRejected):
            service.heartbeat_run_lease(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                lease_id=lease_id,
                node_id=node_id,
                generation=1,
                fencing_token=fencing_token,
            )
        with pytest.raises(service.LeaseRejected):
            service.submit_run_state(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                lease_id=lease_id,
                node_id=node_id,
                generation=1,
                fencing_token=fencing_token,
                observed_state="running",
            )


def test_0007_populated_downgrade_is_fail_closed(db_engine, run_owned_resources) -> None:
    tenant_id: str | None = None
    template_id: str | None = None
    try:
        with db_engine.begin() as connection:
            tenant_id = _tenant(connection, run_owned_resources, "downgrade")
            template_id = _template(connection, tenant_id)

        downgrade = _run_alembic("downgrade", "0006")
        assert downgrade.returncode != 0
        output = downgrade.stdout + downgrade.stderr
        assert "P34.4 downgrade refused" in output
        with db_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM omnibase_meta.alembic_version")
                ).scalar_one()
                == "0008"
            )
    finally:
        if template_id is not None and tenant_id is not None:
            with db_engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM omnibase_meta.workspace_templates "
                        "WHERE tenant_id = :tenant AND id = :template"
                    ),
                    {"tenant": tenant_id, "template": template_id},
                )
