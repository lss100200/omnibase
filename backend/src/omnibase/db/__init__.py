"""OmniBase database package.

Layout:
- models.py      : Base class + GLOBAL_METADATA + Tenant
- tenant/        : Per-tenant ORM (users, documents, chunks) - added in B4/B5
- audit/         : Audit trail (Phase 2+)
"""

from omnibase.db.models import GLOBAL_METADATA, GLOBAL_SCHEMA, TENANT_METADATA, Base, Tenant

__all__ = ["GLOBAL_METADATA", "GLOBAL_SCHEMA", "TENANT_METADATA", "Base", "Tenant"]
