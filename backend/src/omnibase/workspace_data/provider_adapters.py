"""Typed P34.7 provider boundary and a disposable local reference adapter.

The local adapter is intentionally *not* a production object-store adapter. It
exists to exercise the same fail-closed contracts a production implementation
must satisfy: exact grant/effect binding, preflight quota enforcement, an
append-only effect journal, content verification, copy-on-publish, restore to
new logical identities, and committed-marker based visibility.

No public contract in this module accepts or returns a physical locator. The
filesystem root is injected by trusted server composition and is never placed
in a receipt, journal event, exception, or audit-shaped result.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal, Protocol, runtime_checkable
from uuid import UUID

from omnibase.workspace_data.service import canonical_digest, require_digest

_MARKER = ".omnibase-p34-7-disposable-provider"
_SCHEMA_VERSION = 1


class ProviderContractError(RuntimeError):
    """A stable, non-sensitive provider contract rejection."""


class ProviderReconciliationRequired(ProviderContractError):
    """A pending or unknown external effect must not be replayed."""


class ProviderAdmissionDenied(ProviderContractError):
    """The adapter does not satisfy the requested deployment Gate."""


class ProviderEffectKind(StrEnum):
    ARTIFACT_PUT = "artifact_put"
    DERIVED_BUILD = "derived_build"
    PUBLICATION_COPY = "publication_copy"
    SNAPSHOT_CAPTURE = "snapshot_capture"
    SNAPSHOT_RESTORE = "snapshot_restore"


class ProviderLane(StrEnum):
    WORKSPACE_PRIVATE = "workspace_private"
    WORKSPACE_DERIVED = "workspace_derived"
    CONTROLLED_SHARED = "controlled_shared"
    SNAPSHOT_PAYLOAD = "snapshot_payload"


_ACTION_BY_EFFECT: dict[ProviderEffectKind, str] = {
    ProviderEffectKind.ARTIFACT_PUT: "artifact.write",
    ProviderEffectKind.DERIVED_BUILD: "rag.derived.create",
    ProviderEffectKind.PUBLICATION_COPY: "workspace.data.publish",
    ProviderEffectKind.SNAPSHOT_CAPTURE: "workspace.snapshot.capture",
    ProviderEffectKind.SNAPSHOT_RESTORE: "workspace.snapshot.restore",
}


def _uuid_text(value: str, field: str) -> str:
    try:
        normalized = str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc
    if value != normalized:
        raise ValueError(f"{field} must use canonical UUID text")
    return normalized


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ProviderObjectRef:
    """Logical provider object identity; it contains no storage locator."""

    tenant_id: str
    workspace_id: str
    resource_id: str
    resource_version: int
    lane: ProviderLane
    content_digest: str
    size_bytes: int
    workspace_generation: int

    def __post_init__(self) -> None:
        _uuid_text(self.tenant_id, "tenant_id")
        _uuid_text(self.workspace_id, "workspace_id")
        _uuid_text(self.resource_id, "resource_id")
        if self.resource_version < 1:
            raise ValueError("resource_version must be at least one")
        if self.workspace_generation < 1:
            raise ValueError("workspace_generation must be at least one")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        require_digest(self.content_digest, "content_digest")

    def binding(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "resource_id": self.resource_id,
            "resource_version": self.resource_version,
            "lane": self.lane.value,
            "content_digest": self.content_digest,
            "size_bytes": self.size_bytes,
            "workspace_generation": self.workspace_generation,
        }


@dataclass(frozen=True, slots=True)
class ProviderGrantFacts:
    """Short-lived, non-delegable server-verified provider authorization."""

    tenant_id: str
    workspace_id: str
    operation_id: str
    grant_id: str
    grant_version: int
    actions: frozenset[str]
    max_bytes: int
    expires_at: datetime
    revoked: bool = False
    delegation_depth: int = 0

    def __post_init__(self) -> None:
        _uuid_text(self.tenant_id, "tenant_id")
        _uuid_text(self.workspace_id, "workspace_id")
        _uuid_text(self.operation_id, "operation_id")
        _uuid_text(self.grant_id, "grant_id")
        if self.grant_version < 1:
            raise ValueError("grant_version must be at least one")
        if self.max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        _aware_utc(self.expires_at, "expires_at")
        if self.delegation_depth != 0:
            raise ValueError("provider grants must be non-delegable")

    def digest(self) -> str:
        return canonical_digest(
            {
                "tenant_id": self.tenant_id,
                "workspace_id": self.workspace_id,
                "operation_id": self.operation_id,
                "grant_id": self.grant_id,
                "grant_version": self.grant_version,
                "actions": sorted(self.actions),
                "max_bytes": self.max_bytes,
                "expires_at": self.expires_at.astimezone(UTC).isoformat(),
                "revoked": self.revoked,
                "delegation_depth": self.delegation_depth,
            }
        )


@dataclass(frozen=True, slots=True)
class ProviderEffectPlan:
    """Exact effect binding submitted to a trusted provider adapter."""

    kind: ProviderEffectKind
    tenant_id: str
    workspace_id: str
    operation_id: str
    binding_digest: str
    grant: ProviderGrantFacts
    sources: tuple[ProviderObjectRef, ...]
    targets: tuple[ProviderObjectRef, ...]

    def __post_init__(self) -> None:
        _uuid_text(self.tenant_id, "tenant_id")
        _uuid_text(self.workspace_id, "workspace_id")
        _uuid_text(self.operation_id, "operation_id")
        require_digest(self.binding_digest, "binding_digest")
        self._validate_scope()
        self._validate_shape()

    @property
    def charged_bytes(self) -> int:
        return sum(target.size_bytes for target in self.targets)

    def digest(self) -> str:
        return canonical_digest(
            {
                "schema_version": _SCHEMA_VERSION,
                "kind": self.kind.value,
                "tenant_id": self.tenant_id,
                "workspace_id": self.workspace_id,
                "operation_id": self.operation_id,
                "binding_digest": self.binding_digest,
                "grant_digest": self.grant.digest(),
                "sources": [source.binding() for source in self.sources],
                "targets": [target.binding() for target in self.targets],
            }
        )

    def _validate_scope(self) -> None:
        if (
            self.grant.tenant_id != self.tenant_id
            or self.grant.workspace_id != self.workspace_id
            or self.grant.operation_id != self.operation_id
        ):
            raise ProviderContractError("provider grant scope binding changed")
        if self.grant.revoked:
            raise ProviderContractError("provider grant is revoked")
        if self.grant.expires_at.astimezone(UTC) <= datetime.now(UTC):
            raise ProviderContractError("provider grant is expired")
        if _ACTION_BY_EFFECT[self.kind] not in self.grant.actions:
            raise ProviderContractError("provider action is not granted")
        if self.charged_bytes > self.grant.max_bytes:
            raise ProviderContractError("provider byte quota exceeded")
        if any(item.tenant_id != self.tenant_id for item in (*self.sources, *self.targets)):
            raise ProviderContractError("provider object tenant binding changed")

    def _validate_shape(self) -> None:
        source_ids = [item.resource_id for item in self.sources]
        target_ids = [item.resource_id for item in self.targets]
        if len(target_ids) != len(set(target_ids)):
            raise ProviderContractError("provider target identities must be unique")
        if set(source_ids) & set(target_ids):
            raise ProviderContractError("provider effects require new target identities")
        if not self.targets:
            raise ProviderContractError("provider effect requires at least one target")

        validator = {
            ProviderEffectKind.ARTIFACT_PUT: self._validate_artifact_put,
            ProviderEffectKind.DERIVED_BUILD: self._validate_derived_build,
            ProviderEffectKind.PUBLICATION_COPY: self._validate_publication_copy,
            ProviderEffectKind.SNAPSHOT_CAPTURE: self._validate_snapshot_capture,
            ProviderEffectKind.SNAPSHOT_RESTORE: self._validate_snapshot_restore,
        }[self.kind]
        validator()

    def _validate_artifact_put(self) -> None:
        if self.sources or len(self.targets) != 1:
            raise ProviderContractError("artifact put shape is invalid")
        self._require_target_lanes({ProviderLane.WORKSPACE_PRIVATE})
        self._require_workspace(self.targets, self.workspace_id)

    def _validate_derived_build(self) -> None:
        if not self.sources or len(self.targets) != 1:
            raise ProviderContractError("derived build shape is invalid")
        self._require_target_lanes({ProviderLane.WORKSPACE_DERIVED})
        self._require_workspace((*self.sources, *self.targets), self.workspace_id)

    def _validate_publication_copy(self) -> None:
        if len(self.sources) != 1 or len(self.targets) != 1:
            raise ProviderContractError("publication copy shape is invalid")
        self._require_target_lanes({ProviderLane.CONTROLLED_SHARED})
        self._require_workspace(self.sources, self.workspace_id)
        self._require_copy_pairs()

    def _validate_snapshot_capture(self) -> None:
        if len(self.sources) != len(self.targets):
            raise ProviderContractError("snapshot capture shape is invalid")
        self._require_target_lanes({ProviderLane.SNAPSHOT_PAYLOAD})
        self._require_workspace((*self.sources, *self.targets), self.workspace_id)
        self._require_copy_pairs()

    def _validate_snapshot_restore(self) -> None:
        if len(self.sources) != len(self.targets):
            raise ProviderContractError("snapshot restore shape is invalid")
        if any(source.lane is not ProviderLane.SNAPSHOT_PAYLOAD for source in self.sources):
            raise ProviderContractError("restore sources must be sealed snapshot payloads")
        self._require_target_lanes({ProviderLane.WORKSPACE_PRIVATE, ProviderLane.WORKSPACE_DERIVED})
        self._require_workspace(self.sources, self.workspace_id)
        self._require_copy_pairs()
        target_workspaces = {target.workspace_id for target in self.targets}
        if len(target_workspaces) != 1 or self.workspace_id in target_workspaces:
            raise ProviderContractError("restore must create a new workspace identity")
        source_generation = max(source.workspace_generation for source in self.sources)
        if any(target.workspace_generation <= source_generation for target in self.targets):
            raise ProviderContractError("restore generation must advance")

    def _require_target_lanes(self, allowed: set[ProviderLane]) -> None:
        if any(target.lane not in allowed for target in self.targets):
            raise ProviderContractError("provider target lane is unavailable")

    @staticmethod
    def _require_workspace(items: Sequence[ProviderObjectRef], workspace_id: str) -> None:
        if any(item.workspace_id != workspace_id for item in items):
            raise ProviderContractError("provider object workspace binding changed")

    def _require_copy_pairs(self) -> None:
        for source, target in zip(self.sources, self.targets, strict=True):
            if (
                source.content_digest != target.content_digest
                or source.size_bytes != target.size_bytes
            ):
                raise ProviderContractError("provider copy content binding changed")


@dataclass(frozen=True, slots=True)
class ProviderReceipt:
    schema_version: int
    provider_kind: str
    effect_kind: ProviderEffectKind
    tenant_id: str
    workspace_id: str
    operation_id: str
    binding_digest: str
    plan_digest: str
    grant_digest: str
    targets_digest: str
    charged_bytes: int
    committed_at: str
    receipt_digest: str

    def unsigned(self) -> dict[str, object]:
        value = asdict(self)
        value.pop("receipt_digest")
        value["effect_kind"] = self.effect_kind.value
        return value

    def verify(self, plan: ProviderEffectPlan) -> None:
        require_digest(self.receipt_digest, "receipt_digest")
        expected = _receipt_for_plan(
            plan,
            provider_kind=self.provider_kind,
            committed_at=self.committed_at,
        )
        if self != expected:
            raise ProviderContractError("provider receipt binding changed")


@dataclass(frozen=True, slots=True)
class ProviderAdmissionReport:
    accepted: bool
    gate: Literal["staging", "production"]
    provider_kind: str
    reason_code: str
    supports_effect_journal: bool
    supports_atomic_visibility: bool
    supports_receipt_binding: bool
    supports_new_identity_restore: bool


@dataclass(frozen=True, slots=True)
class VerifiedNonDisposableAdmissionFacts:
    """Short-lived facts from a live, data-owner-approved admission transaction."""

    tenant_id: str
    workspace_id: str
    authorization_id: str
    data_owner_user_id: str
    target_fingerprint: str
    allowed_effects: frozenset[ProviderEffectKind]
    verified_at: datetime
    expires_at: datetime
    data_owner_approved: bool

    def __post_init__(self) -> None:
        _uuid_text(self.tenant_id, "tenant_id")
        _uuid_text(self.workspace_id, "workspace_id")
        _uuid_text(self.authorization_id, "authorization_id")
        _uuid_text(self.data_owner_user_id, "data_owner_user_id")
        require_digest(self.target_fingerprint, "target_fingerprint")
        verified = _aware_utc(self.verified_at, "verified_at")
        expires = _aware_utc(self.expires_at, "expires_at")
        if expires <= verified or expires - verified > timedelta(minutes=5):
            raise ValueError("non-disposable admission facts must be short-lived")


@dataclass(frozen=True, slots=True)
class NonDisposableAdmissionReport:
    admitted: bool
    status: Literal["admitted", "blocked/not_proven"]
    reason_code: str


def assess_non_disposable_target(
    facts: VerifiedNonDisposableAdmissionFacts | None,
    *,
    tenant_id: str,
    workspace_id: str,
    effect_kind: ProviderEffectKind,
    target_fingerprint: str,
    now: datetime | None = None,
) -> NonDisposableAdmissionReport:
    """Fail closed without exact, live, data-owner authorization facts."""

    require_digest(target_fingerprint, "target_fingerprint")
    if facts is None:
        return NonDisposableAdmissionReport(
            admitted=False,
            status="blocked/not_proven",
            reason_code="data_owner_authorization_missing",
        )
    current = (now or datetime.now(UTC)).astimezone(UTC)
    admitted = all(
        (
            facts.data_owner_approved,
            facts.tenant_id == tenant_id,
            facts.workspace_id == workspace_id,
            facts.target_fingerprint == target_fingerprint,
            effect_kind in facts.allowed_effects,
            facts.verified_at.astimezone(UTC) <= current,
            current < facts.expires_at.astimezone(UTC),
        )
    )
    return NonDisposableAdmissionReport(
        admitted=admitted,
        status="admitted" if admitted else "blocked/not_proven",
        reason_code="accepted" if admitted else "data_owner_authorization_rejected",
    )


@runtime_checkable
class WorkspaceDataProviderAdapter(Protocol):
    supports_workspace_data_provider: Literal[True]
    supports_effect_journal: Literal[True]
    supports_atomic_visibility: Literal[True]
    supports_receipt_binding: Literal[True]
    supports_new_identity_restore: Literal[True]
    production_ready: bool
    provider_kind: str

    def execute(
        self,
        plan: ProviderEffectPlan,
        *,
        payloads: Mapping[str, bytes] | None = None,
    ) -> ProviderReceipt: ...

    def read_committed(self, *, receipt: ProviderReceipt, target: ProviderObjectRef) -> bytes: ...


def assess_provider_adapter(
    adapter: object, *, require_production: bool
) -> ProviderAdmissionReport:
    """Admit only explicitly marked adapters; duck-typed partials fail closed."""

    gate: Literal["staging", "production"] = "production" if require_production else "staging"
    kind = getattr(adapter, "provider_kind", "unavailable")
    markers = {
        "supports_effect_journal": getattr(adapter, "supports_effect_journal", False) is True,
        "supports_atomic_visibility": getattr(adapter, "supports_atomic_visibility", False) is True,
        "supports_receipt_binding": getattr(adapter, "supports_receipt_binding", False) is True,
        "supports_new_identity_restore": getattr(adapter, "supports_new_identity_restore", False)
        is True,
    }
    accepted = (
        isinstance(adapter, WorkspaceDataProviderAdapter)
        and getattr(adapter, "supports_workspace_data_provider", False) is True
        and all(markers.values())
        and (not require_production or getattr(adapter, "production_ready", False) is True)
    )
    reason = (
        "accepted"
        if accepted
        else (
            "production_evidence_not_admitted"
            if require_production and all(markers.values())
            else "provider_contract_incomplete"
        )
    )
    return ProviderAdmissionReport(
        accepted=accepted,
        gate=gate,
        provider_kind=str(kind),
        reason_code=reason,
        **markers,
    )


class LocalContentAddressedProvider:
    """Disposable/staging reference adapter with committed-marker visibility."""

    supports_workspace_data_provider: ClassVar[Literal[True]] = True
    supports_effect_journal: ClassVar[Literal[True]] = True
    supports_atomic_visibility: ClassVar[Literal[True]] = True
    supports_receipt_binding: ClassVar[Literal[True]] = True
    supports_new_identity_restore: ClassVar[Literal[True]] = True
    production_ready: ClassVar[bool] = False
    provider_kind: ClassVar[str] = "local_content_addressed_reference"

    def __init__(self, root: Path) -> None:
        self._root = _validated_disposable_root(root)
        self._journal = self._root / "journal"
        self._objects = self._root / "objects"
        self._bindings = self._root / "bindings"
        self._staging = self._root / "staging"
        for directory in (self._journal, self._objects, self._bindings, self._staging):
            directory.mkdir(mode=0o700, exist_ok=True)

    @classmethod
    def initialize_disposable(cls, root: Path) -> LocalContentAddressedProvider:
        absolute = root.absolute()
        if absolute.exists() and any(absolute.iterdir()):
            raise ProviderAdmissionDenied("disposable provider root must be empty")
        absolute.mkdir(parents=True, exist_ok=True)
        if absolute.is_symlink() or absolute.resolve() != absolute:
            raise ProviderAdmissionDenied("disposable provider root must not be redirected")
        marker = absolute / _MARKER
        marker.write_text(
            json.dumps({"schema_version": _SCHEMA_VERSION, "disposable": True}) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return cls(absolute)

    def execute(
        self,
        plan: ProviderEffectPlan,
        *,
        payloads: Mapping[str, bytes] | None = None,
    ) -> ProviderReceipt:
        plan_digest = plan.digest()
        previous = self._events(plan.operation_id)
        if previous:
            latest = previous[-1]
            if latest.get("state") == "committed" and latest.get("plan_digest") == plan_digest:
                receipt = _receipt_from_json(latest["receipt"])
                receipt.verify(plan)
                return receipt
            raise ProviderReconciliationRequired("provider effect requires explicit reconciliation")

        self._append_event(
            plan.operation_id,
            {
                "schema_version": _SCHEMA_VERSION,
                "state": "pending",
                "effect_kind": plan.kind.value,
                "plan_digest": plan_digest,
                "binding_digest": plan.binding_digest,
                "grant_digest": plan.grant.digest(),
                "recorded_at": datetime.now(UTC).isoformat(),
            },
        )
        stage = self._staging / _opaque(plan.operation_id)
        stage.mkdir(mode=0o700, parents=False, exist_ok=False)
        try:
            materialized = self._materialize(plan, stage=stage, payloads=payloads or {})
            committed_at = datetime.now(UTC).isoformat()
            receipt = _receipt_for_plan(
                plan,
                provider_kind=self.provider_kind,
                committed_at=committed_at,
            )
            for target, staged in materialized:
                final = self._object_path(target)
                final.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if final.exists():
                    if _sha256_file(final) != target.content_digest:
                        raise ProviderContractError("provider target content conflict")
                    staged.unlink()
                else:
                    os.replace(staged, final)
                self._write_binding(target=target, operation_id=plan.operation_id, receipt=receipt)
            self._after_materialization_before_commit(plan=plan, receipt=receipt)
            self._append_event(
                plan.operation_id,
                {
                    "schema_version": _SCHEMA_VERSION,
                    "state": "committed",
                    "effect_kind": plan.kind.value,
                    "plan_digest": plan_digest,
                    "binding_digest": plan.binding_digest,
                    "grant_digest": plan.grant.digest(),
                    "receipt": _receipt_json(receipt),
                    "recorded_at": committed_at,
                },
            )
            return receipt
        except ProviderContractError:
            self._append_unknown(plan)
            raise
        except Exception as exc:
            self._append_unknown(plan)
            raise ProviderReconciliationRequired(
                "provider effect outcome requires reconciliation"
            ) from exc
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _after_materialization_before_commit(
        self, *, plan: ProviderEffectPlan, receipt: ProviderReceipt
    ) -> None:
        """Fault-injection seam used only by the disposable Gate tests."""

        del plan, receipt

    def read_committed(self, *, receipt: ProviderReceipt, target: ProviderObjectRef) -> bytes:
        events = self._events(receipt.operation_id)
        if not events or events[-1].get("state") != "committed":
            raise ProviderReconciliationRequired("provider object is not committed-visible")
        if _event_receipt_digest(events[-1]) != receipt.receipt_digest:
            raise ProviderContractError("provider committed marker binding changed")
        binding_path = self._binding_path(target)
        if not binding_path.is_file():
            raise ProviderContractError("provider binding is unavailable")
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        if (
            binding.get("operation_id") != receipt.operation_id
            or binding.get("receipt_digest") != receipt.receipt_digest
            or binding.get("target") != target.binding()
        ):
            raise ProviderContractError("provider object receipt binding changed")
        path = self._object_path(target)
        if not path.is_file():
            raise ProviderContractError("provider object is unavailable")
        content = path.read_bytes()
        if len(content) != target.size_bytes or hashlib.sha256(content).hexdigest() != (
            target.content_digest
        ):
            raise ProviderContractError("provider object integrity check failed")
        return content

    def _materialize(
        self,
        plan: ProviderEffectPlan,
        *,
        stage: Path,
        payloads: Mapping[str, bytes],
    ) -> list[tuple[ProviderObjectRef, Path]]:
        results: list[tuple[ProviderObjectRef, Path]] = []
        if plan.kind in {ProviderEffectKind.ARTIFACT_PUT, ProviderEffectKind.DERIVED_BUILD}:
            target = plan.targets[0]
            content = payloads.get(target.resource_id)
            if content is None:
                raise ProviderContractError("provider payload is unavailable")
            results.append((target, self._stage_bytes(stage, target, content)))
            return results

        if payloads:
            raise ProviderContractError("provider copy effects do not accept caller payloads")
        for source, target in zip(plan.sources, plan.targets, strict=True):
            content = self._read_source(source)
            results.append((target, self._stage_bytes(stage, target, content)))
        return results

    def _read_source(self, source: ProviderObjectRef) -> bytes:
        binding_path = self._binding_path(source)
        if not binding_path.is_file():
            raise ProviderContractError("provider source is unavailable")
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        operation_id = binding.get("operation_id")
        receipt_digest = binding.get("receipt_digest")
        if not isinstance(operation_id, str) or not isinstance(receipt_digest, str):
            raise ProviderContractError("provider source binding is invalid")
        events = self._events(operation_id)
        if (
            not events
            or events[-1].get("state") != "committed"
            or _event_receipt_digest(events[-1]) != receipt_digest
        ):
            raise ProviderReconciliationRequired("provider source is not committed-visible")
        path = self._object_path(source)
        content = path.read_bytes()
        if len(content) != source.size_bytes or hashlib.sha256(content).hexdigest() != (
            source.content_digest
        ):
            raise ProviderContractError("provider source integrity check failed")
        return content

    def _stage_bytes(self, stage: Path, target: ProviderObjectRef, content: bytes) -> Path:
        if len(content) != target.size_bytes or hashlib.sha256(content).hexdigest() != (
            target.content_digest
        ):
            raise ProviderContractError("provider payload digest or size changed")
        path = stage / f"{_opaque(target.resource_id)}.blob"
        path.write_bytes(content)
        return path

    def _append_unknown(self, plan: ProviderEffectPlan) -> None:
        with suppress(OSError):
            self._append_event(
                plan.operation_id,
                {
                    "schema_version": _SCHEMA_VERSION,
                    "state": "unknown",
                    "effect_kind": plan.kind.value,
                    "plan_digest": plan.digest(),
                    "binding_digest": plan.binding_digest,
                    "grant_digest": plan.grant.digest(),
                    "reason_code": "provider.outcome_unknown",
                    "recorded_at": datetime.now(UTC).isoformat(),
                },
            )

    def _events(self, operation_id: str) -> list[dict[str, object]]:
        directory = self._journal / _opaque(operation_id)
        if not directory.exists():
            return []
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]

    def _append_event(self, operation_id: str, event: dict[str, object]) -> None:
        directory = self._journal / _opaque(operation_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        sequence = len(list(directory.glob("*.json"))) + 1
        path = directory / f"{sequence:04d}.json"
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(event, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")

    def _object_path(self, target: ProviderObjectRef) -> Path:
        return (
            self._objects
            / target.lane.value
            / _opaque(target.tenant_id)
            / _opaque(target.workspace_id)
            / _opaque(target.resource_id)
            / f"v{target.resource_version}-{target.content_digest}.blob"
        )

    def _binding_path(self, target: ProviderObjectRef) -> Path:
        return (
            self._bindings
            / target.lane.value
            / _opaque(target.tenant_id)
            / _opaque(target.workspace_id)
            / _opaque(target.resource_id)
            / f"v{target.resource_version}.json"
        )

    def _write_binding(
        self,
        *,
        target: ProviderObjectRef,
        operation_id: str,
        receipt: ProviderReceipt,
    ) -> None:
        path = self._binding_path(target)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        value = {
            "schema_version": _SCHEMA_VERSION,
            "operation_id": operation_id,
            "receipt_digest": receipt.receipt_digest,
            "target": target.binding(),
        }
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        if path.exists():
            if path.read_text(encoding="utf-8") != encoded:
                raise ProviderContractError("provider binding conflict")
            return
        temp = path.with_suffix(".pending")
        temp.write_text(encoded, encoding="utf-8", newline="\n")
        os.replace(temp, path)


def _validated_disposable_root(root: Path) -> Path:
    absolute = root.absolute()
    if not absolute.is_dir() or absolute.is_symlink() or absolute.resolve() != absolute:
        raise ProviderAdmissionDenied("disposable provider root is unavailable")
    marker = absolute / _MARKER
    if not marker.is_file():
        raise ProviderAdmissionDenied("disposable provider marker is missing")
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderAdmissionDenied("disposable provider marker is invalid") from exc
    if value != {"schema_version": _SCHEMA_VERSION, "disposable": True}:
        raise ProviderAdmissionDenied("disposable provider marker is invalid")
    return absolute


def _opaque(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _receipt_for_plan(
    plan: ProviderEffectPlan, *, provider_kind: str, committed_at: str
) -> ProviderReceipt:
    unsigned: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "provider_kind": provider_kind,
        "effect_kind": plan.kind.value,
        "tenant_id": plan.tenant_id,
        "workspace_id": plan.workspace_id,
        "operation_id": plan.operation_id,
        "binding_digest": plan.binding_digest,
        "plan_digest": plan.digest(),
        "grant_digest": plan.grant.digest(),
        "targets_digest": canonical_digest([target.binding() for target in plan.targets]),
        "charged_bytes": plan.charged_bytes,
        "committed_at": committed_at,
    }
    return ProviderReceipt(
        schema_version=_SCHEMA_VERSION,
        provider_kind=provider_kind,
        effect_kind=plan.kind,
        tenant_id=plan.tenant_id,
        workspace_id=plan.workspace_id,
        operation_id=plan.operation_id,
        binding_digest=plan.binding_digest,
        plan_digest=plan.digest(),
        grant_digest=plan.grant.digest(),
        targets_digest=canonical_digest([target.binding() for target in plan.targets]),
        charged_bytes=plan.charged_bytes,
        committed_at=committed_at,
        receipt_digest=canonical_digest(unsigned),
    )


def _receipt_json(receipt: ProviderReceipt) -> dict[str, object]:
    value = asdict(receipt)
    value["effect_kind"] = receipt.effect_kind.value
    return value


def _receipt_from_json(value: object) -> ProviderReceipt:
    if not isinstance(value, dict):
        raise ProviderContractError("provider receipt is invalid")
    try:
        return ProviderReceipt(
            schema_version=int(value["schema_version"]),
            provider_kind=str(value["provider_kind"]),
            effect_kind=ProviderEffectKind(str(value["effect_kind"])),
            tenant_id=str(value["tenant_id"]),
            workspace_id=str(value["workspace_id"]),
            operation_id=str(value["operation_id"]),
            binding_digest=str(value["binding_digest"]),
            plan_digest=str(value["plan_digest"]),
            grant_digest=str(value["grant_digest"]),
            targets_digest=str(value["targets_digest"]),
            charged_bytes=int(value["charged_bytes"]),
            committed_at=str(value["committed_at"]),
            receipt_digest=str(value["receipt_digest"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderContractError("provider receipt is invalid") from exc


def _event_receipt_digest(event: Mapping[str, object]) -> object:
    receipt = event.get("receipt")
    return receipt.get("receipt_digest") if isinstance(receipt, dict) else None


__all__ = [
    "LocalContentAddressedProvider",
    "NonDisposableAdmissionReport",
    "ProviderAdmissionDenied",
    "ProviderAdmissionReport",
    "ProviderContractError",
    "ProviderEffectKind",
    "ProviderEffectPlan",
    "ProviderGrantFacts",
    "ProviderLane",
    "ProviderObjectRef",
    "ProviderReceipt",
    "ProviderReconciliationRequired",
    "VerifiedNonDisposableAdmissionFacts",
    "WorkspaceDataProviderAdapter",
    "assess_non_disposable_target",
    "assess_provider_adapter",
]
