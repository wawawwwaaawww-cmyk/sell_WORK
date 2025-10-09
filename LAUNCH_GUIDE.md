# 🚀 Запуск Telegram Sales Bot

## Шаг 1: Установка зависимостей

### Для Windows (рекомендуется):
```bash
pip install -r requirements_windows.txt
```

### Стандартная установка:
```bash
pip install -r requirements.txt
```

### Если возникают ошибки с Rust:
Если вы получаете ошибки о том, что требуется Rust compiler, используйте упрощенную версию:
```bash
pip install -r requirements_minimal.txt
```

### Альтернативное решение:
Используйте pre-compiled packages:
```bash
pip install --only-binary=all -r requirements.txt
```

## Шаг 2: Настройка переменных окружения

1. Скопируйте `.env.example` в `.env`:
```bash
copy .env.example .env
```

2. Отредактируйте `.env` файл и заполните:
   - `TELEGRAM_BOT_TOKEN` - токен вашего бота от @BotFather
   - `DATABASE_URL` - строка подключения к PostgreSQL
   - `OPENAI_API_KEY` - API ключ OpenAI (опционально)

## Шаг 3: Настройка базы данных

### Локальный PostgreSQL (готовые скрипты)
1. Запустите встроенный сервер: `scripts/start_postgres.sh` (порт 5433).
2. Проверьте, что соединение доступно: `pg_isready -h localhost -p 5433`.
3. Убедитесь, что в `.env` указана строка подключения:
```
DATABASE_URL=postgresql+asyncpg://seller_app:sellerapp_pass@localhost:5433/seller_krypto
```
4. Примените миграции: `alembic upgrade head`.

### Внешний PostgreSQL (Neon/production)
1. Создайте БД и пользователя в управляемом кластере.
2. Обновите `DATABASE_URL`/`DATABASE_URL_SYNC` на строку удалённого сервера (не забудьте `sslmode=require`).
3. Запустите `alembic upgrade head`, затем выполните smoke-тесты `python health_check.py`.

## Шаг 4: Создание первого администратора

```bash
python create_admin.py
```

Введите ваш Telegram ID (можно получить у @userinfobot)

## Шаг 5: Проверка системы

```bash
python health_check.py
```

Убедитесь, что все проверки прошли успешно.

## Шаг 6: Запуск бота

### Development режим (polling):
```bash
python start_dev.py
```

### Production режим (webhook):
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

## 📱 Первые шаги после запуска

1. Найдите вашего бота в Telegram
2. Отправьте `/start` для проверки
3. Отправьте `/admin` для проверки админ-панели
4. Создайте первые материалы и продукты через админ-панель

## 🛠️ Полезные команды

- `python health_check.py` - проверка системы
- `python create_admin.py` - создание админа  
- `python start_dev.py` - запуск в dev режиме

## ❓ Решение проблем

### Ошибка "Bot token invalid"
- Проверьте токен бота в `.env`
- Убедитесь, что бот создан через @BotFather

### Ошибка подключения к БД
- Проверьте DATABASE_URL в `.env`
- Убедитесь, что PostgreSQL запущен
- Проверьте права доступа к базе данных

### Ошибки импорта
- Убедитесь, что все зависимости установлены
- Проверьте версию Python (требуется 3.9+)

### Ошибки компиляции Rust
- Используйте `requirements_minimal.txt` вместо `requirements.txt`
- Или установите Rust toolchain: https://rustup.rs/
- Или используйте pre-compiled packages: `pip install --only-binary=all -r requirements.txt`

### Ошибка "No module named 'structlog'" в venv
- Активируйте виртуальное окружение: `& .venv/Scripts/Activate.ps1`
- Установите зависимости в активированном окружении

### Проблемы с SQLite
- Проверьте права на запись в директорию проекта