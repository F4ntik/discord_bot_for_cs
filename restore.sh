#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-discord_bot.service}"
SUDO_BIN="${SUDO_BIN:-sudo}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-"$PROJECT_DIR/backups"}"
TARGET_BACKUP="${1:-}"

if [[ ! -d "$BACKUP_ROOT" ]]; then
  echo "Каталог резервных копий $BACKUP_ROOT не найден." >&2
  exit 1
fi

if [[ -z "$TARGET_BACKUP" ]]; then
  TARGET_BACKUP="$(ls -1dt "$BACKUP_ROOT"/* 2>/dev/null | head -n 1 || true)"
  if [[ -z "$TARGET_BACKUP" ]]; then
    echo "Резервные копии отсутствуют." >&2
    exit 1
  fi
else
  TARGET_BACKUP="$BACKUP_ROOT/$TARGET_BACKUP"
fi

if [[ ! -d "$TARGET_BACKUP" ]]; then
  echo "Резервная копия $TARGET_BACKUP не найдена." >&2
  exit 1
fi

echo "[restore] Остановка сервиса $SERVICE_NAME"
$SUDO_BIN systemctl stop "$SERVICE_NAME"

echo "[restore] Восстановление файлов из $TARGET_BACKUP"
rsync -a --delete \
  --exclude 'venv' \
  --exclude '__pycache__' \
  --exclude 'backups' \
  --exclude '.git' \
  --exclude 'config.py' \
  "$TARGET_BACKUP"/ "$PROJECT_DIR"/

echo "[restore] Запуск сервиса $SERVICE_NAME"
$SUDO_BIN systemctl start "$SERVICE_NAME"

echo "[restore] Готово"
