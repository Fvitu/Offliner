"""
Manages Redis server and RQ worker lifecycle for Offliner.

Automatically starts a local Redis instance (if not already running) and
launches an RQ worker process when the application is run directly via
``python app.py``.

Cross-platform: works on Windows, Linux, and macOS.
"""

from __future__ import annotations

import atexit
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse
import signal

logger = logging.getLogger(__name__)

# Ensure services log messages are visible (console) even before Flask
# configures the app logger.
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Module-level handles so atexit can clean up.
_redis_process: subprocess.Popen | None = None
_worker_process: subprocess.Popen | None = None
_stopping = False


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Return *True* if *host:port* is accepting TCP connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def _parse_redis_url(redis_url: str) -> tuple[str, int]:
    """Extract host and port from a Redis URL."""
    parsed = urlparse(redis_url)
    return parsed.hostname or "localhost", parsed.port or 6379


def _popen_kwargs() -> dict:
    """Return platform-specific keyword arguments for ``subprocess.Popen``."""
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if platform.system() == "Windows":
        # Prevent a visible console window from appearing.
        # Create a new process group so we can signal/terminate the whole group.
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        kwargs["creationflags"] = creation
    else:
        # On Unix create a new session so children won't be reparented unexpectedly
        kwargs["start_new_session"] = True
    return kwargs


def _find_redis_executable() -> str | None:
    """Locate the ``redis-server`` binary.

    Search order:
        1. Same directory as this file (the application root).
        2. System ``PATH``.
    """
    is_windows = platform.system() == "Windows"
    name = "redis-server.exe" if is_windows else "redis-server"

    # 1. Application directory
    local = os.path.join(BASE_DIR, name)
    if os.path.isfile(local):
        return local

    # 2. System PATH
    found = shutil.which(name)
    if found:
        return found

    return None


# ------------------------------------------------------------------
# Redis
# ------------------------------------------------------------------


def start_redis(redis_url: str) -> bool:
    """Start a Redis server if none is listening on the configured port.

    Returns *True* when Redis is available (either already running or
    successfully started), *False* otherwise.
    """
    global _redis_process  # noqa: PLW0603

    host, port = _parse_redis_url(redis_url)

    if _is_port_open(host, port):
        logger.info("Redis already listening on %s:%s", host, port)
        return True

    exe = _find_redis_executable()
    if exe is None:
        logger.error(
            "redis-server executable not found.  "
            "Place it in '%s' or install Redis and ensure it is on PATH.",
            BASE_DIR,
        )
        return False

    logger.info("Starting Redis server: %s --port %s", exe, port)

    try:
        _redis_process = subprocess.Popen(
            [exe, "--port", str(port)],
            **_popen_kwargs(),
        )
    except Exception:
        logger.exception("Failed to launch redis-server")
        return False

    # Wait for Redis to accept connections (up to 5 s).
    for _ in range(50):
        if _redis_process.poll() is not None:
            logger.error(
                "redis-server exited immediately (code %s)",
                _redis_process.returncode,
            )
            _redis_process = None
            return False
        if _is_port_open(host, port):
            logger.info("Redis server ready (PID %s)", _redis_process.pid)
            return True
        time.sleep(0.1)

    logger.error("Redis server did not become ready within 5 seconds")
    return False


# ------------------------------------------------------------------
# RQ Worker
# ------------------------------------------------------------------


def start_worker(redis_url: str) -> bool:
    """Launch an RQ worker in a child process.

    On Windows the worker uses ``rq.SimpleWorker`` (does not require
    ``fork()``).  On other platforms the standard ``rq.Worker`` is used.
    """
    global _worker_process  # noqa: PLW0603

    worker_script = os.path.join(BASE_DIR, "rq_worker.py")

    cmd = [sys.executable, worker_script, redis_url]

    kwargs = _popen_kwargs()
    kwargs["cwd"] = BASE_DIR

    try:
        _worker_process = subprocess.Popen(cmd, **kwargs)
        logger.info("RQ worker started (PID %s)", _worker_process.pid)
        return True
    except Exception:
        logger.exception("Failed to start RQ worker")
        return False


# ------------------------------------------------------------------
# Lifecycle
# ------------------------------------------------------------------


def stop_services() -> None:
    """Terminate any child processes that *we* started."""
    global _redis_process, _worker_process  # noqa: PLW0603
    global _stopping
    if _stopping:
        return
    _stopping = True

    for label, proc in [("RQ worker", _worker_process), ("Redis", _redis_process)]:
        if proc is not None and proc.poll() is None:
            logger.info("Stopping %s (PID %s)...", label, proc.pid)
            try:
                # Try a graceful terminate first
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    logger.exception("Failed to kill %s", label)
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass
            except Exception:
                logger.exception("Error stopping %s", label)
            logger.info("%s stopped.", label)

    _worker_process = None
    _redis_process = None


def ensure_services(redis_url: str = "redis://localhost:6379/0") -> None:
    """Start Redis and an RQ worker (if needed) and register cleanup."""
    if start_redis(redis_url):
        start_worker(redis_url)
    else:
        logger.error(
            "Redis is not available — RQ worker will NOT be started.  "
            "Downloads will not work until Redis is running."
        )
    # Register cleanup handlers
    atexit.register(stop_services)

    # Signals: ensure we catch typical termination signals and shut down children.
    def _handle_signal(signum, frame=None):
        logger.info("Received signal %s, shutting down services...", signum)
        try:
            stop_services()
        finally:
            # Re-raise KeyboardInterrupt for normal Ctrl+C behaviour in main process
            try:
                if signum == signal.SIGINT:
                    raise KeyboardInterrupt
            except Exception:
                pass

    # Register available signals (SIGINT, SIGTERM). On Windows also handle SIGBREAK.
    try:
        signal.signal(signal.SIGINT, _handle_signal)
    except Exception:
        pass
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except Exception:
        pass
    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, _handle_signal)
        except Exception:
            pass
