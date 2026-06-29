# 406-ФЗ auth compliance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development for Task 1. Steps use checkbox (`- [ ]`) syntax. Prod steps (Task 5) are state-changing on production — STOP for explicit user approval before each.

**Goal:** Дать роли `admin` ставить логин+пароль ЛЮБОМУ юзеру (включая google), затем отключить вход через Google OAuth — для соответствия ФЗ 406-ФЗ.

**Architecture:** Минимальное расширение существующего. Backend: `UserUpdate` += `password`, `update_password` флипает `auth_provider` google→local, `PUT /users/{id}` дергает его. Frontend: новая Edit-модалка на DetailPage (формы редактирования сейчас нет), переиспользует уже объявленный `adminApi.updateUser`. Google off = config-флаг в прод `.env`. Без миграций (БД на `0007`), без новых эндпоинтов.

**Tech Stack:** Python 3.11/3.13 + FastAPI + pytest (backend), React + TS + Tailwind + Vite (frontend).

**Spec:** `docs/superpowers/specs/2026-06-29-406fz-auth-compliance-design.md`

**Epic:** CorpAdmin-AZ-dbo

**Working tree:** создать worktree `feature/406fz-auth-compliance` в `.worktrees/406fz-auth` (Task 0).

---

## File Structure

| File | Action |
|---|---|
| `corpweb/backend/app/schemas/user.py` | Modify — `UserUpdate` += `password` (lines 21-25) |
| `corpweb/backend/app/crud/user.py` | Modify — `update_password` флипает provider (lines 156-161) |
| `corpweb/backend/app/api/v1/admin.py` | Modify — `update_user` ставит пароль если передан (lines 105-143) |
| `corpweb/backend/tests/test_admin.py` | Add — 4 теста в `class TestAdminUsers` |
| `corpweb/frontend/src/types/index.ts` | Modify — `UserUpdateRequest` += `password?` (lines 25-29) |
| `corpweb/frontend/src/pages/AdminUserDetailPage.tsx` | Modify — Edit-кнопка + модалка |

No new files. No DB migrations.

---

## Task 0: Worktree

- [ ] **0.1** Создать изолированный worktree (skill: superpowers:using-git-worktrees):

```bash
cd /home/brolin/Documents/ITSS/AdminAZWG/CorpAdmin-AZ
git worktree add -b feature/406fz-auth-compliance .worktrees/406fz-auth CorpAdmin
```

- [ ] **0.2** Claim бид: `bd update CorpAdmin-AZ-dbo --claim` (если нужны под-таски — создать, но фича маленькая, ведём под эпиком).

Все дальнейшие пути — внутри `.worktrees/406fz-auth/`.

---

## Task 1: Backend — admin set-password + provider flip (TDD)

**Files:** `app/schemas/user.py`, `app/crud/user.py`, `app/api/v1/admin.py`, `tests/test_admin.py`

- [ ] **1.1 RED — написать 4 падающих теста.** Добавить в `tests/test_admin.py` внутрь `class TestAdminUsers` (фикстуры `client, admin_user, admin_token, regular_user, db`; хелпер `auth_header` уже импортирован):

```python
    def test_update_user_sets_password(self, client, admin_user, admin_token, regular_user, db):
        resp = client.put(
            f"/api/v1/admin/users/{regular_user.id}",
            headers=auth_header(admin_token),
            json={"password": "newsecret1"},
        )
        assert resp.status_code == 200
        # authenticate() must now succeed with the new password
        from app.crud import user as crud_user
        assert crud_user.authenticate(db, regular_user.email, "newsecret1") is not None

    def test_update_user_password_localizes_google_user(self, client, admin_user, admin_token, db):
        from app.crud import user as crud_user
        g = crud_user.create_google_user(db, "ivan@corp.com", "google-sub-123")
        resp = client.put(
            f"/api/v1/admin/users/{g.id}",
            headers=auth_header(admin_token),
            json={"password": "newsecret1"},
        )
        assert resp.status_code == 200
        assert resp.json()["auth_provider"] == "local"
        assert crud_user.authenticate(db, "ivan@corp.com", "newsecret1") is not None

    def test_update_user_short_password_rejected(self, client, admin_user, admin_token, regular_user):
        resp = client.put(
            f"/api/v1/admin/users/{regular_user.id}",
            headers=auth_header(admin_token),
            json={"password": "short"},
        )
        assert resp.status_code == 422

    def test_update_user_without_password_keeps_hash(self, client, admin_user, admin_token, regular_user, db):
        before = regular_user.password_hash
        resp = client.put(
            f"/api/v1/admin/users/{regular_user.id}",
            headers=auth_header(admin_token),
            json={"username": "renamed"},
        )
        assert resp.status_code == 200
        db.refresh(regular_user)
        assert regular_user.password_hash == before
        assert regular_user.auth_provider == "local"
        assert regular_user.username == "renamed"
```

- [ ] **1.2 Verify RED:**

```bash
cd .worktrees/406fz-auth/corpweb/backend
python3 -m pytest tests/test_admin.py::TestAdminUsers -k "password or localizes" -v
```

Ожидание: FAIL — `UserUpdate` не знает `password` (поле игнорится → пароль не меняется → authenticate=None; provider не флипается).

- [ ] **1.3 GREEN — schema.** `app/schemas/user.py`, `UserUpdate`:

```python
class UserUpdate(BaseModel):
    """Admin updates user fields"""
    email: Optional[str] = Field(None, max_length=255)
    username: Optional[str] = Field(None, min_length=2, max_length=50, pattern=r'^[a-zA-Z0-9._-]+$')
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=6, max_length=128)
```

- [ ] **1.4 GREEN — crud flip.** `app/crud/user.py`, `update_password`:

```python
def update_password(db: Session, user: User, new_password: str) -> User:
    """Update user password. Localizes Google users (406-FZ: admin sets a
    local password so the user can log in without Google OAuth)."""
    user.password_hash = get_password_hash(new_password)
    if user.auth_provider == "google":
        user.auth_provider = "local"
    db.commit()
    db.refresh(user)
    return user
```

- [ ] **1.5 GREEN — endpoint.** `app/api/v1/admin.py`, `update_user`, после блока `user = crud_user.update_user(...)` (≈ line 136-141) добавить:

```python
    if data.password is not None:
        crud_user.update_password(db, user, data.password)
```

- [ ] **1.6 Verify GREEN + регрессия:**

```bash
python3 -m pytest tests/test_admin.py -v 2>&1 | tail -20
python3 -m pytest -q 2>&1 | tail -5
```

Ожидание: 4 новых PASS; весь backend-сьют зелёный (был 350 passed → 354).

- [ ] **1.7 Commit:**

```bash
cd /home/brolin/Documents/ITSS/AdminAZWG/CorpAdmin-AZ/.worktrees/406fz-auth
git add corpweb/backend/app/schemas/user.py corpweb/backend/app/crud/user.py corpweb/backend/app/api/v1/admin.py corpweb/backend/tests/test_admin.py
git commit -m "feat(admin): set password for any user incl. Google (localizes provider) (CorpAdmin-AZ-dbo)"
```

---

## Task 2: Frontend — Edit-модалка с паролем

**Files:** `src/types/index.ts`, `src/pages/AdminUserDetailPage.tsx`

- [ ] **2.1 Type.** `src/types/index.ts`, `UserUpdateRequest`:

```typescript
export interface UserUpdateRequest {
  email?: string
  username?: string
  is_active?: boolean
  password?: string
}
```

- [ ] **2.2 Edit-модалка на DetailPage.** В `AdminUserDetailPage.tsx`:

(a) добавить state рядом с прочими (≈ line 31):

```tsx
  const [showEdit, setShowEdit] = useState(false)
  const [editEmail, setEditEmail] = useState('')
  const [editUsername, setEditUsername] = useState('')
  const [editPassword, setEditPassword] = useState('')
  const [saving, setSaving] = useState(false)
```

(b) handler (рядом с `handleToggleBlock`):

```tsx
  const openEdit = () => {
    if (!user) return
    setEditEmail(user.email)
    setEditUsername(user.username)
    setEditPassword('')
    setError('')
    setShowEdit(true)
  }

  const handleEdit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!userId) return
    setSaving(true)
    setError('')
    try {
      await adminApi.updateUser(userId, {
        email: editEmail,
        username: editUsername,
        ...(editPassword ? { password: editPassword } : {}),
      })
      setShowEdit(false)
      await loadData()
    } catch (err: unknown) {
      const message = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || 'Ошибка сохранения'
      setError(message)
    } finally {
      setSaving(false)
    }
  }
```

(c) кнопка «Редактировать» рядом с block-кнопкой (≈ line 205-224, в блоке action-кнопок шапки):

```tsx
            <button
              onClick={openEdit}
              className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition font-medium"
            >
              Редактировать
            </button>
```

(d) модалка (по образцу Create-модалки из `AdminUsersPage.tsx:288-346`) — вставить перед закрывающим тегом компонента:

```tsx
      {showEdit && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl max-w-md w-full p-6">
            <h2 className="text-xl font-bold text-gray-900 mb-4">Редактировать пользователя</h2>
            <form onSubmit={handleEdit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
                <input type="email" value={editEmail} onChange={(e) => setEditEmail(e.target.value)}
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none" required />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Логин</label>
                <input type="text" value={editUsername} onChange={(e) => setEditUsername(e.target.value)}
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none" required />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Новый пароль</label>
                <input type="password" value={editPassword} onChange={(e) => setEditPassword(e.target.value)}
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
                  placeholder="Оставьте пустым, чтобы не менять" minLength={6} />
              </div>
              <div className="flex gap-3 pt-2">
                <button type="button" onClick={() => setShowEdit(false)}
                  className="flex-1 px-4 py-2.5 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition font-medium">Отмена</button>
                <button type="submit" disabled={saving}
                  className="flex-1 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2.5 rounded-lg transition font-medium disabled:opacity-50 flex items-center justify-center gap-2">
                  {saving && <Loader2 className="w-4 h-4 animate-spin" />}Сохранить</button>
              </div>
            </form>
          </div>
        </div>
      )}
```

- [ ] **2.3 Verify build:**

```bash
cd /home/brolin/Documents/ITSS/AdminAZWG/CorpAdmin-AZ/.worktrees/406fz-auth/corpweb/frontend
npm install --silent && npx tsc --noEmit && npm run build 2>&1 | tail -10
```

Ожидание: tsc без ошибок, `dist/assets/index-*.js` пересобран.

- [ ] **2.4 Commit:**

```bash
cd /home/brolin/Documents/ITSS/AdminAZWG/CorpAdmin-AZ/.worktrees/406fz-auth
git add corpweb/frontend/src/types/index.ts corpweb/frontend/src/pages/AdminUserDetailPage.tsx
git commit -m "feat(frontend): admin edit-user modal with optional password (CorpAdmin-AZ-dbo)"
```

---

## Task 3: Staging deploy (bb) + ручная проверка

> Не прод. brolin имеет sudo NOPASSWD на bb.

- [ ] **3.1** Backend на bb:

```bash
rsync -avz -e "ssh -p 2201" .worktrees/406fz-auth/corpweb/backend/app/ brolin@bb.azfi.ru:/tmp/cw-app/
ssh -p 2201 brolin@bb.azfi.ru 'sudo rsync -a --delete /tmp/cw-app/ /opt/corpweb/backend/app/ && sudo systemctl restart corpweb-backend && sleep 2 && systemctl is-active corpweb-backend'
```

- [ ] **3.2** Frontend на bb:

```bash
rsync -avz -e "ssh -p 2201" .worktrees/406fz-auth/corpweb/frontend/dist/ brolin@bb.azfi.ru:/tmp/cw-dist/
ssh -p 2201 brolin@bb.azfi.ru 'sudo rsync -a --delete /tmp/cw-dist/ /opt/corpweb/frontend/ && ls /opt/corpweb/frontend/assets/ | head'
```

- [ ] **3.3** Ручная проверка на bb (skill: superpowers:verification-before-completion):
  1. Залогиниться админом, открыть карточку юзера → «Редактировать» → сменить пароль обычному юзеру → выйти/войти под ним новым паролем — успех.
  2. Если на bb есть google-юзер (или создать через тестовый OAuth/SQL) — поставить пароль, убедиться, что карточка показывает «Local» и вход проходит.
  3. Пустое поле пароля → пароль не меняется, email/username обновляются.

---

## Task 4: Merge в CorpAdmin

- [ ] **4.1** skill: superpowers:requesting-code-review по диффу ветки.
- [ ] **4.2** skill: superpowers:finishing-a-development-branch — PR `feature/406fz-auth-compliance → CorpAdmin` (или fast-forward merge), затем `git push origin CorpAdmin`. Правило: на прод катим ТОЛЬКО из `CorpAdmin`.
- [ ] **4.3** `git worktree remove .worktrees/406fz-auth` после merge.

---

## Task 5: Прод (wgfi2) — деплой, затем отключение Google ⚠️ STATE-CHANGING

> wgfi2 = ПРОДАКШН. Каждый шаг — STOP, явный approval пользователя. brolin имеет sudo NOPASSWD на wgfi2 (verified). Порядок критичен: сначала фича (саппорт получает инструмент), ТОЛЬКО ПОТОМ гасим Google.

- [ ] **5.1** С локального `CorpAdmin` (после merge) — backend на прод:

```bash
git -C /home/brolin/Documents/ITSS/AdminAZWG/CorpAdmin-AZ checkout CorpAdmin && git pull
rsync -avz -e "ssh -p 2201" corpweb/backend/app/ brolin@wgfi2.p4i.ru:/tmp/cw-app/
ssh -p 2201 brolin@wgfi2.p4i.ru 'sudo rsync -a --delete /tmp/cw-app/ /opt/corpweb/backend/app/ && sudo systemctl restart corpweb-backend && sleep 2 && systemctl is-active corpweb-backend'
```

(БД уже на `0007` — `alembic upgrade` не нужен.)

- [ ] **5.2** Frontend на прод (собрать из CorpAdmin локально → rsync `dist/`):

```bash
cd corpweb/frontend && npm install --silent && npm run build && cd ../..
rsync -avz -e "ssh -p 2201" corpweb/frontend/dist/ brolin@wgfi2.p4i.ru:/tmp/cw-dist/
ssh -p 2201 brolin@wgfi2.p4i.ru 'sudo rsync -a --delete /tmp/cw-dist/ /opt/corpweb/frontend/'
```

- [ ] **5.3** Проверка на проде ДО отключения Google: админом сменить пароль реальному google-юзеру (с его согласия / тестовому) → юзер входит новым паролем. Убедиться, что инструмент саппорта работает.

- [ ] **5.4** ⚠️ Отключить Google OAuth (после подтверждения 5.3):

```bash
ssh -p 2201 brolin@wgfi2.p4i.ru 'sudo sed -i "s/^GOOGLE_CLIENT_ID=.*/GOOGLE_CLIENT_ID=disabled/" /opt/corpweb/backend/.env && sudo systemctl restart corpweb-backend && sleep 2 && curl -s localhost:8000/api/v1/auth/config'
```

Ожидание: `{"google_oauth_enabled": false}`. Проверить в браузере: кнопка «Войти через Google» исчезла, `/api/v1/auth/google` → 404.

- [ ] **5.5** Закрыть эпик: `bd close CorpAdmin-AZ-dbo --reason "Admin set-password shipped + Google OAuth disabled on prod per 406-FZ"`.

---

## Self-review checklist

- **Spec coverage:** 2.1 (Google off) → Task 5.4; 2.2 backend → Task 1; 2.2 frontend (edit form) → Task 2; критический порядок выкатки → Task 5 (фича до отключения Google).
- **Verified against code:** `UserUpdate` (нет `password`), `update_password` (есть, флипа нет), `update_user` endpoint (нет пароля), `authenticate` (только хэш), `/change-password` флип = no-op (provider уже local), фронт `updateUser` не вызывается → нужна модалка, `is_google_oauth_configured()` флаг.
- **No placeholders:** все шаги — код или точные команды.
- **Tests:** 4 backend RED→GREEN; фронт — tsc + ручная (UI-проект без компонентных тестов).
- **Prod safety:** Task 5 помечен STATE-CHANGING, по-шаговый approval, деплой только из `CorpAdmin`.
