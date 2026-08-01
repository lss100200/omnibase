#!/bin/sh
set -eu

case "$POSTGRES_DB" in
  omnibase_test_[a-z0-9_]*) ;;
  *)
    echo "Refusing to initialize destructively unsafe database name: $POSTGRES_DB" >&2
    exit 1
    ;;
esac

case "$TEST_DATABASE_ROLE" in
  omnibase_test_[a-z0-9_]*) ;;
  *)
    echo "Refusing unsafe integration-test role name: $TEST_DATABASE_ROLE" >&2
    exit 1
    ;;
esac

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=test_role="$TEST_DATABASE_ROLE" --set=test_password="$TEST_DATABASE_PASSWORD" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE public.omnibase_test_sentinel (
    marker TEXT PRIMARY KEY CHECK (marker = 'OMNIBASE_DESTRUCTIVE_TEST_DATABASE_V1')
);
INSERT INTO public.omnibase_test_sentinel(marker)
VALUES ('OMNIBASE_DESTRUCTIVE_TEST_DATABASE_V1')
ON CONFLICT DO NOTHING;

SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION',
    :'test_role',
    :'test_password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'test_role') \gexec

GRANT CONNECT, CREATE ON DATABASE :"DBNAME" TO :"test_role";
GRANT USAGE, CREATE ON SCHEMA public TO :"test_role";
GRANT SELECT ON public.omnibase_test_sentinel TO :"test_role";
SQL
