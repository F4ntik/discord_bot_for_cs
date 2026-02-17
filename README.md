# Discord Bot for CS 1.6

Автоматизация взаимодействия между Discord и сервером Counter-Strike 1.6.

- Подробная документация: [docs/PROJECT_DOC_RU.md](docs/PROJECT_DOC_RU.md)
- Полный справочник команд с примерами: [docs/COMMANDS_REFERENCE_RU.md](docs/COMMANDS_REFERENCE_RU.md)
- Краткий журнал работ: [PROJECT_LOG.md](PROJECT_LOG.md)
- Скрипты обслуживания: `updater.sh` (обновление) и `restore.sh` (восстановление)
- `updater.sh` сохраняет локальный `config.py` и автоматически добавляет в него новые параметры из репозитория (существующие значения не перезаписываются).
- Для обновления из конкретной ветки используйте `./updater.sh --branch <имя_ветки>`.
- Shell-скрипты в репозитории фиксируются с LF (`.gitattributes`), чтобы на Linux не возникала ошибка `/usr/bin/env: 'bash\\r'`.
- При запуске бот отправляет уведомление в администраторский канал (ID задаётся в `config.py`).
- Статус CS в Discord обновляется push-вебхуками из AMX-плагина (события + heartbeat), без постоянного RCON-поллинга.
- Команда `/map_install` поддерживает `.bsp` и `.zip`: архив может содержать карту и ресурсы (`maps/models/sound/sprites/gfx/overviews/resource`).
- Добавлены явные серверные команды списка карт: `/server_maps` (активная ротация) и `/server_maps_installed` (все `.bsp` в `maps/`), чтобы не путать источник с БД бота.

## Быстрый чеклист для `/map_install`
- В `config.py` должны быть заполнены: `MAP_DEPLOY_PROTOCOL`, `MAP_FTP_HOST`, `MAP_FTP_PORT`, `MAP_FTP_USER`, `MAP_FTP_PASSWORD`.
- Для удалённых путей используются `MAP_REMOTE_MAPS_DIR` и (опционально) `MAP_REMOTE_BASE_DIR`, `MAP_REMOTE_MODELS_DIR`, `MAP_REMOTE_SOUND_DIR`, `MAP_REMOTE_SPRITES_DIR`, `MAP_REMOTE_GFX_DIR`, `MAP_REMOTE_OVERVIEWS_DIR`, `MAP_REMOTE_RESOURCE_DIR` (все значения должны быть абсолютными путями на FTP/FTPS, например `/cstrike/maps`).
- Для FTPS по умолчанию включены `MAP_FTP_USE_TLS = True` и пассивный режим `MAP_FTP_PASSIVE = True`.
- ZIP поддерживает структурный и плоский формат; плоские файлы раскладываются по расширению. В архиве должен быть ровно один `.bsp`.
- Конфликты по файлам на FTP не перезаписываются: такие файлы пропускаются с предупреждением.
- Добавление в ротацию выполняется только при `add_to_rotation=true` и полностью чистой установке (без skipped/rejected и с успешной загрузкой `.bsp`).
