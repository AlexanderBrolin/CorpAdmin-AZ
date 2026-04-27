"""
Google OAuth2 integration
"""
from authlib.integrations.starlette_client import OAuth
from app.config import settings

oauth = OAuth()

if settings.is_google_oauth_configured():
    oauth.register(
        name='google',
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={
            'scope': 'openid email profile',
        },
    )


def get_google_client():
    """Get the registered Google OAuth client, or None if not configured."""
    return oauth.google if settings.is_google_oauth_configured() else None
