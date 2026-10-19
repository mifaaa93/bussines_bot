# Запуск на сервере

Бот ходит в Telegram через long polling и хранит данные в SQLite (`data/secretary.sqlite3`). Вебхук и отдельная база не нужны. Сервис запускается от **root**.

На сервере может работать только один процесс с этим токеном. Перед запуском останови бота на своём компьютере, иначе Telegram ответит `409 Conflict`.

## 1. Система

Нужен Linux и Python 3.11 или новее.

```bash
apt update
apt install -y python3 python3-venv python3-pip
```

## 2. Код

```bash
mkdir -p /root/bussines_bot
cd /root/bussines_bot
git clone https://github.com/mifaaa93/bussines_bot .
```

Если репозиторий уже склонирован, обновление:

```bash
cd /root/bussines_bot
systemctl stop secretary
git pull
.venv/bin/pip install -r requirements.txt
systemctl start secretary
```

## 3. Секреты

Файл `.env` в git не попадает. Создай его на сервере вручную:

```bash
cp .env.example .env
nano .env
```

```env
BOT_TOKEN=токен_от_BotFather
ADMIN_USER_IDS=
```

`ADMIN_USER_IDS` необязателен. Аккаунт `480055341` уже админ в коде и видит `/admin`. Сюда можно дописать другие id через запятую.

Бот не привязан к одному Telegram-аккаунту. Любой пользователь пишет `/start`, подключает бота как секретаря в своём Business и получает уведомления в свой чат с ботом.

Права, чтобы файл не читали другие пользователи, даже если потом появится не-root:

```bash
chmod 600 /root/bussines_bot/.env
```

## 4. Окружение

```bash
cd /root/bussines_bot
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
```

Проверка вручную:

```bash
.venv/bin/python -u bot.py
```

В выводе должны быть `commands set` и `Start polling`. Остановка: Ctrl+C.

## 5. systemd от root

`/etc/systemd/system/secretary.service`:

```ini
[Unit]
Description=Telegram secretary bot
After=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/bussines_bot
ExecStart=/root/bussines_bot/.venv/bin/python -u bot.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now secretary
systemctl status secretary
```

Лог приложения: `/root/bussines_bot/logs/bot.log`.  
Лог сервиса: `journalctl -u secretary -f`.

## 6. Что не коммитить

В репозитории не должно быть `.env`, `data/`, `logs/`, `downloads/` и `.venv/`. Это закрыто в `.gitignore`. Историю сообщений на новый сервер переноси отдельно, и только когда бот остановлен: скопируй `data/secretary.sqlite3`.
