# Test plan — PR #1 (monetization)

Bot: **@GPTpayeasybot** → https://t.me/GPTpayeasybot
Repo: dzinnlok101-netizen/gptbot, branch `devin/1778128460-init-bot`.

## Constraints (must be acknowledged before execution)

- I drive the bot from the VM (long polling). The user @dzinnlok101 drives the
  client side from real Telegram. I observe `bot/__main__.py` stdout (INFO logs
  via `logging.basicConfig`) and the SQLite file directly.
- Telegram Stars payment requires a real Telegram account with stars balance.
  Only the user can perform it. The pre-checkout / successful_payment path is
  proven only after a real (or developer-test) payment goes through.
- Channel-subscription verification calls `getChatMember`, which only works if
  @GPTpayeasybot is an administrator in @investor_giftov. If it isn't,
  `is_user_subscribed` returns `None` and the bonus claim must show the
  "verification unavailable" alert (this is itself a meaningful assertion).
- For admin testing, ADMIN_USER_IDS must contain the user's Telegram user-id.
  After T1 we read the user-id from `bot/database.py`'s users table directly
  and inject it via env var, then restart the bot.

## Scenarios under test

Each step has a single concrete pass/fail criterion. Steps that depend on
external action by the user are marked **[user]**; steps verified by me via
logs / SQLite are **[obs]**.

### T1 — Trial baseline
- **[user]** /start the bot fresh.
- **[obs]** SQLite row for the user has `text_credits=5`, `image_credits=1`,
  `current_model='gpt-5.5'`, `channel_bonus_claimed=0`. → expected via
  `SELECT * FROM users`. **Fail** if any of those values differ.
- **[user]** /profile.
- **[obs]** Reply text contains literally `Текстовые запросы: <b>5</b>` and
  `Картинки: <b>1</b>` and `Модель: <b>gpt-5.5</b>`. **Fail** on any other
  numbers or model.

### T2 — Quota enforcement (text)
- **[user]** Send 5 plain text messages (e.g. `привет`, `1`, `2`, `3`, `4`).
  After each, the bot must reply with model-generated text.
- **[obs]** After each, `text_credits` decrements by 1 (5→4→3→2→1→0).
  **Fail** if it doesn't decrement, or decrements before the model replies.
- **[obs]** After the 5th message, the bot sends an extra message
  `Это был последний бесплатный запрос.` with a keyboard.
- **[user]** Send a 6th text message.
- **[obs]** Bot replies `💬 У тебя кончились бесплатные запросы.` and shows the
  inline keyboard with the channel-subscribe + buy-stars + invite buttons.
  **No** assistant chat reply must follow. **Fail** if the model still answers.

### T3 — Quota enforcement (image)
- **[user]** /image небольшое красное яблоко на белом фоне
- **[obs]** Bot uploads a PNG. `image_credits` decrements 1→0. **Fail** if it
  sends text or doesn't decrement.
- **[user]** /image кошка на стуле
- **[obs]** Bot replies `🖼 У тебя кончились бесплатные картинки.` with the
  same keyboard. No image is sent. **Fail** if image is generated.

### T4 — Channel bonus (depends on bot being admin in the channel)
- **[user]** Make sure account is subscribed to @investor_giftov, then tap
  inline button `✅ Я подписался — забрать бонус`.
- **[obs] If bot is admin in channel:** alert "+20 текст, +3 картинок
  начислено!", DB has `channel_bonus_claimed=1`, credits incremented by exactly
  +20 / +3. **Fail** on any other increment or if `channel_bonus_claimed` is
  still 0.
- **[obs] If bot is NOT admin:** alert "Не могу проверить подписку. Сообщи
  владельцу бота…" — credits unchanged, `channel_bonus_claimed=0`. This is a
  pass for the failure path.
- **[user]** Tap the same button a second time.
- **[obs]** Alert "Бонус уже получен." — credits unchanged. **Fail** if
  credits are added again.

### T5 — Telegram Stars buy flow (owner-only test)
- **[user]** /buy → tap `Pack S — 100 ⭐`.
- **[obs]** A Telegram invoice is sent with currency XTR, amount 100, payload
  starting with `pack:small:<user_id>`. Verified via aiogram log line
  `INFO aiogram.event: Update id=… for SuccessfulPayment` after payment, and
  the raw invoice metadata I dump to log on `send_invoice`.
- **[user]** Pay the invoice (or cancel if testing only the rejection path).
- **[obs] On payment:** `pre_checkout_query` is answered `ok=True`,
  `successful_payment` handler runs, credits increase by exactly +100 / +5,
  a `purchases` row is inserted with `stars=100`, `pack_id='small'`,
  non-empty `telegram_payment_charge_id`. Bot replies `✅ Оплата прошла. …`.
  **Fail** if any of those don't happen.
- **[user]** /profile.
- **[obs]** Updated `Текстовые запросы` reflects the new total.

### T6 — Referral
- **[user]** /ref → copy link `https://t.me/GPTpayeasybot?start=ref_<ID1>`.
- **[user]** Open the link from a SECOND Telegram account (or with a
  fresh-test-user) and send /start.
- **[obs]** Second user is registered with `referrer_id=<ID1>`. First user's
  `ref_count` becomes 1 and `text_credits` increases by exactly +10 (and
  `image_credits` by +1). **Fail** if numbers differ. Self-referral with
  same user must be ignored (`referrer_id` stays NULL on first /start).

### T7 — Admin
- **[user]** /stats from the admin account.
- **[obs]** Reply contains `Пользователей:` followed by exact count from
  SQLite, `Покупок:` and `Доход (⭐):` matching `SELECT COUNT(*), SUM(stars)
  FROM purchases`. **Fail** on mismatch.
- **[user]** /grant <some_test_user> small.
- **[obs]** Reply `Выдано: Pack S — 100 запросов → <id>`. Target user's
  `text_credits` increases by exactly +100, `image_credits` by +5.
  **Fail** if not granted, or if non-admin can issue /grant (negative test:
  send /grant from any non-admin account → silently ignored).

## Why these tests catch real breakage

- T1/T2/T3 would fail if the SQLite migrations didn't run, if
  `_ensure_user` didn't seed credits from settings, or if the
  `consume_text_credit` / `consume_image_credit` logic short-circuited.
- T2 and T3 would also fail if the menu keyboard isn't shown when credits
  hit 0 (proves `_no_credits_keyboard` and the post-decrement check fire).
- T4 catches both happy- and sad-path of channel verification, including the
  config issue (bot not admin) which is the most likely real-world failure.
- T5 directly proves the XTR payment plumbing — invoice payload encoding,
  `pre_checkout_query` answer, atomic credit grant + purchase logging.
- T6 proves deep-link parsing, idempotent ref counting, and self-ref guard.
- T7 proves admin gating, /grant and /stats SQL aggregation.

## Evidence I will collect

- INFO logs from `bot/__main__.py` for every step.
- `sqlite3 gptbot.db ".schema"` after first /start (proves migrations ran).
- `sqlite3 gptbot.db "SELECT * FROM users"`, `… FROM purchases` snapshots
  before and after each test step.
- Screenshots of the Telegram client (provided by user) at the points
  where there's no log evidence (e.g. invoice modal, alert text).
