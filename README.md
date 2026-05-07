# gptbot

Telegram-бот с ChatGPT через провайдера [Codex Sale](https://codex.sale) (OpenAI-совместимый API).

## Возможности

- Диалог с моделью (история сохраняется в памяти на каждого пользователя).
- Генерация картинок через `gpt-image-2`.
- Переключение модели на лету (через инлайн-клавиатуру).
- Опциональное ограничение доступа по списку Telegram user-id.

### Команды

| Команда | Что делает |
| --- | --- |
| `/start`, `/help` | Приветствие и список команд |
| `/reset` | Очистить историю диалога |
| `/model` | Выбрать модель (gpt-5.5, gpt-5.4, gpt-5.4-mini, gpt-5.3-codex) |
| `/image <описание>` | Сгенерировать картинку |

## Стек

- Python 3.11+
- [aiogram 3](https://docs.aiogram.dev/) — Telegram Bot API
- [openai](https://github.com/openai/openai-python) — клиент OpenAI-совместимого API
- [uv](https://docs.astral.sh/uv/) (рекомендуется) или pip — установка зависимостей

## Локальный запуск

1. Создайте бота у [@BotFather](https://t.me/BotFather) и получите токен.
2. Получите API-ключ Codex Sale на [codex.sale](https://codex.sale).
3. Скопируйте `.env.example` в `.env` и заполните значения:

   ```bash
   cp .env.example .env
   # отредактируйте .env
   ```

4. Установите зависимости и запустите:

   ```bash
   # вариант с uv (рекомендуется)
   uv sync
   uv run gptbot

   # или с pip
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   gptbot
   ```

Бот запустится в режиме long-polling и начнёт принимать сообщения.

## Переменные окружения

| Переменная | Обязательная | По умолчанию | Описание |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | да | — | Токен от @BotFather |
| `CODEX_SALE_API_KEY` | да | — | API-ключ Codex Sale |
| `CODEX_SALE_BASE_URL` | нет | `https://codex.sale/v1` | Endpoint провайдера |
| `DEFAULT_CHAT_MODEL` | нет | `gpt-5.5` | Модель по умолчанию |
| `DEFAULT_IMAGE_MODEL` | нет | `gpt-image-2` | Модель для `/image` |
| `SYSTEM_PROMPT` | нет | `You are a helpful assistant. Answer concisely.` | Системный промпт |
| `MAX_HISTORY_MESSAGES` | нет | `20` | Сколько сообщений хранить в истории на пользователя |
| `ALLOWED_USER_IDS` | нет | пусто (все) | Список user-id через запятую |

## Тесты и линтер

```bash
uv run ruff check .
uv run pytest -q
```

## Деплой

Простейший вариант — запустить бота на любой VPS под systemd или в Docker.
Бот использует long-polling, поэтому ему не нужен публичный URL и webhook.

Пример минимального `Dockerfile`:

```Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e .
CMD ["gptbot"]
```

```bash
docker build -t gptbot .
docker run --rm --env-file .env gptbot
```

## Лицензия

MIT (см. `LICENSE`, если добавите).
