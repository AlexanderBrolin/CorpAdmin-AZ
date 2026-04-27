"""
Tests for authentication endpoints
"""
from tests.conftest import auth_header


class TestLogin:
    def test_login_success(self, client, admin_user):
        response = client.post("/api/v1/auth/login", json={
            "login": "admin",
            "password": "adminpass"
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0

    def test_login_with_email(self, client, admin_user):
        response = client.post("/api/v1/auth/login", json={
            "login": "admin@test.com",
            "password": "adminpass"
        })
        assert response.status_code == 200

    def test_login_wrong_password(self, client, admin_user):
        response = client.post("/api/v1/auth/login", json={
            "login": "admin",
            "password": "wrong"
        })
        assert response.status_code == 401

    def test_login_nonexistent_user(self, client):
        response = client.post("/api/v1/auth/login", json={
            "login": "nobody",
            "password": "pass"
        })
        assert response.status_code == 401

    def test_login_blocked_user(self, client, db, regular_user):
        regular_user.is_active = False
        db.commit()
        response = client.post("/api/v1/auth/login", json={
            "login": "testuser",
            "password": "userpass"
        })
        assert response.status_code == 403
        assert "blocked" in response.json()["detail"].lower()


class TestMe:
    def test_me_authenticated(self, client, admin_user, admin_token, system_settings):
        response = client.get("/api/v1/auth/me", headers=auth_header(admin_token))
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"
        assert data["config_count"] == 0
        assert data["max_configs"] == 2

    def test_me_unauthenticated(self, client):
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_me_invalid_token(self, client):
        response = client.get("/api/v1/auth/me", headers=auth_header("invalid-token"))
        assert response.status_code == 401


class TestChangePassword:
    def test_change_password_success(self, client, regular_user, user_token):
        response = client.post("/api/v1/auth/change-password",
            headers=auth_header(user_token),
            json={
                "current_password": "userpass",
                "new_password": "newpass123"
            }
        )
        assert response.status_code == 200

        # Verify new password works
        response = client.post("/api/v1/auth/login", json={
            "login": "testuser",
            "password": "newpass123"
        })
        assert response.status_code == 200

    def test_change_password_wrong_current(self, client, regular_user, user_token):
        response = client.post("/api/v1/auth/change-password",
            headers=auth_header(user_token),
            json={
                "current_password": "wrong",
                "new_password": "newpass123"
            }
        )
        assert response.status_code == 400


class TestLogout:
    def test_logout(self, client, admin_user, admin_token):
        response = client.post("/api/v1/auth/logout", headers=auth_header(admin_token))
        assert response.status_code == 200


class TestAuthConfig:
    """GET /api/v1/auth/config — public endpoint exposing auth feature flags."""

    def test_config_endpoint_is_public(self, client):
        response = client.get("/api/v1/auth/config")
        assert response.status_code == 200

    def test_google_oauth_enabled_when_client_id_configured(self, client):
        # conftest sets GOOGLE_CLIENT_ID="test-client-id" → enabled
        response = client.get("/api/v1/auth/config")
        assert response.json() == {"google_oauth_enabled": True}

    def test_google_oauth_disabled_when_client_id_is_disabled_sentinel(
        self, client, monkeypatch
    ):
        from app.config import settings
        monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "disabled")
        response = client.get("/api/v1/auth/config")
        assert response.json() == {"google_oauth_enabled": False}

    def test_google_oauth_disabled_when_client_id_empty(
        self, client, monkeypatch
    ):
        from app.config import settings
        monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")
        response = client.get("/api/v1/auth/config")
        assert response.json() == {"google_oauth_enabled": False}


class TestGoogleOAuthGuards:
    """When OAuth is not configured, /google* routes must 404 instead of crashing."""

    def test_google_login_returns_404_when_disabled(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "disabled")
        response = client.get("/api/v1/auth/google", follow_redirects=False)
        assert response.status_code == 404

    def test_google_callback_returns_404_when_disabled(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "disabled")
        response = client.get("/api/v1/auth/google/callback", follow_redirects=False)
        assert response.status_code == 404
