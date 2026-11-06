#!/bin/sh
# Repeatable read-only MySQL grants for the Java Catalog (existing volumes too).
# Passwords are generated locally and must not appear in SQL logs or CLI args.
set -eu

: "${MYSQL_ROOT_PASSWORD:?missing MYSQL_ROOT_PASSWORD}"
: "${CATALOG_READ_PASSWORD:?missing CATALOG_READ_PASSWORD}"
: "${MYSQL_DATABASE:?missing MYSQL_DATABASE}"

case "$MYSQL_DATABASE" in
  ""|*[!a-zA-Z0-9_]*)
    echo "MYSQL_DATABASE must be a simple SQL identifier" >&2; exit 2 ;;
esac
case "$CATALOG_READ_PASSWORD" in
  ""|*[!a-f0-9]*)
    echo "CATALOG_READ_PASSWORD must be generated lowercase hex" >&2; exit 2 ;;
esac
if [ "${#CATALOG_READ_PASSWORD}" -lt 48 ]; then
  echo "CATALOG_READ_PASSWORD too short" >&2
  exit 2
fi

export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"
# Only the three already-migrated public tables are readable. No grants on
# conversations, messages, admin audit, or mutation privileges.
mysql --protocol=tcp -hmysql -uroot --batch <<SQL
CREATE USER IF NOT EXISTS 'gaobao_reader'@'%' IDENTIFIED BY '$CATALOG_READ_PASSWORD';
ALTER USER 'gaobao_reader'@'%' IDENTIFIED BY '$CATALOG_READ_PASSWORD';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'gaobao_reader'@'%';
GRANT SELECT ON `$MYSQL_DATABASE`.`schools` TO 'gaobao_reader'@'%';
GRANT SELECT ON `$MYSQL_DATABASE`.`admission_scores` TO 'gaobao_reader'@'%';
GRANT SELECT ON `$MYSQL_DATABASE`.`enrollment_plans` TO 'gaobao_reader'@'%';
SQL
unset MYSQL_PWD
echo "Java Catalog least-privilege grants installed (three SELECT-only tables)"
