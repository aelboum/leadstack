#!/bin/sh
# Runs once, when the PostgreSQL data directory is first initialised.
# Creates LeadStack's two roles (SaaS-OS's model):
# - the schema owner, used only by `leadstack-migrate`;
# - the restricted application role (NOSUPERUSER NOBYPASSRLS), used by the
#   API and the worker, which refuse to start under a more privileged role.
# The superuser is used for nothing else.
set -eu

for name in OWNER_DB_USER OWNER_DB_PASSWORD APP_DB_USER APP_DB_PASSWORD; do
  eval "value=\${$name:-}"
  if [ -z "$value" ]; then
    echo "10-roles.sh: $name must be set" >&2
    exit 1
  fi
done

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v owner="$OWNER_DB_USER" -v owner_password="$OWNER_DB_PASSWORD" \
  -v app="$APP_DB_USER" -v app_password="$APP_DB_PASSWORD" \
  -v database="$POSTGRES_DB" <<'SQL'
CREATE ROLE :"owner" LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD :'owner_password';
CREATE ROLE :"app" LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
ALTER DATABASE :"database" OWNER TO :"owner";
REVOKE ALL ON DATABASE :"database" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database" TO :"app";
SQL
