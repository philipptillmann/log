#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$APP_DIR"
command -v git >/dev/null || { echo 'Install Git first.' >&2; exit 1; }
command -v docker >/dev/null || { echo 'Install Docker Engine and the Compose plugin first.' >&2; exit 1; }
command -v flock >/dev/null || { echo 'Install util-linux (flock) first.' >&2; exit 1; }
exec 9>"$APP_DIR/.update.lock"
flock -n 9 || { echo 'Another update is running.' >&2; exit 1; }
docker compose version >/dev/null
docker info >/dev/null
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo 'Local changes found. Commit or move them before updating. Nothing was overwritten.' >&2
  exit 1
fi
branch="$(git symbolic-ref --quiet --short HEAD)" || { echo 'Check out your deployment branch first.' >&2; exit 1; }
[[ -n "$(git for-each-ref --format='%(upstream)' "refs/heads/$branch")" ]] || { echo 'Set an upstream branch before updating.' >&2; exit 1; }
git fetch --prune
# Fast-forward only: never discard local commits or resolve conflicts automatically.
git merge --ff-only '@{upstream}'
# Re-execute the new updater once, if Git updated this script.
if [[ "${LOG_UPDATE_REEXEC:-0}" != 1 ]]; then
  export LOG_UPDATE_REEXEC=1
  flock -u 9
  exec 9>&-
  exec bash "$APP_DIR/update.sh"
fi
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
fi
docker compose config --quiet
docker compose build --pull
# No down, volume removal, pruning, or deletion of local configuration.
docker compose up -d --wait --wait-timeout 90 --remove-orphans
echo 'log is running. See docker compose ps for status.'
