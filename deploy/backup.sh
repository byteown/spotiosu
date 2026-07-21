#!/usr/bin/env bash
# Daily PostgreSQL backup for spotiosu, run by spotiosu-backup.timer.
#
# Dumps to /var/backups/spotiosu, keeps the last KEEP_DAYS files, and verifies
# the archive is readable before rotating anything away.

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/spotiosu}"
KEEP_DAYS="${KEEP_DAYS:-14}"
DB_NAME="${DB_NAME:-spotiosu}"
STAMP="$(date +%F_%H%M)"
TARGET="${BACKUP_DIR}/${DB_NAME}-${STAMP}.sql.gz"

install -d -m 750 "$BACKUP_DIR"

# Dump as the postgres superuser via peer auth - no password needed, and the
# credentials in /opt/spotiosu/.env stay out of this script.
sudo -u postgres pg_dump --clean --if-exists "$DB_NAME" | gzip -9 > "$TARGET"

# A truncated dump is worse than no dump: prove it decompresses and ends cleanly
# before we let rotation delete older, known-good copies.
if ! gzip -t "$TARGET"; then
    echo "backup failed integrity check, removing: $TARGET" >&2
    rm -f "$TARGET"
    exit 1
fi
if ! zcat "$TARGET" | tail -5 | grep -q "PostgreSQL database dump complete"; then
    echo "backup looks truncated, removing: $TARGET" >&2
    rm -f "$TARGET"
    exit 1
fi

chmod 640 "$TARGET"
find "$BACKUP_DIR" -name "${DB_NAME}-*.sql.gz" -mtime "+${KEEP_DAYS}" -delete

echo "backup ok: $TARGET ($(du -h "$TARGET" | cut -f1)), $(find "$BACKUP_DIR" -name '*.sql.gz' | wc -l) kept"
