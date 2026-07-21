#!/usr/bin/env bash
# One-time server bootstrap for spotiosu (Ubuntu 22.04, no Docker).
#
# This box is an OpenVZ container: the kernel is shared, so Docker cannot run.
# Everything is installed natively instead - PostgreSQL, Caddy and the app as a
# systemd service.
#
# Run as root:   bash setup-server.sh
# Safe to re-run: every step is idempotent.

set -euo pipefail

APP_USER="deploy"
APP_ROOT="/opt/spotiosu"
DOMAIN="${DOMAIN:-spotiosu.ru}"
PY="python3.12"

echo "==> Creating $APP_USER"
id -u "$APP_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$APP_USER"

echo "==> Base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq software-properties-common ca-certificates curl gnupg rsync

echo "==> Python 3.12 (rosu-pp-py needs >=3.11; jammy ships 3.10)"
add-apt-repository -y ppa:deadsnakes/ppa >/dev/null
apt-get update -qq
apt-get install -y -qq "$PY" "${PY}-venv" "${PY}-dev" build-essential

echo "==> PostgreSQL"
apt-get install -y -qq postgresql
systemctl enable --now postgresql

# Create role/database if missing. Password comes from PG_PASSWORD or is generated.
PG_PASSWORD="${PG_PASSWORD:-$(openssl rand -hex 24)}"
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='spotiosu'" | grep -q 1; then
    sudo -u postgres psql -qc "CREATE ROLE spotiosu LOGIN PASSWORD '${PG_PASSWORD}';"
    echo "    created role 'spotiosu' with password: ${PG_PASSWORD}"
    echo "    >>> put this in ${APP_ROOT}/.env as part of SPOTIOSU_DSN <<<"
else
    echo "    role 'spotiosu' already exists (password unchanged)"
fi
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='spotiosu'" | grep -q 1 \
    || sudo -u postgres createdb -O spotiosu spotiosu

echo "==> Caddy (automatic HTTPS)"
if ! command -v caddy >/dev/null 2>&1; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update -qq
    apt-get install -y -qq caddy
fi

echo "==> Application directories"
install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$APP_ROOT" "$APP_ROOT/app"
if [ ! -d "$APP_ROOT/venv" ]; then
    sudo -u "$APP_USER" "$PY" -m venv "$APP_ROOT/venv"
    sudo -u "$APP_USER" "$APP_ROOT/venv/bin/pip" install -q --upgrade pip
fi

if [ ! -f "$APP_ROOT/.env" ]; then
    cat > "$APP_ROOT/.env" <<EOF
SPOTIOSU_CLIENT_ID=63675
SPOTIOSU_CLIENT_SECRET=replace-me
SPOTIOSU_SESSION_SECRET=$(openssl rand -hex 32)
SPOTIOSU_PUBLIC_BASE=https://${DOMAIN}
SPOTIOSU_HOST=127.0.0.1
SPOTIOSU_PORT=8000
SPOTIOSU_DSN=postgresql://spotiosu:${PG_PASSWORD}@127.0.0.1:5432/spotiosu
EOF
    echo "    wrote ${APP_ROOT}/.env  --  EDIT SPOTIOSU_CLIENT_SECRET!"
fi
chown "$APP_USER:$APP_USER" "$APP_ROOT/.env"
chmod 600 "$APP_ROOT/.env"

echo "==> systemd service"
cp "$(dirname "$0")/spotiosu.service" /etc/systemd/system/spotiosu.service
systemctl daemon-reload
systemctl enable spotiosu

echo "==> Letting $APP_USER restart the service from CI"
cat > /etc/sudoers.d/spotiosu <<EOF
$APP_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart spotiosu, /usr/bin/systemctl status spotiosu, /usr/bin/systemctl start spotiosu, /usr/bin/systemctl stop spotiosu
EOF
chmod 440 /etc/sudoers.d/spotiosu
visudo -cf /etc/sudoers.d/spotiosu

echo "==> Caddy site config"
sed "s/{\$SITE_ADDRESS}/${DOMAIN}/g" "$(dirname "$0")/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

echo
echo "Done. Next:"
echo "  1. edit ${APP_ROOT}/.env  (SPOTIOSU_CLIENT_SECRET)"
echo "  2. push to master - CI will sync the code and start the service"
