"""In-container fixture operations for the disposable P5.9P acceptance run.

This module is never copied into a production image.  The acceptance Compose
overlay bind-mounts this one file into a disposable target so that the journey
can install one sealed first-party instruction Skill, publish one encrypted
workspace Memory, activate the already-reviewed personal canary, and inspect
ledger identities without exposing PostgreSQL on the host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from omnibase.agent_memory.compiler import personal_default_memory_policy
from omnibase.agent_memory.crypto import MemoryContentCipher
from omnibase.agent_memory.service import (
    CandidateConfirmation,
    CandidateDraft,
    candidate_confirmation_sha256,
    confirm_candidate,
    create_candidate,
)
from omnibase.agent_skills.service import SkillPersistenceService
from omnibase.capabilities.service import TrustedIssuerContext, create_grant
from omnibase.control_plane.service import (
    create_approval,
    create_operation,
    decide_approval,
)
from omnibase.core.config import get_settings
from omnibase.production.personal_runtime_activation import (
    activate_personal_runtime_canary,
    kill_personal_runtime_canary,
    load_personal_runtime_canary_config,
)
from omnibase.production.phase5_skill_contract import SkillDefinition, SkillVersion

_CONFIG_PATH = Path("/run/omnibase-personal/canary.json")
_STATE_DIR = Path("/run/omnibase-personal/state")
_READINESS_ROOT = Path("/run/omnibase-personal/readiness-root")


class AcceptanceFixtureError(RuntimeError):
    """A disposable acceptance fixture could not establish exact state."""


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _emit(value: object) -> None:
    print(_canonical(value))


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL", "")
    if not value:
        raise AcceptanceFixtureError("DATABASE_URL is required")
    return value


def _engine():  # type: ignore[no-untyped-def]
    return create_engine(_database_url(), pool_pre_ping=True)


def _tenant_schema(connection, tenant_id: str) -> str:  # type: ignore[no-untyped-def]
    value = connection.execute(
        text(
            "SELECT schema_name FROM omnibase_meta.tenants "
            "WHERE id = :tenant AND is_active IS TRUE"
        ),
        {"tenant": tenant_id},
    ).scalar_one_or_none()
    if not isinstance(value, str) or not value.startswith("tenant_"):
        raise AcceptanceFixtureError("active tenant schema is unavailable")
    return value


def _set_tenant_search_path(connection, schema_name: str) -> None:  # type: ignore[no-untyped-def]
    if (
        not schema_name.startswith("tenant_")
        or not schema_name.replace("_", "").isalnum()
    ):
        raise AcceptanceFixtureError("tenant schema name is invalid")
    connection.execute(
        text(f'SET LOCAL search_path TO "{schema_name}", omnibase_meta, public')  # noqa: S608
    )


def _skill_definition() -> SkillDefinition:
    return SkillDefinition.from_mapping(
        {
            "skill_definition_id": str(uuid.uuid4()),
            "stable_logical_key": f"omnibase.p5-9p-{uuid.uuid4().hex[:8]}",
            "display_name": "P5.9P Personal Acceptance",
            "description": "Sealed first-party instruction-only acceptance Skill",
            "definition_state": "active",
            "allowed_installation_scopes": ["workspace"],
            "first_party": True,
        }
    )


def _skill_version(definition_id: str, agent_version_digest: str) -> SkillVersion:
    instructions = (
        "P5_SKILL_MARKER: keep the answer bounded, direct, and free of tool execution."
    )
    return SkillVersion.from_mapping(
        {
            "skill_version_id": str(uuid.uuid4()),
            "skill_definition_id": definition_id,
            "version": "1.0.0",
            "version_state": "tested",
            "kind": "instruction",
            "instructions": instructions,
            "instructions_digest": hashlib.sha256(instructions.encode()).hexdigest(),
            "input_schema": {"type": "object", "additionalProperties": False},
            "output_schema": {"type": "object", "additionalProperties": False},
            "required_tool_ids": [],
            "capability_requirements": [],
            "supported_agent_version_digests": [agent_version_digest],
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
                    "command_id": "p5-9p-disposable-acceptance",
                    "profile": "pytest",
                    "arguments": ["p5-9p-personal-acceptance"],
                    "network_allowed": False,
                }
            ],
            "rollback_version_id": None,
        }
    )


def install_skill(args: argparse.Namespace) -> dict[str, object]:
    engine = _engine()
    with engine.connect() as connection:
        schema_name = _tenant_schema(connection, args.tenant_id)
        digest = connection.execute(
            text(
                "SELECT manifest_digest FROM omnibase_meta.agent_versions "
                "WHERE tenant_id = :tenant AND id = :version AND version_state = 'sealed'"
            ),
            {"tenant": args.tenant_id, "version": args.agent_version_id},
        ).scalar_one_or_none()
    if not isinstance(digest, str):
        raise AcceptanceFixtureError("sealed AgentVersion is unavailable")
    definition = _skill_definition()
    version = _skill_version(definition.skill_definition_id, digest)
    with Session(engine, expire_on_commit=False) as session:
        service = SkillPersistenceService(session)
        service.register_definition(
            tenant_id=args.tenant_id,
            tenant_schema=schema_name,
            owner_user_id=args.owner_user_id,
            definition=definition,
        )
        service.seal_version(
            tenant_id=args.tenant_id,
            tenant_schema=schema_name,
            owner_user_id=args.owner_user_id,
            version=version,
        )
        installation = service.install(
            tenant_id=args.tenant_id,
            tenant_schema=schema_name,
            owner_user_id=args.owner_user_id,
            workspace_id=args.workspace_id,
            agent_version_id=args.agent_version_id,
            skill_version_id=version.skill_version_id,
        )
        session.commit()
        return {
            "installation_id": installation.id,
            "skill_definition_id": definition.skill_definition_id,
            "skill_version_id": version.skill_version_id,
        }


def _memory_source(connection, args: argparse.Namespace) -> dict[str, str]:  # type: ignore[no-untyped-def]
    row = (
        connection.execute(
            text(
                "SELECT task.id, task.agent_definition_id, task.agent_version_id, "
                "capsule.id AS capsule_id, capsule.invocation_id, capsule.memory_policy_id "
                "FROM omnibase_meta.agent_tasks task "
                "JOIN context_capsules capsule ON capsule.task_id = task.id "
                "AND capsule.tenant_id = task.tenant_id "
                "WHERE task.id = :task AND task.tenant_id = :tenant "
                "AND task.workspace_id = :workspace AND task.actor_user_id = :owner "
                "AND task.agent_version_id = :version AND task.state = 'succeeded' "
                "ORDER BY capsule.created_at DESC LIMIT 1"
            ),
            {
                "task": args.source_task_id,
                "tenant": args.tenant_id,
                "workspace": args.workspace_id,
                "owner": args.owner_user_id,
                "version": args.agent_version_id,
            },
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AcceptanceFixtureError("succeeded source Task/Capsule is unavailable")
    return {key: str(row[key]) for key in row.keys()}


def publish_memory(args: argparse.Namespace) -> dict[str, object]:
    engine = _engine()
    settings = get_settings()
    cipher = MemoryContentCipher.from_settings(settings)
    plaintext = "P5_MEMORY_MARKER: remember the Owner prefers concise personal answers."
    content_sha256 = hashlib.sha256(plaintext.encode()).hexdigest()
    policy = personal_default_memory_policy()
    evidence_id = str(uuid.uuid4())
    with Session(engine, expire_on_commit=False) as session:
        connection = session.connection()
        schema_name = _tenant_schema(connection, args.tenant_id)
        _set_tenant_search_path(connection, schema_name)
        source = _memory_source(connection, args)
        if source["memory_policy_id"] != policy.memory_policy_id:
            raise AcceptanceFixtureError("source Capsule Memory policy drifted")
        aad = MemoryContentCipher.aad(
            tenant_id=args.tenant_id,
            owner_user_id=args.owner_user_id,
            workspace_id=args.workspace_id,
            agent_version_id=args.agent_version_id,
            task_id=args.source_task_id,
            invocation_id=source["invocation_id"],
            memory_policy_id=source["memory_policy_id"],
            source_resource_id=args.source_task_id,
            source_resource_version=1,
            content_sha256=content_sha256,
            key_version=1,
        )
        encrypted = cipher.encrypt(plaintext, aad=aad)
        create_request_sha256 = hashlib.sha256(
            _canonical(
                {
                    "content_sha256": content_sha256,
                    "source_task_id": args.source_task_id,
                    "workspace_id": args.workspace_id,
                }
            ).encode()
        ).hexdigest()
        create_record = create_operation(
            session,
            tenant_id=args.tenant_id,
            kind="memory.candidate.create",
            risk_level="R1",
            actor_type="agent",
            actor_id=source["agent_definition_id"],
            workspace_id=args.workspace_id,
            resource_id=args.source_task_id,
            resource_version=1,
            request_hash=create_request_sha256,
        )
        candidate = create_candidate(
            session,
            CandidateDraft(
                tenant_id=args.tenant_id,
                owner_user_id=args.owner_user_id,
                workspace_id=args.workspace_id,
                agent_version_id=args.agent_version_id,
                task_id=args.source_task_id,
                invocation_id=source["invocation_id"],
                source_capsule_id=source["capsule_id"],
                memory_policy_id=source["memory_policy_id"],
                requested_scope="workspace_private",
                sensitivity="personal",
                content_ciphertext=encrypted.ciphertext,
                content_nonce=encrypted.nonce,
                content_key_version=1,
                content_sha256=content_sha256,
                source_resource_id=args.source_task_id,
                source_resource_version=1,
                evidence_reference_ids=(evidence_id,),
                confidence_millis=900,
                retention_days=30,
                requires_user_confirmation=False,
                operation_id=create_record.id,
                operation_expected_version=1,
                request_sha256=create_request_sha256,
                request_id=f"p59-memory-create-{uuid.uuid4()}",
            ),
        )
        now = datetime.now(UTC)
        grant = create_grant(
            session,
            tenant_id=args.tenant_id,
            workspace_id=args.workspace_id,
            runtime_instance_id=str(uuid.uuid4()),
            issuer_context=TrustedIssuerContext(
                tenant_id=args.tenant_id,
                system_actor_id=str(uuid.uuid4()),
                originating_user_id=args.owner_user_id,
            ),
            actions={"data.rows.read"},
            resource_ids={args.source_task_id},
            not_before=now - timedelta(seconds=5),
            expires_at=now + timedelta(minutes=5),
            max_calls=10,
            max_bytes=10_000,
            max_cost_units=10,
            delegation_depth_limit=0,
            constraints={"timeout_ms": 1500},
        )
        confirmation_sha256 = candidate_confirmation_sha256(candidate)
        acceptance_record = create_operation(
            session,
            tenant_id=args.tenant_id,
            kind="memory.candidate.accept",
            risk_level="R2",
            actor_type="agent",
            actor_id=source["agent_definition_id"],
            workspace_id=args.workspace_id,
            resource_id=args.source_task_id,
            resource_version=1,
            request_hash=confirmation_sha256,
        )
        approval = create_approval(
            session,
            tenant_id=args.tenant_id,
            requester_type="agent",
            requester_id=source["agent_definition_id"],
            action="memory.candidate.accept",
            risk_level="R2",
            request_hash=confirmation_sha256,
            expires_at=now + timedelta(minutes=5),
            grant_id=grant.id,
            workspace_id=args.workspace_id,
            resource_id=args.source_task_id,
            resource_version=1,
            operation_id=acceptance_record.id,
        )
        decided = decide_approval(
            session,
            tenant_id=args.tenant_id,
            approval_id=approval.id,
            expected_version=1,
            decided_by_actor_type="user",
            decided_by_actor_id=args.owner_user_id,
            decided_by_actor_role="tenant_admin",
            decision="approved",
            decision_reason="personal Owner accepted exact P5.9P Memory Candidate",
            request_hash=confirmation_sha256,
            resource_version=1,
        )
        memory, _version = confirm_candidate(
            session,
            CandidateConfirmation(
                tenant_id=args.tenant_id,
                candidate_id=candidate.id,
                owner_user_id=args.owner_user_id,
                operation_id=acceptance_record.id,
                operation_expected_version=1,
                approval_id=approval.id,
                approval_expected_version=decided.version,
                grant_id=grant.id,
                token_count=8,
                request_id=f"p59-memory-accept-{uuid.uuid4()}",
            ),
        )
        session.commit()
    return {
        "candidate_id": candidate.id,
        "content_sha256": content_sha256,
        "memory_id": memory.id,
    }


def activate() -> dict[str, object]:
    config = load_personal_runtime_canary_config(
        _CONFIG_PATH,
        repo_root=_READINESS_ROOT,
        verify_owner_readiness=True,
    )
    plan = config.activation_plan()
    event = activate_personal_runtime_canary(
        config,
        state_dir=_STATE_DIR,
        confirmed_plan_sha256=plan.canonical_digest(),
    )
    return {
        "canary_id": config.canary_id,
        "event_sha256": event.last_event_sha256,
        "plan_sha256": plan.canonical_digest(),
        "state": event.state.value,
    }


def kill() -> dict[str, object]:
    config = load_personal_runtime_canary_config(
        _CONFIG_PATH,
        repo_root=_READINESS_ROOT,
        verify_owner_readiness=True,
    )
    marker = kill_personal_runtime_canary(
        state_dir=_STATE_DIR,
        canary_id=config.canary_id,
        reason_code="p5_9p_acceptance_kill_switch",
    )
    return {
        "canary_id": config.canary_id,
        "kill_sha256": marker.last_event_sha256,
        "state": marker.state.value,
    }


def provider_stats() -> dict[str, object]:
    with urllib.request.urlopen(
        "http://fake-provider:8080/stats", timeout=3
    ) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise AcceptanceFixtureError("fake Provider stats are invalid")
    return {"provider_stats": payload}


def inspect_task(args: argparse.Namespace) -> dict[str, object]:
    engine = _engine()
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT task.id AS task_id, task.state AS task_state, "
                    "attempt.id AS attempt_id, attempt.state AS attempt_state, "
                    "task_lease.id AS task_lease_id, task_lease.state AS task_lease_state, "
                    "task_lease.task_fencing_token, effect.id AS effect_id, "
                    "effect.operation_id, effect.state AS effect_state, run.id AS agent_run_id, "
                    "run.workspace_run_id, run.runtime_instance_id, run.workload_identity_digest, "
                    "workspace_run.observed_state AS workspace_run_state, "
                    "run_lease.id AS run_lease_id, run_lease.fencing_token AS run_fencing_token "
                    "FROM omnibase_meta.agent_tasks task "
                    "JOIN omnibase_meta.agent_attempts attempt ON attempt.task_id = task.id "
                    "AND attempt.tenant_id = task.tenant_id "
                    "LEFT JOIN omnibase_meta.agent_task_leases task_lease "
                    "ON task_lease.attempt_id = attempt.id AND task_lease.tenant_id = task.tenant_id "
                    "JOIN omnibase_meta.agent_task_effects effect ON effect.attempt_id = attempt.id "
                    "AND effect.tenant_id = task.tenant_id "
                    "JOIN omnibase_meta.agent_runs run ON run.id = attempt.agent_run_id "
                    "AND run.tenant_id = task.tenant_id "
                    "JOIN omnibase_meta.workspace_runs workspace_run ON workspace_run.id = run.workspace_run_id "
                    "AND workspace_run.tenant_id = task.tenant_id "
                    "LEFT JOIN omnibase_meta.run_leases run_lease "
                    "ON run_lease.id = task_lease.run_lease_id "
                    "AND run_lease.tenant_id = task.tenant_id "
                    "WHERE task.id = :task AND task.tenant_id = :tenant "
                    "ORDER BY attempt.attempt_number DESC, task_lease.created_at DESC, "
                    "run_lease.created_at DESC LIMIT 1"
                ),
                {"task": args.task_id, "tenant": args.tenant_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise AcceptanceFixtureError("Task identity is unavailable")
        reconciliation_count = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.agent_reconciliation_cases "
                    "WHERE tenant_id = :tenant AND task_id = :task AND state = 'open'"
                ),
                {"tenant": args.tenant_id, "task": args.task_id},
            ).scalar_one()
        )
    payload = {
        key: (None if value is None else str(value)) for key, value in row.items()
    }
    payload["open_reconciliation_count"] = reconciliation_count
    return payload


def inspect_restored(args: argparse.Namespace) -> dict[str, object]:
    engine = _engine()
    with engine.connect() as connection:
        schema_name = _tenant_schema(connection, args.tenant_id)
        _set_tenant_search_path(connection, schema_name)
        skill_present = bool(
            connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM workspace_agent_skill_installations "
                    "WHERE tenant_id = :tenant AND workspace_id = :workspace "
                    "AND agent_version_id = :version AND skill_version_id = :skill "
                    "AND installation_state = 'installed')"
                ),
                {
                    "tenant": args.tenant_id,
                    "workspace": args.workspace_id,
                    "version": args.agent_version_id,
                    "skill": args.skill_version_id,
                },
            ).scalar_one()
        )
        memory_present = bool(
            connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM memories memory "
                    "JOIN memory_versions version ON version.tenant_id = memory.tenant_id "
                    "AND version.memory_id = memory.id AND version.version = memory.current_version "
                    "WHERE memory.tenant_id = :tenant AND memory.id = :memory "
                    "AND memory.owner_user_id = :owner AND memory.workspace_id = :workspace "
                    "AND memory.lifecycle_state = 'active')"
                ),
                {
                    "tenant": args.tenant_id,
                    "memory": args.memory_id,
                    "owner": args.owner_user_id,
                    "workspace": args.workspace_id,
                },
            ).scalar_one()
        )
    return {"memory_present": memory_present, "skill_present": skill_present}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("activate")
    commands.add_parser("kill")
    commands.add_parser("provider-stats")
    skill = commands.add_parser("install-skill")
    memory = commands.add_parser("publish-memory")
    inspect = commands.add_parser("inspect-task")
    restored = commands.add_parser("inspect-restored")
    for command in (skill, memory):
        command.add_argument("--tenant-id", required=True)
        command.add_argument("--owner-user-id", required=True)
        command.add_argument("--workspace-id", required=True)
        command.add_argument("--agent-version-id", required=True)
    memory.add_argument("--source-task-id", required=True)
    inspect.add_argument("--tenant-id", required=True)
    inspect.add_argument("--task-id", required=True)
    restored.add_argument("--tenant-id", required=True)
    restored.add_argument("--owner-user-id", required=True)
    restored.add_argument("--workspace-id", required=True)
    restored.add_argument("--agent-version-id", required=True)
    restored.add_argument("--memory-id", required=True)
    restored.add_argument("--skill-version-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        result = {
            "activate": lambda _args: activate(),
            "kill": lambda _args: kill(),
            "provider-stats": lambda _args: provider_stats(),
            "install-skill": install_skill,
            "publish-memory": publish_memory,
            "inspect-task": inspect_task,
            "inspect-restored": inspect_restored,
        }[args.command](args)
        _emit({"ok": True, **result})
        return 0
    except (AcceptanceFixtureError, OSError, RuntimeError, ValueError) as exc:
        _emit({"error": str(exc), "ok": False})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
