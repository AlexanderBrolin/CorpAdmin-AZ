# Настройки администратора - Управление лимитами конфигураций

## Обзор

В CorpWeb реализована гибкая система управления ограничениями на количество конфигураций для пользователей. Администратор может динамически изменять максимальное количество конфигов, которые может создать каждый пользователь, без необходимости изменения кода или перезапуска приложения.

## Архитектура

### База данных

Настройки хранятся в таблице `system_settings`:

```sql
CREATE TABLE system_settings (
    id INTEGER PRIMARY KEY DEFAULT 1,              -- Всегда 1 (singleton)
    max_configs_per_user INTEGER NOT NULL DEFAULT 2,  -- Максимум конфигов на пользователя
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by VARCHAR(50),                        -- Имя админа, внесшего изменение
    google_play_url VARCHAR(500),                  -- Ссылка на Google Play в frontend (Установка → Android)
    app_store_url VARCHAR(500),                    -- Ссылка на App Store (Установка → iOS)
    apk_url VARCHAR(500),                          -- Прямая ссылка на APK
    windows_url VARCHAR(500)                       -- Ссылка на Windows-клиент
);
```

| Колонка | Тип | Default | Назначение |
|---|---|---|---|
| `google_play_url` | VARCHAR(500) | NULL | Ссылка на Google Play в frontend (Установка → Android) |
| `app_store_url` | VARCHAR(500) | NULL | Ссылка на App Store (Установка → iOS) |
| `apk_url` | VARCHAR(500) | NULL | Прямая ссылка на APK |
| `windows_url` | VARCHAR(500) | NULL | Ссылка на Windows-клиент |

Эти 4 колонки добавляются в существующую таблицу через `ADD COLUMN IF NOT EXISTS` в [`corpweb/backend/app/db/init_db.py:70-77`](backend/app/db/init_db.py#L70-L77) — safe migration на уже установленных CP.

**Важно:** Эта таблица является singleton - в ней всегда только одна строка с `id = 1`.

### Application-level enforcement

Лимит проверяется в Python в [`corpweb/backend/app/api/v1/configs.py:85-94`](backend/app/api/v1/configs.py#L85-L94) перед INSERT нового конфига:

```python
active_count = crud_config.count_active_by_user(db, current_user.id)
if active_count >= max_configs:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Maximum {max_configs} active configs allowed. Delete an existing config first."
    )
```

При превышении — HTTP 400, INSERT не происходит. Race-condition между двумя одновременными POST-запросами теоретически возможен (count + insert не atomic); на практике не наблюдался, т.к. UI блокирует кнопку "Добавить" при достижении лимита. Если станет проблемой — мигрировать в PG-триггер (отдельный issue).

## API Endpoints

### GET /api/v1/admin/settings

Реализовано в [`corpweb/backend/app/api/v1/admin.py:304-316`](backend/app/api/v1/admin.py#L304-L316). Возвращает текущий объект `SystemSettings`. Требует admin-сессию.

Пример:
```bash
curl -H "Authorization: Bearer $TOKEN" https://panel.example.com/api/v1/admin/settings
```

Ответ:
```json
{
  "id": 1,
  "max_configs_per_user": 2,
  "google_play_url": null,
  "app_store_url": null,
  "apk_url": null,
  "windows_url": null,
  "updated_at": "2026-05-07T12:34:56Z",
  "updated_by": "admin"
}
```

### PATCH /api/v1/admin/settings

Реализовано в [`corpweb/backend/app/api/v1/admin.py:319-357`](backend/app/api/v1/admin.py#L319-L357). Принимает `SystemSettingsUpdate` (см. `backend/app/schemas/settings.py`). `max_configs_per_user` валидируется в диапазоне 1-10.

Пример:
```bash
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"max_configs_per_user": 5}' \
     https://panel.example.com/api/v1/admin/settings
```

**Ограничения:**
- Минимум: 1 конфиг
- Максимум: 10 конфигов (настраивается)
- Только администраторы могут изменять

## Пользовательский интерфейс (планируется)

### Админ-панель

В разделе "Настройки системы" администратор увидит:

```
┌──────────────────────────────────────────────┐
│ Системные настройки                          │
├──────────────────────────────────────────────┤
│                                              │
│ Максимум конфигов на пользователя:          │
│  ┌───────┐                                   │
│  │   2   │  [Изменить]                       │
│  └───────┘                                   │
│                                              │
│ Рекомендуемые значения:                      │
│  • 2 - стандартно (телефон + ноутбук)        │
│  • 3-5 - для power users                     │
│  • 10 - максимум (корпоративные устройства)  │
│                                              │
│ ⚠️ Изменение применяется мгновенно для       │
│    всех новых конфигов. Существующие         │
│    конфиги пользователей не удаляются.       │
│                                              │
│ Последнее изменение: admin                   │
│ Дата: 16.02.2026 18:30                       │
└──────────────────────────────────────────────┘
```

## Примеры использования

### Сценарий 1: Увеличение лимита для корпорации

Компания хочет разрешить сотрудникам иметь конфиги для всех их устройств (телефон, ноутбук, планшет, домашний ПК).

**Действия:**
1. Администратор открывает "Настройки системы"
2. Изменяет значение с 2 на 4
3. Нажимает "Сохранить"
4. Пользователи теперь могут создать до 4 конфигов

### Сценарий 2: Временное ограничение

Нагрузка на VPN сервер высокая, нужно временно ограничить количество устройств.

**Действия:**
1. Администратор снижает лимит с 3 до 2
2. Пользователи с 3 конфигами **сохраняют** их (не удаляются)
3. Но не могут создать новые до удаления одного
4. Позже администратор возвращает лимит обратно

### Сценарий 3: Проверка текущего лимита через SQL

```sql
-- Получить текущий лимит
SELECT max_configs_per_user FROM system_settings WHERE id = 1;

-- Изменить лимит вручную (если нет доступа к админ-панели)
UPDATE system_settings
SET max_configs_per_user = 5,
    updated_at = CURRENT_TIMESTAMP,
    updated_by = 'admin'
WHERE id = 1;
```

## Логика проверки в коде

Реализовано в [`corpweb/backend/app/api/v1/configs.py:85-94`](backend/app/api/v1/configs.py#L85-L94). Перед созданием конфига читается `SystemSettings` из БД, затем вызывается `crud_config.count_active_by_user`. Если активных конфигов уже `>= max_configs_per_user` — поднимается HTTP 400 без INSERT.

## Преимущества подхода

### 1. Гибкость
- Изменение без перезапуска сервера
- Моментальное применение для новых конфигов
- Существующие конфиги пользователей не затрагиваются

### 2. Безопасность
- Application-level enforcement блокирует превышение лимита (HTTP 400)
- Проверка выполняется до любого INSERT
- UI дополнительно блокирует кнопку при достижении лимита

### 3. Масштабируемость
- Легко добавить новые настройки в `system_settings`
- Можно добавить лимиты по ролям (VIP пользователи)
- Возможность истории изменений (audit log)

### 4. Удобство
- Администратор не работает с кодом
- Понятный UI для изменения
- Немедленный эффект

## Будущие расширения

### Лимиты по ролям

```sql
ALTER TABLE users ADD COLUMN config_limit_override INTEGER;

-- Если у пользователя есть override - использовать его, иначе глобальный
```

### История изменений

```sql
CREATE TABLE settings_history (
    id SERIAL PRIMARY KEY,
    setting_name VARCHAR(50),
    old_value VARCHAR(100),
    new_value VARCHAR(100),
    changed_by VARCHAR(50),
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Уведомления пользователей

При изменении лимита администратором:
- Email уведомление: "Теперь вы можете создать до X конфигов"
- Уведомление в UI при следующем входе

## Миграция данных

При обновлении с предыдущих версий (если были):

```python
# Скрипт миграции
def migrate_to_system_settings(db: Session):
    # Проверяем, существует ли запись
    settings = db.query(SystemSettings).filter(SystemSettings.id == 1).first()

    if not settings:
        # Создаем с дефолтными значениями
        settings = SystemSettings(
            id=1,
            max_configs_per_user=2,
            updated_at=datetime.utcnow()
        )
        db.add(settings)
        db.commit()
        print("✅ SystemSettings initialized with default values")
```

## Troubleshooting

### Проблема: Пользователь не может создать конфиг

**Проверка 1:** Проверьте текущий лимит
```sql
SELECT * FROM system_settings WHERE id = 1;
```

**Проверка 2:** Посчитайте конфиги пользователя
```sql
SELECT COUNT(*) FROM vpn_configs
WHERE user_id = '<user_uuid>' AND is_active = TRUE;
```

**Решение:** Если count >= max_configs, пользователь должен удалить один конфиг

### Проблема: Лимит не применяется после изменения настроек

**Проверка:** Убедитесь, что значение сохранилось в БД
```sql
SELECT max_configs_per_user FROM system_settings WHERE id = 1;
```

**Решение:** Если значение отличается от ожидаемого — используйте PATCH-endpoint или обновите напрямую через SQL (см. Сценарий 3)

## Заключение

Система настраиваемых лимитов конфигураций предоставляет администратору гибкий инструмент управления ресурсами VPN сервера. Она балансирует между удобством пользователей (возможность подключения нескольких устройств) и контролем нагрузки на инфраструктуру.

---

**Вопросы?** Создайте issue в репозитории или обратитесь к разработчику.
