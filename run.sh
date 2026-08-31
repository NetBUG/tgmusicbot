#!/bin/sh
# Build and start the bot. Re-runnable: every step is idempotent.
set -eu

LOG_DIR="${LOG_DIR:-/var/log/tgmusicbot}"

if [ ! -f .env ]; then
    echo "No .env — copy .env.example and fill in TG_TOKEN and TG_ALLOWED_USERS" >&2
    exit 1
fi

# -p, so a second run does not fail; 1000 to match the uid inside the image
if [ ! -d "$LOG_DIR" ]; then
    sudo mkdir -p "$LOG_DIR"
    sudo chown 1000:1000 "$LOG_DIR"
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
elif command -v podman-compose >/dev/null 2>&1; then
    COMPOSE="podman-compose"
else
    echo "No compose implementation found (docker compose / docker-compose / podman-compose)" >&2
    exit 1
fi

$COMPOSE up -d --build --remove-orphans
$COMPOSE logs --tail 20 tgmusicbot
