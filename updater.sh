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
BRANCH_NAME=""

# Python/venv
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV="$PROJECT_DIR/venv"
REQ="$PROJECT_DIR/requirements.txt"
MERGE_CONFIG_DEFAULTS="${MERGE_CONFIG_DEFAULTS:-1}"

print_usage() {
  cat <<EOF
Использование: ./updater.sh [опции]

Опции:
  -b, --branch <name>  Обновить проект из указанной ветки
  -h, --help           Показать справку
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -b|--branch)
      if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "[updater] Ошибка: для $1 нужно указать имя ветки"
        print_usage
        exit 1
      fi
      BRANCH_NAME="$2"
      shift 2
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      echo "[updater] Ошибка: неизвестный аргумент: $1"
      print_usage
      exit 1
      ;;
  esac
done

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

if [[ -n "$BRANCH_NAME" ]]; then
  echo "[updater] Загрузка репозитория $REPO_URL (ветка: $BRANCH_NAME)"
  git clone --depth 1 --branch "$BRANCH_NAME" "$REPO_URL" "$TMP_DIR/repo"
else
  echo "[updater] Загрузка репозитория $REPO_URL"
  git clone --depth 1 "$REPO_URL" "$TMP_DIR/repo"
fi

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

CURRENT_CONFIG="$PROJECT_DIR/config.py"
TEMPLATE_CONFIG="$TMP_DIR/repo/config.py"

merge_config_defaults() {
  local current_config="$1"
  local template_config="$2"
  "$PYTHON_BIN" - "$current_config" "$template_config" <<'PY'
import re
import sys
from pathlib import Path

ASSIGN_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=")


def read_text(path: Path):
    for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return path.read_text(encoding=enc), enc
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Не удалось декодировать файл: {path}")


def key_positions(lines):
    positions = []
    for idx, line in enumerate(lines):
        m = ASSIGN_RE.match(line)
        if m:
            positions.append((idx, m.group(1)))
    return positions


def main() -> int:
    current_path = Path(sys.argv[1])
    template_path = Path(sys.argv[2])

    current_text, current_enc = read_text(current_path)
    template_text, _ = read_text(template_path)

    current_lines = current_text.splitlines(keepends=True)
    template_lines = template_text.splitlines(keepends=True)

    current_keys = {key for _, key in key_positions(current_lines)}
    template_pos = key_positions(template_lines)

    added_keys = []
    blocks = []

    for idx, key in template_pos:
        if key in current_keys:
            continue

        # Берем assignment + возможное многострочное значение до следующего ключа.
        end = idx + 1
        while end < len(template_lines) and not ASSIGN_RE.match(template_lines[end]):
            end += 1

        # Добавляем ближайшие комментарии прямо над параметром (без захвата чужих assignment).
        start = idx
        while start > 0:
            prev = template_lines[start - 1]
            if prev.lstrip().startswith("#") or prev.strip() == "":
                start -= 1
                continue
            break

        blocks.append("".join(template_lines[start:end]))
        added_keys.append(key)
        current_keys.add(key)

    if not blocks:
        print("[updater] config.py: новые параметры не найдены")
        return 0

    merged = current_text.rstrip("\n")
    merged += "\n\n# --- Добавлено updater.sh: новые параметры из шаблона config.py ---\n"
    for block in blocks:
        merged += block
        if not block.endswith("\n"):
            merged += "\n"
    if not merged.endswith("\n"):
        merged += "\n"

    current_path.write_text(merged, encoding=current_enc)
    print(f"[updater] config.py: добавлено параметров: {len(added_keys)}")
    print("[updater] config.py: " + ", ".join(added_keys))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
}

if [[ "$MERGE_CONFIG_DEFAULTS" != "0" ]]; then
  if [[ -f "$TEMPLATE_CONFIG" ]]; then
    if [[ -f "$CURRENT_CONFIG" ]]; then
      echo "[updater] Слияние config.py (добавление новых параметров без перезаписи значений)"
      merge_config_defaults "$CURRENT_CONFIG" "$TEMPLATE_CONFIG"
    else
      echo "[updater] config.py отсутствует, копирование шаблона из репозитория"
      cp "$TEMPLATE_CONFIG" "$CURRENT_CONFIG"
    fi
  else
    echo "[updater] Шаблон config.py не найден в репозитории, пропуск слияния"
  fi
else
  echo "[updater] MERGE_CONFIG_DEFAULTS=0, слияние config.py отключено"
fi

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
