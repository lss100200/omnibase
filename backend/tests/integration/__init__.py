"""Integration tests package.

Tests in this directory require running infrastructure:
- PostgreSQL (pgvector)
- MinIO
- Redis

They are auto-skipped unless the OMNIBASE_INTEGRATION_TESTS env var is set
to "1" (or pytest is invoked with -m integration).

See tests/integration/conftest.py for fixtures.
"""
