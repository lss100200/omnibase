"""No-Docker persistence foundation for the OmniBase personal desktop."""

from omnibase.desktop_local.config import (
    DesktopLocalConfig,
    default_user_data_root,
    prepare_data_root,
    validate_data_root,
)
from omnibase.desktop_local.database import (
    DesktopLocalHealth,
    initialized_database,
    local_health,
    migrate_database,
    open_database,
)
from omnibase.desktop_local.errors import (
    DesktopDatabaseUnavailable,
    DesktopLocalError,
    DesktopMigrationError,
    UnsafeDataRoot,
)
from omnibase.desktop_local.repository import (
    ClaimedRuntimeJob,
    append_audit_event,
    claim_next_runtime_job,
    create_owner,
    create_workspace,
    enqueue_runtime_job,
    finish_runtime_job,
    start_runtime_job,
)
from omnibase.desktop_local.schema import DESKTOP_APPLICATION_ID, DESKTOP_SCHEMA_VERSION

__all__ = [
    "DESKTOP_APPLICATION_ID",
    "DESKTOP_SCHEMA_VERSION",
    "ClaimedRuntimeJob",
    "DesktopDatabaseUnavailable",
    "DesktopLocalConfig",
    "DesktopLocalError",
    "DesktopLocalHealth",
    "DesktopMigrationError",
    "UnsafeDataRoot",
    "append_audit_event",
    "claim_next_runtime_job",
    "create_owner",
    "create_workspace",
    "default_user_data_root",
    "enqueue_runtime_job",
    "finish_runtime_job",
    "initialized_database",
    "local_health",
    "migrate_database",
    "open_database",
    "prepare_data_root",
    "start_runtime_job",
    "validate_data_root",
]
