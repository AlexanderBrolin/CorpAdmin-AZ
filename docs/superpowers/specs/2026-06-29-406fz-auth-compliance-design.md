# 406-ФЗ auth compliance — design

**Epic:** CorpAdmin-AZ-dbo
**Date:** 2026-06-29
**Status:** approved-design (approach A)

## Контекст

ФЗ № 406-ФЗ (31.07.2023, правит 149-ФЗ) запрещает авторизацию на российских
сайтах через иностранные сервисы (Google/Apple OAuth). Штрафы для владельцев
сайтов — с 01.07.2026 (199-ФЗ): юрлицо 500–700 тыс ₽. У нас ~442 из 502
пользователей входят через Google OAuth.

Решения пользователя (зафиксированы):
1. Прод обновить из текущей ветки `CorpAdmin`.
2. Grace-period НЕТ.
3. Отключить вход через Google OAuth.
4. Дать роли `admin` ставить/менять логин и пароль ЛЮБОМУ пользователю,
   включая созданных через Google. Дальше саппорт мигрирует юзеров вручную.

## Scope

### 2.1 — Отключить Google OAuth (ops, без кода)
Механизм уже есть: `settings.is_google_oauth_configured()` смотрит
`GOOGLE_CLIENT_ID`. В прод `.env` ставим `GOOGLE_CLIENT_ID=disabled` + рестарт
backend → фронт прячет кнопку (`GET /auth/config` вернёт
`google_oauth_enabled=false`), эндпоинты `/auth/google` и `/auth/google/callback`
отдают 404. **Изменений кода нет.**

### 2.2 — Admin set-password (код, approach A)
Расширяем существующее админ-редактирование юзера, без новых эндпоинтов.

**Backend:**
- `schemas/user.py` `UserUpdate`: добавить `password: Optional[str] =
  Field(None, min_length=6, max_length=128)` (те же границы, что в `UserCreate`).
- `api/v1/admin.py` `update_user` (`PUT /users/{id}`): если
  `data.password is not None` (поле `password` имеет `min_length=6`, значит
  любое переданное значение валидно; `None` = поле опущено) —
  1. `crud_user.update_password(db, user, data.password)`;
  2. если `user.auth_provider == "google"` → флип `auth_provider = "local"`
     (`google_id` оставляем как есть — безвреден).
  Порядок: сначала email/username/is_active (уже есть, с проверками
  уникальности), затем пароль. Если `password` опущен — хэш не трогаем.
- `crud/user.py`: расширить `update_password(db, user, new_password)` —
  внутри после установки хэша флипать `auth_provider` на `"local"`, если он был
  `"google"`. Один CRUD-вызов, без отдельного helper'а. Логика тривиальна,
  держим в одном месте.

**Почему работает вход после этого:** `crud_user.authenticate()` проверяет
только `password_hash` (не `auth_provider`), поэтому google-юзер с проставленным
хэшем сразу логинится через `POST /auth/login`. После флипа на `local` он ещё и
сможет сам менять пароль через `/auth/change-password`.

**Frontend:**
- `src/pages/AdminUserDetailPage.tsx`: в форму редактирования добавить
  необязательное поле «Новый пароль» (пустое = не менять), слать в `PUT` body.
- `src/api/admin.ts`: тип `UserUpdate` += `password?: string`.

## Out of scope (YAGNI)
- Grace-period / self-service миграция (явно отвергнуто).
- KeyCloak-прокладка поверх Google (юридически = обход, отвергнуто).
- Российский SSO (VK ID / Yandex ID / ЕСИА) — отдельный эпик в будущем.
- Принудительная инвалидация активных google-сессий — истекут сами по TTL
  refresh-token.

## Критический порядок выкатки (защита от локаута)
442 google-юзера потеряют вход в момент отключения Google. Поэтому:
1. Реализовать + смержить 2.2 в `CorpAdmin`.
2. Задеплоить `CorpAdmin` на прод (включает 2.2 + синхронизацию из текущей базы).
3. Проверить на проде, что admin реально ставит пароль google-юзеру и тот
   логинится.
4. **Только потом** флипнуть `GOOGLE_CLIENT_ID=disabled` (2.1) на проде.

Никаких state-changing действий на проде до merge в `CorpAdmin` (правило репо).

## Testing
**Backend (pytest, TDD RED→GREEN):**
- `PUT /users/{id}` с `password` → `password_hash` выставлен, `authenticate()`
  проходит с новым паролем.
- google-юзер: после установки пароля `auth_provider == "local"`.
- `password` короче 6 → 422.
- `PUT` без `password` → хэш не изменился, `auth_provider` не изменился.
- email/username по-прежнему обновляются; проверки уникальности живы.

**Frontend:** `npx tsc --noEmit`; ручная проверка формы на staging (bb).

## Deployment
- Staging: bb.azfi.ru (тест перед прод).
- Прод: wgfi2.p4i.ru — деплой backend (`/opt/corpweb/backend/app`) + frontend
  build, рестарт `corpweb-backend`. БД уже на alembic `0007` (= голова ветки),
  миграций нет. Точный runbook — в плане.
