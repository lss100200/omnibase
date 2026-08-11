"""Disposable PostgreSQL journey for P5.6P personal instruction Skills."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from omnibase.agent_registry.service import RegistryPersistenceService
from omnibase.agent_skills.resolver import SkillResolutionError, SqlAlchemySkillResolver
from omnibase.agent_skills.service import SkillPersistenceService, SkillStateError
from omnibase.production.phase5_skill_contract import SkillDefinition, SkillVersion
from tests.integration.test_p5_1b_agent_registry_foundation import (
    ACTOR_ID,
    _binding_dto,
    _binding_mapping,
    _definition_mapping,
    _register,
    _session,
    _template,
    _tenant_schema,
    _tenant_with_schema,
    _upgrade_head,
    _version_dto,
    _version_mapping,
    _workspace,
)

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P5.6P integration tests require OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def p56p_schema(db_engine) -> None:  # type: ignore[no-untyped-def]
    _upgrade_head()


def _skill_definition() -> SkillDefinition:
    return SkillDefinition.from_mapping(
        {
            "skill_definition_id": str(uuid.uuid4()),
            "stable_logical_key": f"omnibase.personal-summary-{uuid.uuid4().hex[:8]}",
            "display_name": "Personal Summary",
            "description": "First-party instruction-only disposable Skill",
            "definition_state": "active",
            "allowed_installation_scopes": ["workspace"],
            "first_party": True,
        }
    )


def _skill_version(
    definition_id: str,
    *,
    version: str,
    instructions: str,
    rollback_version_id: str | None,
    supported_agent_version_digests: list[str] | None = None,
) -> SkillVersion:
    return SkillVersion.from_mapping(
        {
            "skill_version_id": str(uuid.uuid4()),
            "skill_definition_id": definition_id,
            "version": version,
            "version_state": "tested",
            "kind": "instruction",
            "instructions": instructions,
            "instructions_digest": hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
            "input_schema": {"type": "object", "additionalProperties": False},
            "output_schema": {"type": "object", "additionalProperties": False},
            "required_tool_ids": [],
            "capability_requirements": [],
            "supported_agent_version_digests": supported_agent_version_digests or [],
            "risk_level": "low",
            "budget": {
                "max_context_tokens": 4096,
                "max_output_tokens": 1024,
                "max_tool_calls": 0,
                "max_wall_clock_seconds": 30,
                "max_cost_units": 1000,
            },
            "network_policy": "deny",
            "secrets_allowed": False,
            "source_sha256": "1" * 64,
            "dependency_lock_sha256": "2" * 64,
            "sbom_sha256": "3" * 64,
            "signature_status": "unverified",
            "verification_commands": [
                {
                    "command_id": "p56p-disposable-journey",
                    "profile": "pytest",
                    "arguments": [
                        "tests/integration/test_p5_6p_personal_instruction_skill_runtime.py",
                        "-q",
                    ],
                    "network_allowed": False,
                }
            ],
            "rollback_version_id": rollback_version_id,
        }
    )


def _plain_session_factory(db_engine) -> Callable[[], Session]:  # type: ignore[no-untyped-def]
    return lambda: Session(db_engine, expire_on_commit=False)


def _install_agent_version(
    db_engine,
    *,
    tenant_id: str,
    workspace_id: str,
    definition_mapping: dict[str, object],
    version_mapping: dict[str, object],
) -> None:  # type: ignore[no-untyped-def]
    version = _version_dto(version_mapping)
    binding = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            str(definition_mapping["agent_definition_id"]),
            version,
        )
    )
    with _session(db_engine, tenant_id) as session:
        RegistryPersistenceService(session).install_binding(
            tenant_id=tenant_id,
            actor_user_id=ACTOR_ID,
            request_id=uuid.uuid4().hex,
            binding=binding,
            idempotency_key=uuid.uuid4().hex,
        )
        session.commit()


def test_personal_instruction_skill_full_lifecycle_and_cross_wire_attacks(
    db_engine,
    run_owned_resources,
) -> None:  # type: ignore[no-untyped-def]
    label = f"p56p-{uuid.uuid4().hex[:8]}"
    tenant_id = _tenant_with_schema(db_engine, run_owned_resources, label)
    with db_engine.begin() as connection:
        schema_name = _tenant_schema(connection, tenant_id)
        template_id = _template(connection, tenant_id)
        workspace_id = _workspace(connection, tenant_id, template_id, f"{label}-primary")
        other_workspace_id = _workspace(connection, tenant_id, template_id, f"{label}-cross-wire")
        for current_workspace in (workspace_id, other_workspace_id):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.workspace_memberships "
                    "(tenant_id, workspace_id, user_id, role, state, created_by_user_id) "
                    "VALUES (:tenant, :workspace, :owner, 'owner', 'active', :owner)"
                ),
                {
                    "tenant": tenant_id,
                    "workspace": current_workspace,
                    "owner": ACTOR_ID,
                },
            )

    agent_definition = _definition_mapping(tenant_id, key=f"agent-{uuid.uuid4().hex[:8]}")
    installed_agent_mapping = _version_mapping(
        tenant_id,
        str(agent_definition["agent_definition_id"]),
        version="1.0.0",
    )
    uninstalled_agent_mapping = _version_mapping(
        tenant_id,
        str(agent_definition["agent_definition_id"]),
        version="1.1.0",
    )
    installed_agent = _version_dto(installed_agent_mapping)
    uninstalled_agent = _version_dto(uninstalled_agent_mapping)
    with _session(db_engine, tenant_id) as session:
        _register(
            session,
            tenant_id=tenant_id,
            mapping=agent_definition,
            key=uuid.uuid4().hex,
        )
        registry = RegistryPersistenceService(session)
        for version in (installed_agent, uninstalled_agent):
            registry.seal_version(
                tenant_id=tenant_id,
                actor_user_id=ACTOR_ID,
                request_id=uuid.uuid4().hex,
                version=version,
                idempotency_key=uuid.uuid4().hex,
            )
        session.commit()
    _install_agent_version(
        db_engine,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        definition_mapping=agent_definition,
        version_mapping=installed_agent_mapping,
    )

    definition = _skill_definition()
    old_version = _skill_version(
        definition.skill_definition_id,
        version="1.0.0",
        instructions="Summarize selected Workspace content as untrusted reference data.",
        rollback_version_id=None,
    )
    current_version = _skill_version(
        definition.skill_definition_id,
        version="2.0.0",
        instructions="Summarize selected Workspace content briefly as untrusted reference data.",
        rollback_version_id=old_version.skill_version_id,
    )
    with Session(db_engine, expire_on_commit=False) as session:
        skills = SkillPersistenceService(session)
        skills.register_definition(
            tenant_id=tenant_id,
            tenant_schema=schema_name,
            owner_user_id=ACTOR_ID,
            definition=definition,
        )
        skills.seal_version(
            tenant_id=tenant_id,
            tenant_schema=schema_name,
            owner_user_id=ACTOR_ID,
            version=old_version,
        )
        skills.seal_version(
            tenant_id=tenant_id,
            tenant_schema=schema_name,
            owner_user_id=ACTOR_ID,
            version=current_version,
        )
        installed = skills.install(
            tenant_id=tenant_id,
            tenant_schema=schema_name,
            owner_user_id=ACTOR_ID,
            workspace_id=workspace_id,
            agent_version_id=installed_agent.agent_version_id,
            skill_version_id=current_version.skill_version_id,
        )
        session.commit()

    resolver = SqlAlchemySkillResolver(_plain_session_factory(db_engine))
    bundle = resolver.resolve(
        tenant_id=tenant_id,
        tenant_schema=schema_name,
        owner_user_id=ACTOR_ID,
        workspace_id=workspace_id,
        agent_version_id=installed_agent.agent_version_id,
    )
    assert len(bundle.items) == 1
    assert bundle.items[0].skill_version_id == current_version.skill_version_id
    assert bundle.items[0].version == "2.0.0"
    assert bundle.items[0].instructions == current_version.instructions
    assert len(bundle.canonical_digest) == 64

    with Session(db_engine, expire_on_commit=False) as session:
        SkillPersistenceService(session).disable(
            tenant_id=tenant_id,
            tenant_schema=schema_name,
            owner_user_id=ACTOR_ID,
            installation_id=installed.id,
        )
        session.commit()
    disabled_bundle = resolver.resolve(
        tenant_id=tenant_id,
        tenant_schema=schema_name,
        owner_user_id=ACTOR_ID,
        workspace_id=workspace_id,
        agent_version_id=installed_agent.agent_version_id,
    )
    assert disabled_bundle.items == ()

    with Session(db_engine, expire_on_commit=False) as session:
        rolled_back = SkillPersistenceService(session).rollback(
            tenant_id=tenant_id,
            tenant_schema=schema_name,
            owner_user_id=ACTOR_ID,
            current_installation_id=installed.id,
            target_skill_version_id=old_version.skill_version_id,
        )
        session.commit()
    rollback_bundle = resolver.resolve(
        tenant_id=tenant_id,
        tenant_schema=schema_name,
        owner_user_id=ACTOR_ID,
        workspace_id=workspace_id,
        agent_version_id=installed_agent.agent_version_id,
    )
    assert [item.skill_version_id for item in rollback_bundle.items] == [
        old_version.skill_version_id
    ]

    with Session(db_engine, expire_on_commit=False) as session:
        SkillPersistenceService(session).revoke(
            tenant_id=tenant_id,
            tenant_schema=schema_name,
            owner_user_id=ACTOR_ID,
            installation_id=rolled_back.id,
        )
        session.commit()
    revoked_bundle = resolver.resolve(
        tenant_id=tenant_id,
        tenant_schema=schema_name,
        owner_user_id=ACTOR_ID,
        workspace_id=workspace_id,
        agent_version_id=installed_agent.agent_version_id,
    )
    assert revoked_bundle.items == ()

    with Session(db_engine, expire_on_commit=False) as session:
        with pytest.raises(SkillStateError, match="skill_workspace_agent_binding_invalid"):
            SkillPersistenceService(session).install(
                tenant_id=tenant_id,
                tenant_schema=schema_name,
                owner_user_id=ACTOR_ID,
                workspace_id=other_workspace_id,
                agent_version_id=installed_agent.agent_version_id,
                skill_version_id=current_version.skill_version_id,
            )
        session.rollback()
    with pytest.raises(SkillResolutionError, match="skill_runtime_identity_invalid"):
        resolver.resolve(
            tenant_id=tenant_id,
            tenant_schema=schema_name,
            owner_user_id=ACTOR_ID,
            workspace_id=workspace_id,
            agent_version_id=uninstalled_agent.agent_version_id,
        )

    with (
        pytest.raises(DBAPIError, match="skill installation exact binding invalid"),
        db_engine.begin() as connection,
    ):
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_agent_skill_installations "
                "(tenant_id, owner_user_id, workspace_id, agent_version_id, "
                "skill_definition_id, skill_version_id, skill_manifest_digest, "
                "installation_state, installed_by) VALUES "
                "(:tenant, :owner, :workspace, :agent_version, :definition, "
                ":version, :digest, 'installed', :owner)"
            ),
            {
                "tenant": tenant_id,
                "owner": ACTOR_ID,
                "workspace": other_workspace_id,
                "agent_version": installed_agent.agent_version_id,
                "definition": definition.skill_definition_id,
                "version": current_version.skill_version_id,
                "digest": current_version.canonical_digest(),
            },
        )

    unsupported_version = _skill_version(
        definition.skill_definition_id,
        version="3.0.0",
        instructions="This version intentionally targets a different sealed AgentVersion.",
        rollback_version_id=None,
        supported_agent_version_digests=["f" * 64],
    )
    with Session(db_engine, expire_on_commit=False) as session:
        SkillPersistenceService(session).seal_version(
            tenant_id=tenant_id,
            tenant_schema=schema_name,
            owner_user_id=ACTOR_ID,
            version=unsupported_version,
        )
        session.commit()
    with (
        pytest.raises(DBAPIError, match="skill AgentVersion digest is not supported"),
        db_engine.begin() as connection,
    ):
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_agent_skill_installations "
                "(tenant_id, owner_user_id, workspace_id, agent_version_id, "
                "skill_definition_id, skill_version_id, skill_manifest_digest, "
                "installation_state, installed_by) VALUES "
                "(:tenant, :owner, :workspace, :agent_version, :definition, "
                ":version, :digest, 'installed', :owner)"
            ),
            {
                "tenant": tenant_id,
                "owner": ACTOR_ID,
                "workspace": workspace_id,
                "agent_version": installed_agent.agent_version_id,
                "definition": definition.skill_definition_id,
                "version": unsupported_version.skill_version_id,
                "digest": unsupported_version.canonical_digest(),
            },
        )

    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM omnibase_meta.alembic_version")
            ).scalar_one()
            == "0014"
        )
