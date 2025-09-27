#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-discord_bot.service}"
SUDO_BIN="${SUDO_BIN:-sudo}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-"$PROJECT_DIR/backups"}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$TIMESTAMP"
REPO_URL_DEFAULT="$(git -C "$PROJECT_DIR" config --get remote.origin.url 2>/dev/null || true)"
REPO_URL="${REPO_URL:-$REPO_URL_DEFAULT}"

if [[ -z "$REPO_URL" ]]; then
  echo "Не удалось определить URL репозитория. Укажите его через переменную окружения REPO_URL." >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "[updater] Остановка сервиса $SERVICE_NAME"
$SUDO_BIN systemctl stop "$SERVICE_NAME"

mkdir -p "$BACKUP_DIR"
echo "[updater] Создание резервной копии в $BACKUP_DIR"
rsync -a --delete \
  --exclude 'venv' \
  --exclude '__pycache__' \
  --exclude 'backups' \
  --exclude '.git' \
  --exclude 'config.py' \
  "$PROJECT_DIR"/ "$BACKUP_DIR"/

echo "[updater] Загрузка репозитория $REPO_URL"
git clone --depth 1 "$REPO_URL" "$TMP_DIR/repo"

echo "[updater] Обновление файлов проекта"
rsync -a --delete \
  --exclude 'venv' \
  --exclude '__pycache__' \
  --exclude 'backups' \
  --exclude '.git' \
  --exclude 'config.py' \
  --exclude 'amxmodx_plugin' \
  "$TMP_DIR/repo"/ "$PROJECT_DIR"/

echo "[updater] Запуск сервиса $SERVICE_NAME"
$SUDO_BIN systemctl start "$SERVICE_NAME"

echo "[updater] Готово"
