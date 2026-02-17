"""
A web application to download music from YouTube and Spotify.
No user data storage - respecting privacy.
"""

import os
import logging
import shutil
from logging.handlers import RotatingFileHandler

from flask import Flask
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import config
from routes import register_routes, register_error_handlers, init_rq

# Base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def create_app(config_name="development"):
    """
    Factory function to create the Flask application.

    Args:
        config_name: Configuration name to use ('development', 'production', 'testing')

    Returns:
        Flask: Configured application instance
    """
    app = Flask(__name__)

    # Load configuration
    app.config.from_object(config[config_name])

    # Setup logging
    setup_logging(app)

    # Initial cleanup of temporary directories
    cleanup_temp_dirs(app)

    # Initialize extensions
    CSRFProtect(app)

    # Rate limiting to prevent abuse - using configuration values
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=[
            f"{app.config['RATE_LIMIT_PER_DAY']} per day",
            f"{app.config['RATE_LIMIT_PER_HOUR']} per hour",
        ],
        storage_uri="memory://",
    )

    # Initialise Redis connection and RQ task queue.
    # The same REDIS_URL is shared by the progress store in logic.py and the
    # RQ queue in routes.py so that the Flask app and the RQ worker both
    # read/write progress via the same Redis instance.
    redis_url = app.config.get("REDIS_URL", "redis://localhost:6379/0")

    from logic import init_redis as _init_logic_redis

    _init_logic_redis(redis_url)
    init_rq(redis_url)

    app.logger.info(f"Redis & RQ initialised ({redis_url})")

    # Register routes
    register_routes(app, limiter)

    # Register error handlers
    register_error_handlers(app)

    return app


def setup_logging(app):
    """Configures the application logging system."""
    # Remove duplicate Flask handlers if they exist
    if app.logger.hasHandlers():
        app.logger.handlers.clear()

    # Create logs directory if it doesn't exist
    log_dir = os.path.join(BASE_DIR, "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Configure log format
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # File handler with UTF-8 encoding to support emojis
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"),
        maxBytes=10240000,  # 10MB
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    # Console handler for development with UTF-8 encoding
    if app.debug:
        import sys

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.DEBUG)
        if hasattr(console_handler.stream, "reconfigure"):
            console_handler.stream.reconfigure(encoding="utf-8", errors="replace")
        app.logger.addHandler(console_handler)

    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info("Offliner application started")


def cleanup_temp_dirs(app):
    """Cleans the temporary downloads directory at startup."""
    try:
        # Clean both Temp and Zip folders inside the downloads directory
        downloads_temp = os.path.join(BASE_DIR, "Downloads", "Temp")
        downloads_zip = os.path.join(BASE_DIR, "Downloads", "Zip")

        for path in (downloads_temp, downloads_zip):
            if os.path.exists(path):
                # Only remove and log if the directory actually contains files/subdirs
                has_contents = False
                if os.path.isdir(path):
                    try:
                        with os.scandir(path) as it:
                            for _ in it:
                                has_contents = True
                                break
                    except Exception:
                        # If we can't scan the dir, fall back to listing
                        try:
                            has_contents = len(os.listdir(path)) > 0
                        except Exception:
                            has_contents = False
                else:
                    # If it's not a directory (unexpected), treat it as content
                    has_contents = True

                if has_contents:
                    shutil.rmtree(path, ignore_errors=True)
                    app.logger.info(f"Initial cleanup: {path} deleted.")

        # Recreate empty Temp directory
        os.makedirs(downloads_temp, exist_ok=True)
        os.makedirs(downloads_zip, exist_ok=True)
    except Exception as e:
        app.logger.error(f"Error cleaning temporary directories: {e}")


# Create application instance
app = create_app(os.getenv("FLASK_ENV", "development"))


if __name__ == "__main__":
    from services import ensure_services

    # Start Redis server and RQ worker before accepting requests.
    # In debug mode with the reloader, only the *parent* process starts
    # services; the reloader children skip this to avoid duplicate processes.
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        redis_url = app.config.get("REDIS_URL", "redis://localhost:6379/0")
        ensure_services(redis_url)

    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
