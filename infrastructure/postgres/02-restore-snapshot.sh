#!/bin/sh
# Restores the bundled demo snapshot (calibrated reference environment, incident history,
# model registry) into a FRESH platform database. Runs only when the Postgres volume is created,
# so an existing installation is never overwritten. Set CAUSALOPS_RESTORE_SNAPSHOT=false to start
# from an empty database instead (the environment then begins in LEARNING).
#
# The Postgres entrypoint SOURCES this file when it is not executable (e.g. after a git checkout
# that dropped the mode bit), so it must not call `exit` or change shell options.
causalops_snapshot=/snapshot/causalops.sql.gz
if [ "${CAUSALOPS_RESTORE_SNAPSHOT:-true}" != "true" ]; then
  echo "CausalOps snapshot restore disabled; Flyway will create an empty schema."
elif [ ! -f "$causalops_snapshot" ]; then
  echo "No CausalOps snapshot at $causalops_snapshot; Flyway will create an empty schema."
else
  echo "Restoring CausalOps demo snapshot into database $POSTGRES_DB ..."
  gunzip -c "$causalops_snapshot" | psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"
  echo "CausalOps demo snapshot restored."
fi
