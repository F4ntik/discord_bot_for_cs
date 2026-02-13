#-------------------------------------------------------------------
#-- Asura Bot Version
#-------------------------------------------------------------------
# _VERSION_ = "0.3.1" # deprecated
#-------------------------------------------------------------------

# Токен бота для доступа к API Discord
BOT_TOKEN = ''

# Ключ API для доступа к сторонним сервисам (если требуется)
API_KEY = ''

# Идентификатор гильдии (сервера) Discord, на котором работает бот
GUILD_ID = 

# Идентификатор канала, в который бот будет отправлять сообщения из чата
CS_CHAT_CHNL_ID = 

# Идентификатор канала для администраторов
# Укажите числовой ID Discord-канала, куда бот будет отправлять служебные уведомления.
# Если оставить значение пустым, уведомления отправляться не будут.
ADMIN_CHANNEL_ID = None

# Идентификатор информационного канала
INFO_CHANNEL_ID = 

# Legacy-параметр старой pull-модели статуса (оставлен для совместимости, сейчас не используется)
STATUS_INTERVAL = 10

#-------------------------------------------------------------------
# New in 0.3.1
CS_RECONNECT_INTERVAL = 60
# Минимальный интервал между попытками подключения к CS (в секундах).
# Нужен, чтобы избежать дублей при старте (BE_READY + автопереподключение).
CS_CONNECT_MIN_INTERVAL = 2
# Параметры ниже оставлены для обратной совместимости.
# После перехода на push-модель статуса (события + heartbeat из AMX) они не используются.
CS_INFO_WEBHOOK_TIMEOUT = 12
CS_INFO_WEBHOOK_MAX_MISSES = 3
#-------------------------------------------------------------------

# Параметры установки карт через /map_install (FTP/FTPS + опциональная ротация)
MAP_DEPLOY_PROTOCOL = "ftps"  # ftp | ftps
MAP_FTP_HOST = ""
MAP_FTP_PORT = 21
MAP_FTP_USER = ""
MAP_FTP_PASSWORD = ""
MAP_FTP_PASSIVE = True
MAP_FTP_USE_TLS = True
MAP_FTP_TIMEOUT_SEC = 30
# Удалённые пути на FTP/FTPS (абсолютные пути на стороне game-сервера).
# Пример: "/cstrike/maps", "/home/cs/serverfiles/cstrike/maps"
MAP_REMOTE_MAPS_DIR = "/cstrike/maps"
MAP_REMOTE_BASE_DIR = "/cstrike"
MAP_REMOTE_MODELS_DIR = "/cstrike/models"
MAP_REMOTE_SOUND_DIR = "/cstrike/sound"
MAP_REMOTE_SPRITES_DIR = "/cstrike/sprites"
MAP_REMOTE_GFX_DIR = "/cstrike/gfx"
MAP_REMOTE_OVERVIEWS_DIR = "/cstrike/overviews"
MAP_REMOTE_RESOURCE_DIR = "/cstrike/resource"
MAP_INSTALL_SYNC_TIMEOUT_SEC = 12
MAP_INSTALL_MAX_FILE_MB = 200
MAP_INSTALL_WORKDIR = "uploaded_maps"
#-------------------------------------------------------------------

# Хост и пароль для подключения к серверу (например, игровому серверу)
CS_HOST = '111.111.11.11'  # Локальный хост
CS_RCON_PASSWORD = ''  # Пароль для удаленного управления

# Настройки подключения к базе данных
DB_HOST = '..ru'  # Хост базы данных
DB_PORT = 3306  # Порт базы данных, по дефолту MySQL = 3306
DB_USER = ''  # Имя пользователя базы данных
DB_PASSWORD = ''  # Пароль пользователя базы данных
DB_NAME = ''  # Имя базы данных


# Порт веб-сервера, на котором будет работать приложение
WEB_HOST_ADDRESS = '0.0.0.0'
WEB_SERVER_PORT = 8080
WEB_ALLOWED_IPS = ['111.111.11.11']

# redis (универсальные значения)
REDIS_HOST = '127.0.0.1'
REDIS_PORT = 6379
