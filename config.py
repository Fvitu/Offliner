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
