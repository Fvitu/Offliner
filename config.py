"""
Configuración de la aplicación Flask.
Sin almacenamiento de datos de usuario - respetando la privacidad.
"""

import os
from dotenv import load_dotenv

# Cargar variables de entorno desde archivo .env
load_dotenv()


class Config:
    """Configuración base de la aplicación."""

    # Clave secreta para CSRF - DEBE ser configurada en producción
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-key-change-in-production")

    # Configuración de Spotify (para metadata)
    SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
    SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")

    # Configuración de sesión
    SESSION_COOKIE_SECURE = True  # Solo enviar cookies por HTTPS en producción
    SESSION_COOKIE_HTTPONLY = True  # Prevenir acceso JavaScript a cookies
    SESSION_COOKIE_SAMESITE = "Lax"  # Prevenir ataques CSRF

    # Configuración de Flask
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # Limitar tamaño de uploads a 16MB

    # ============================================
    # Límites de Rate Limiting (Peticiones HTTP)
    # ============================================
    RATE_LIMIT_PER_DAY = int(os.getenv("RATE_LIMIT_PER_DAY", "200"))
    RATE_LIMIT_PER_HOUR = int(os.getenv("RATE_LIMIT_PER_HOUR", "50"))

    # Límites específicos por endpoint
    RATE_LIMIT_SEARCH = os.getenv("RATE_LIMIT_SEARCH", "10 per minute")
    RATE_LIMIT_PLAYLIST = os.getenv("RATE_LIMIT_PLAYLIST", "30 per minute")
    RATE_LIMIT_MEDIA_INFO = os.getenv("RATE_LIMIT_MEDIA_INFO", "60 per minute")
    RATE_LIMIT_DOWNLOAD = os.getenv("RATE_LIMIT_DOWNLOAD", "10 per minute")

    # ============================================
    # Límites de Descargas por Usuario
    # ============================================
    # Límite de archivos (videos/audios) descargados por usuario
    MAX_DOWNLOADS_PER_HOUR = int(os.getenv("MAX_DOWNLOADS_PER_HOUR", "10"))
    MAX_DOWNLOADS_PER_DAY = int(os.getenv("MAX_DOWNLOADS_PER_DAY", "50"))

    # Límite de duración total de contenido descargado (en minutos)
    MAX_DURATION_PER_HOUR = int(os.getenv("MAX_DURATION_PER_HOUR", "120"))  # 2 horas
    MAX_DURATION_PER_DAY = int(os.getenv("MAX_DURATION_PER_DAY", "600"))  # 10 horas

    # ============================================
    # Límites de Contenido Individual
    # ============================================
    # Duración máxima permitida para un video o audio individual (en minutos)
    MAX_CONTENT_DURATION = int(os.getenv("MAX_CONTENT_DURATION", "60"))  # 1 hora

    # Límite máximo de items en una playlist
    MAX_PLAYLIST_ITEMS = int(os.getenv("MAX_PLAYLIST_ITEMS", "100"))


class DevelopmentConfig(Config):
    """Configuración para desarrollo."""

    DEBUG = True
    SESSION_COOKIE_SECURE = False  # Permitir HTTP en desarrollo


class ProductionConfig(Config):
    """Configuración para producción."""

    DEBUG = False
    # En producción, asegurarse de que SECRET_KEY esté configurada


class TestingConfig(Config):
    """Configuración para testing."""

    TESTING = True
    DEBUG = True


# Diccionario de configuraciones disponibles
config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}


def get_config():
    """Obtiene la configuración según el entorno."""
    env = os.getenv("FLASK_ENV", "development")
    return config.get(env, config["default"])
