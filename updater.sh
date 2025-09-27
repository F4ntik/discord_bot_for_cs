#!/usr/bin/env bash
set -euo pipefail

# --- Параметры/дефолты ---
SERVICE_NAME="${SERVICE_NAME:-discord_bot.service}"
SUDO_BIN="${SUDO_BIN:-sudo}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-"$PROJECT_DIR/backups"}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$TIMESTAMP"

# Репозиторий по умолчанию (если REPO_URL не задан)
REPO_URL="${REPO_URL:-https://github.com/F4ntik/discord_bot_for_cs}"

# Python/venv
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV="$PROJECT_DIR/venv"
REQ="$PROJECT_DIR/requirements.txt"

# --- Временная папка ---
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
  --exclude 'logs' \
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
  --exclude 'PROJECT_LOG.md' \
  --exclude 'AGENTS.md' \
  "$TMP_DIR/repo"/ "$PROJECT_DIR"/

echo "[updater] Подготовка виртуального окружения"
if [[ ! -d "$VENV" ]]; then
  "$PYTHON_BIN" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "[updater] Обновление pip/wheel"
pip install --upgrade pip wheel

if [[ -f "$REQ" ]]; then
  echo "[updater] Установка зависимостей из $REQ"
  pip install -r "$REQ"
else
  echo "[updater] Файл $REQ не найден, пропуск установки зависимостей"
fi

deactivate

echo "[updater] Запуск сервиса $SERVICE_NAME"
$SUDO_BIN systemctl start "$SERVICE_NAME"

echo "[updater] Готово"
