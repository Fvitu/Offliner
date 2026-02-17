"""
Flask application configuration.
No user data is stored by this service to respect user privacy.
"""

import os
from dotenv import load_dotenv

# Load environment variables from a .env file if present
load_dotenv()


class Config:
    """Base configuration for the application."""

    # Secret key for CSRF protection - MUST be set in production
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-key-change-in-production")

    # Spotify API configuration (used for resolving Spotify links)
    SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
    SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")

    # Session cookie settings
    SESSION_COOKIE_SECURE = True  # Only send cookies over HTTPS in production
    SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access to cookies
    SESSION_COOKIE_SAMESITE = "Lax"  # Helps mitigate CSRF

    # Flask upload size limit
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # Limit uploads to 16MB

    # ============================================
    # HTTP Rate Limiting (requests)
    # ============================================
    RATE_LIMIT_PER_DAY = int(os.getenv("RATE_LIMIT_PER_DAY", "200"))
    RATE_LIMIT_PER_HOUR = int(os.getenv("RATE_LIMIT_PER_HOUR", "50"))

    # Endpoint-specific rate limits (string form used by limiter)
    RATE_LIMIT_SEARCH = os.getenv("RATE_LIMIT_SEARCH", "10 per minute")
    RATE_LIMIT_PLAYLIST = os.getenv("RATE_LIMIT_PLAYLIST", "30 per minute")
    RATE_LIMIT_MEDIA_INFO = os.getenv("RATE_LIMIT_MEDIA_INFO", "60 per minute")
    RATE_LIMIT_DOWNLOAD = os.getenv("RATE_LIMIT_DOWNLOAD", "10 per minute")

    # ============================================
    # Per-user download limits
    # ============================================
    # Max number of files (video/audio) a user can download
    MAX_DOWNLOADS_PER_HOUR = int(os.getenv("MAX_DOWNLOADS_PER_HOUR", "10"))
    MAX_DOWNLOADS_PER_DAY = int(os.getenv("MAX_DOWNLOADS_PER_DAY", "50"))

    # Max total duration of content downloaded by a user (minutes)
    MAX_DURATION_PER_HOUR = int(os.getenv("MAX_DURATION_PER_HOUR", "120"))  # 2 hours
    MAX_DURATION_PER_DAY = int(os.getenv("MAX_DURATION_PER_DAY", "600"))  # 10 hours

    # ============================================
    # Individual content limits
    # ============================================
    # Maximum allowed duration for a single media item (minutes)
    MAX_CONTENT_DURATION = int(os.getenv("MAX_CONTENT_DURATION", "60"))  # 1 hour

    # Maximum number of items allowed in a playlist
    MAX_PLAYLIST_ITEMS = int(os.getenv("MAX_PLAYLIST_ITEMS", "100"))

    # ============================================
    # Redis configuration (used by RQ task queue & progress store)
    # ============================================
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


class DevelopmentConfig(Config):
    """Development configuration."""

    DEBUG = True
    SESSION_COOKIE_SECURE = False  # Allow HTTP during local development


class ProductionConfig(Config):
    """Production configuration."""

    DEBUG = False
    # In production ensure SECRET_KEY is properly configured


class TestingConfig(Config):
    """Testing configuration."""

    TESTING = True
    DEBUG = True


# Available configuration mappings
config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}


def get_config():
    """Return the configuration class based on the FLASK_ENV environment variable."""
    env = os.getenv("FLASK_ENV", "development")
    return config.get(env, config["default"])
