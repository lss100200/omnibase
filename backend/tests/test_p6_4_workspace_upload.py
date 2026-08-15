from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

import omnibase.documents.service as document_service
import omnibase.workers.tasks as worker_tasks
from omnibase.agent_alpha.adapters import RagKnowledgeRetriever
from omnibase.control_plane.models import ResourceRecord
from omnibase.core.config import Settings
from omnibase.db.tenant import Document
from omnibase.documents.service import DocumentError, StorageError, upload_document
from omnibase.rag.ingest import IngestResult
from omnibase.workspaces.models import ResourceScopeBinding


def _settings() -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            max_upload_size_bytes=1024 * 1024,
            max_upload_size_mb=1,
            allowed_mime_types=("text/plain",),
            minio_bucket="fixture",
        ),
    )


def _scalar(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _upload_session() -> MagicMock:
    session = MagicMock()
    session.refresh.side_effect = lambda _value: None
    session.commit.return_value = None
    return session


def _patch_upload_infrastructure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session: MagicMock,
    client: MagicMock,
    enqueue: bool = True,
) -> None:
    monkeypatch.setattr(document_service, "get_minio_client", lambda _settings: client)
    monkeypatch.setattr(document_service, "get_session_factory", lambda _settings: lambda: session)
    monkeypatch.setattr(document_service, "enqueue_ingest", lambda **_kwargs: enqueue)


def _workspace_upload(
    monkeypatch: pytest.MonkeyPatch, *, enqueue: bool = True
) -> tuple[
    MagicMock,
    MagicMock,
    MagicMock,
]:
    session = _upload_session()
    client = MagicMock()
    register = MagicMock()
    _patch_upload_infrastructure(
        monkeypatch,
        session=session,
        client=client,
        enqueue=enqueue,
    )
    monkeypatch.setattr(document_service, "_require_workspace_upload_access", MagicMock())
    monkeypatch.setattr(
        document_service,
        "_require_workspace_upload_access_in_session",
        MagicMock(),
    )
    monkeypatch.setattr(document_service, "register_resource", register)
    return session, client, register


def test_workspace_upload_denial_happens_before_object_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_factory = MagicMock()
    monkeypatch.setattr(document_service, "get_minio_client", client_factory)
    monkeypatch.setattr(
        document_service,
        "_require_workspace_upload_access",
        MagicMock(side_effect=DocumentError("Workspace upload is unavailable")),
    )

    with pytest.raises(DocumentError, match="Workspace upload is unavailable"):
        upload_document(
            schema_name="tenant_aaaaaaaaaaaa",
            filename="facts.txt",
            content_type="text/plain",
            data=b"bounded fixture",
            settings=_settings(),
            extract_metadata=False,
            tenant_id="00000000-0000-0000-0000-000000000001",
            actor_user_id="00000000-0000-0000-0000-000000000002",
            workspace_id="00000000-0000-0000-0000-000000000003",
        )

    client_factory.assert_not_called()


def test_workspace_upload_registers_exact_workspace_private_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, client, register = _workspace_upload(monkeypatch)

    result = upload_document(
        schema_name="tenant_aaaaaaaaaaaa",
        filename="facts.txt",
        content_type="text/plain",
        data=b"bounded fixture",
        settings=_settings(),
        extract_metadata=False,
        tenant_id="00000000-0000-0000-0000-000000000001",
        actor_user_id="00000000-0000-0000-0000-000000000002",
        workspace_id="00000000-0000-0000-0000-000000000003",
    )

    client.put_object.assert_called_once()
    register.assert_called_once()
    kwargs = register.call_args.kwargs
    assert kwargs["resource_id"] == result.document.id
    assert kwargs["tenant_id"] == "00000000-0000-0000-0000-000000000001"
    assert kwargs["owner_type"] == "workspace"
    assert kwargs["owner_id"] == "00000000-0000-0000-0000-000000000003"
    assert kwargs["kind"] == "document"
    assert kwargs["policy_class"] == "workspace_private"
    assert kwargs["state"] == "provisioning"
    assert set(kwargs["metadata"]) == {"content_sha256", "media_type", "size_bytes"}
    bindings = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], ResourceScopeBinding)
    ]
    assert len(bindings) == 1
    assert bindings[0].resource_id == result.document.id
    assert bindings[0].tenant_id == "00000000-0000-0000-0000-000000000001"
    assert bindings[0].workspace_id == "00000000-0000-0000-0000-000000000003"
    assert bindings[0].scope_class == "workspace_private"


def test_legacy_tenant_upload_does_not_create_workspace_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _upload_session()
    client = MagicMock()
    register = MagicMock()
    _patch_upload_infrastructure(monkeypatch, session=session, client=client)
    monkeypatch.setattr(document_service, "register_resource", register)

    upload_document(
        schema_name="tenant_aaaaaaaaaaaa",
        filename="legacy.txt",
        content_type="text/plain",
        data=b"legacy tenant document",
        settings=_settings(),
        extract_metadata=False,
    )

    register.assert_not_called()
    assert not any(
        isinstance(call.args[0], ResourceScopeBinding) for call in session.add.call_args_list
    )


def test_transaction_revalidation_failure_compensates_uploaded_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _upload_session()
    client = MagicMock()
    register = MagicMock()
    _patch_upload_infrastructure(monkeypatch, session=session, client=client)
    monkeypatch.setattr(document_service, "_require_workspace_upload_access", MagicMock())
    monkeypatch.setattr(
        document_service,
        "_require_workspace_upload_access_in_session",
        MagicMock(side_effect=DocumentError("Workspace upload is unavailable")),
    )
    monkeypatch.setattr(document_service, "register_resource", register)

    with pytest.raises(DocumentError, match="Workspace upload is unavailable"):
        upload_document(
            schema_name="tenant_aaaaaaaaaaaa",
            filename="race.txt",
            content_type="text/plain",
            data=b"membership changed",
            settings=_settings(),
            extract_metadata=False,
            tenant_id="00000000-0000-0000-0000-000000000001",
            actor_user_id="00000000-0000-0000-0000-000000000002",
            workspace_id="00000000-0000-0000-0000-000000000003",
        )

    client.put_object.assert_called_once()
    client.remove_object.assert_called_once()
    register.assert_not_called()


def test_initial_metadata_commit_failure_compensates_uploaded_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, client, _register = _workspace_upload(monkeypatch)
    session.commit.side_effect = RuntimeError("database unavailable")

    with pytest.raises(DocumentError, match="Failed to persist upload metadata"):
        upload_document(
            schema_name="tenant_aaaaaaaaaaaa",
            filename="commit.txt",
            content_type="text/plain",
            data=b"metadata transaction fails",
            settings=_settings(),
            extract_metadata=False,
            tenant_id="00000000-0000-0000-0000-000000000001",
            actor_user_id="00000000-0000-0000-0000-000000000002",
            workspace_id="00000000-0000-0000-0000-000000000003",
        )

    session.rollback.assert_called_once()
    client.remove_object.assert_called_once()


def test_unverified_metadata_failure_compensation_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, client, _register = _workspace_upload(monkeypatch)
    session.commit.side_effect = RuntimeError("database unavailable")
    client.remove_object.side_effect = RuntimeError("object store unavailable")

    with pytest.raises(StorageError, match="cleanup could not be verified"):
        upload_document(
            schema_name="tenant_aaaaaaaaaaaa",
            filename="commit.txt",
            content_type="text/plain",
            data=b"metadata transaction fails",
            settings=_settings(),
            extract_metadata=False,
            tenant_id="00000000-0000-0000-0000-000000000001",
            actor_user_id="00000000-0000-0000-0000-000000000002",
            workspace_id="00000000-0000-0000-0000-000000000003",
        )

    session.rollback.assert_called_once()
    client.remove_object.assert_called_once()


def test_enqueue_failure_moves_workspace_resource_out_of_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _client, _register = _workspace_upload(monkeypatch, enqueue=False)
    update_result = MagicMock()
    update_result.rowcount = 1
    session.execute.return_value = update_result

    result = upload_document(
        schema_name="tenant_aaaaaaaaaaaa",
        filename="queue.txt",
        content_type="text/plain",
        data=b"queue failure",
        settings=_settings(),
        extract_metadata=False,
        tenant_id="00000000-0000-0000-0000-000000000001",
        actor_user_id="00000000-0000-0000-0000-000000000002",
        workspace_id="00000000-0000-0000-0000-000000000003",
    )

    assert result.document.status == "failed"
    statements = [str(call.args[0]) for call in session.execute.call_args_list]
    assert any("UPDATE omnibase_meta.resource_registry" in value for value in statements)
    assert any("resource_registry.state" in value for value in statements)


@pytest.mark.parametrize(
    ("ingest_result", "expected_document_state", "expected_resource_state"),
    [
        (IngestResult("doc", 1, 1, None), "indexed", "active"),
        (IngestResult("doc", 0, 0, "No extractable text content"), "failed", "failed"),
    ],
)
def test_worker_converges_workspace_resource_with_document_index_state(
    monkeypatch: pytest.MonkeyPatch,
    ingest_result: IngestResult,
    expected_document_state: str,
    expected_resource_state: str,
) -> None:
    first_session = MagicMock()
    final_session = MagicMock()
    document = SimpleNamespace(status="queued", error_detail=None, metadata_={})
    resource = SimpleNamespace(state="provisioning", version=1)
    first_session.execute.return_value = _scalar(document)
    final_session.execute.side_effect = [_scalar(document), _scalar(resource)]
    factory = MagicMock(side_effect=[first_session, final_session])
    monkeypatch.setattr(worker_tasks, "get_session_factory", lambda: factory)
    monkeypatch.setattr(worker_tasks, "ingest_document", lambda **_kwargs: ingest_result)

    worker_tasks._process_ingest(
        schema_name="tenant_aaaaaaaaaaaa",
        document_id="00000000-0000-0000-0000-000000000004",
        file_data=b"fixture",
        filename="facts.txt",
        mime_type="text/plain",
    )

    assert document.status == expected_document_state
    assert resource.state == expected_resource_state
    assert resource.version == 2


def test_workspace_retrieval_uses_authoritative_v1_and_exact_active_binding() -> None:
    session = MagicMock()
    statements: list[str] = []

    def execute(statement: object) -> MagicMock:
        statements.append(str(statement))
        call = len(statements)
        if call == 1:
            return _scalar("tenant_aaaaaaaaaaaa")
        if call == 2:
            return MagicMock()
        result = MagicMock()
        if call == 3:
            result.all.return_value = [
                (
                    "00000000-0000-0000-0000-000000000010",
                    "00000000-0000-0000-0000-000000000011",
                    "ORCHID-417 belongs to Workspace A.",
                    {"page": 2},
                    0.9,
                    0,
                )
            ]
        else:
            result.all.return_value = []
        return result

    session.execute.side_effect = execute
    retriever = RagKnowledgeRetriever(MagicMock(return_value=session))

    chunks = retriever.retrieve(
        tenant_id="00000000-0000-0000-0000-000000000001",
        tenant_schema="ignored_and_server_resolved",
        workspace_id="00000000-0000-0000-0000-000000000003",
        query="ORCHID-417",
        top_k=5,
    )

    assert len(chunks) == 1
    assert chunks[0].document_id == "00000000-0000-0000-0000-000000000011"
    canonical_sql = statements[2]
    assert "FROM embeddings" in canonical_sql
    assert "embeddings_v2" not in canonical_sql
    assert "resource_scope_bindings" in canonical_sql
    assert "resource_registry" in canonical_sql
    assert "resource_scope_bindings.workspace_id" in canonical_sql
    assert "resource_scope_bindings.tenant_id" in canonical_sql
    assert "resource_registry.state" in canonical_sql
    session.close.assert_called_once()


def test_workspace_retrieval_deduplicates_v2_shadow_when_v1_is_available() -> None:
    session = MagicMock()

    def execute(_statement: object) -> MagicMock:
        call = session.execute.call_count
        if call == 1:
            return _scalar("tenant_aaaaaaaaaaaa")
        if call == 2:
            return MagicMock()
        result = MagicMock()
        if call == 3:
            result.all.return_value = [
                (
                    "00000000-0000-0000-0000-000000000010",
                    "00000000-0000-0000-0000-000000000011",
                    "authoritative canonical chunk",
                    {"page": 1},
                    0.5,
                    0,
                )
            ]
        else:
            result.all.return_value = [
                (
                    "00000000-0000-0000-0000-000000000012",
                    "00000000-0000-0000-0000-000000000011",
                    "duplicate shadow chunk",
                    {"page": 1},
                    1.0,
                    0,
                )
            ]
        return result

    session.execute.side_effect = execute
    retriever = RagKnowledgeRetriever(MagicMock(return_value=session))

    chunks = retriever.retrieve(
        tenant_id="00000000-0000-0000-0000-000000000001",
        tenant_schema="ignored_and_server_resolved",
        workspace_id="00000000-0000-0000-0000-000000000003",
        query="ORCHID-417",
        top_k=5,
    )

    assert len(chunks) == 1
    assert chunks[0].chunk_id == "00000000-0000-0000-0000-000000000010"
    assert chunks[0].content == "authoritative canonical chunk"


def test_workspace_private_document_visibility_requires_live_binding_membership() -> None:
    clause = document_service._document_visibility_clause(
        tenant_id="00000000-0000-0000-0000-000000000001",
        actor_user_id="00000000-0000-0000-0000-000000000002",
        write=True,
    )
    assert clause is not None
    statement = str(select(Document.id).where(clause))
    assert "resource_registry" in statement
    assert "resource_scope_bindings" in statement
    assert "workspace_memberships" in statement
    assert "workspace_memberships.user_id" in statement
    assert "workspace_memberships.state" in statement
    assert "workspace_memberships.role" in statement


def test_workspace_upload_binding_uses_global_resource_model() -> None:
    assert ResourceScopeBinding.__table__.schema == "omnibase_meta"
    assert ResourceRecord.__table__.schema == "omnibase_meta"
