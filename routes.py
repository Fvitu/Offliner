"""
Flask routes for Offliner.
Contains all @app.route endpoints.
"""

import os
import uuid
import json
import time
import threading
import shutil
import logging
import yt_dlp

import redis as _redis
from rq import Queue as _RQQueue

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    send_file,
    Response,
    stream_with_context,
)
from datetime import datetime, timedelta

from models.ModelFile import ModelFile, DEFAULT_CONFIG
from config import get_config

# Base directory for the application
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
logger = logging.getLogger(__name__)

# Get configuration
app_config = get_config()

# ============================================
# Redis & RQ Queue — initialised once per process
# ============================================
# The Redis URL comes from the app config; the connection is created eagerly so
# that both the progress store and the RQ queue share the same Redis instance.

_redis_conn: _redis.Redis | None = None
_task_queue: _RQQueue | None = None


def init_rq(redis_url: str | None = None) -> _RQQueue:
    """Create (or return the cached) RQ Queue backed by Redis.

    Called from ``app.py`` during application startup.
    """
    global _redis_conn, _task_queue  # noqa: PLW0603
    url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    if _redis_conn is None:
        _redis_conn = _redis.Redis.from_url(url)
    if _task_queue is None:
        _task_queue = _RQQueue(connection=_redis_conn)
    return _task_queue


def get_rq_queue() -> _RQQueue:
    """Return the module-level RQ queue, initialising lazily if needed."""
    if _task_queue is None:
        init_rq()
    assert _task_queue is not None
    return _task_queue


# ============================================
# Progress Manager — backed by DownloadProgressStore in logic.py
# ============================================
# Imported lazily to avoid circular imports.
# DownloadProgressStore is the thread-safe global store for SSE progress.


def _get_progress_store():
    """Lazy import of DownloadProgressStore to avoid circular imports."""
    from logic import DownloadProgressStore

    return DownloadProgressStore


# ============================================
# Download Tracker (tracks downloads per user/IP)
# ============================================


class DownloadTracker:
    """Thread-safe tracker for user downloads with hourly and daily limits."""

    def __init__(self):
        self._downloads = {}  # {ip: {'hourly': [...], 'daily': [...]}}
        self._lock = threading.Lock()

    def _clean_old_entries(self, ip):
        """Removes entries older than 1 hour and 1 day."""
        now = datetime.utcnow()
        one_hour_ago = now - timedelta(hours=1)
        one_day_ago = now - timedelta(days=1)

        if ip in self._downloads:
            # Clean hourly entries
            self._downloads[ip]["hourly"] = [
                entry
                for entry in self._downloads[ip]["hourly"]
                if entry["timestamp"] > one_hour_ago
            ]

            # Clean daily entries
            self._downloads[ip]["daily"] = [
                entry
                for entry in self._downloads[ip]["daily"]
                if entry["timestamp"] > one_day_ago
            ]

    def check_limits(self, ip, duration_seconds=0):
        """
        Checks if the user has exceeded download limits.

        Args:
            ip: User's IP address
            duration_seconds: Duration of the content to download

        Returns:
            dict: {
                'allowed': bool,
                'reason': str (if not allowed),
                'limits': dict with current usage
            }
        """
        with self._lock:
            self._clean_old_entries(ip)

            if ip not in self._downloads:
                self._downloads[ip] = {"hourly": [], "daily": []}

            hourly = self._downloads[ip]["hourly"]
            daily = self._downloads[ip]["daily"]

            # Count downloads
            hourly_count = len(hourly)
            daily_count = len(daily)

            # Calculate total duration
            hourly_duration = sum(entry.get("duration", 0) for entry in hourly)
            daily_duration = sum(entry.get("duration", 0) for entry in daily)

            # Check content duration limit
            duration_minutes = duration_seconds / 60
            if duration_minutes > app_config.MAX_CONTENT_DURATION:
                return {
                    "allowed": False,
                    "reason": f"content_duration_exceeded",
                    "max_allowed": app_config.MAX_CONTENT_DURATION,
                    "requested": int(duration_minutes),
                }

            # Check hourly download count
            if hourly_count >= app_config.MAX_DOWNLOADS_PER_HOUR:
                return {
                    "allowed": False,
                    "reason": "hourly_downloads_exceeded",
                    "current": hourly_count,
                    "max_allowed": app_config.MAX_DOWNLOADS_PER_HOUR,
                }

            # Check daily download count
            if daily_count >= app_config.MAX_DOWNLOADS_PER_DAY:
                return {
                    "allowed": False,
                    "reason": "daily_downloads_exceeded",
                    "current": daily_count,
                    "max_allowed": app_config.MAX_DOWNLOADS_PER_DAY,
                }

            # Check hourly duration
            hourly_duration_minutes = hourly_duration / 60
            if (
                hourly_duration_minutes + duration_minutes
                > app_config.MAX_DURATION_PER_HOUR
            ):
                return {
                    "allowed": False,
                    "reason": "hourly_duration_exceeded",
                    "current": int(hourly_duration_minutes),
                    "requested": int(duration_minutes),
                    "max_allowed": app_config.MAX_DURATION_PER_HOUR,
                }

            # Check daily duration
            daily_duration_minutes = daily_duration / 60
            if (
                daily_duration_minutes + duration_minutes
                > app_config.MAX_DURATION_PER_DAY
            ):
                return {
                    "allowed": False,
                    "reason": "daily_duration_exceeded",
                    "current": int(daily_duration_minutes),
                    "requested": int(duration_minutes),
                    "max_allowed": app_config.MAX_DURATION_PER_DAY,
                }

            # All checks passed
            return {
                "allowed": True,
                "limits": {
                    "hourly_downloads": f"{hourly_count}/{app_config.MAX_DOWNLOADS_PER_HOUR}",
                    "daily_downloads": f"{daily_count}/{app_config.MAX_DOWNLOADS_PER_DAY}",
                    "hourly_duration": f"{int(hourly_duration_minutes)}/{app_config.MAX_DURATION_PER_HOUR} min",
                    "daily_duration": f"{int(daily_duration_minutes)}/{app_config.MAX_DURATION_PER_DAY} min",
                },
            }

    def record_download(self, ip, duration_seconds=0, item_count=1):
        """Records a download for the user."""
        with self._lock:
            if ip not in self._downloads:
                self._downloads[ip] = {"hourly": [], "daily": []}

            now = datetime.utcnow()
            entry = {"timestamp": now, "duration": duration_seconds}

            # Record for both hourly and daily tracking
            for _ in range(item_count):
                self._downloads[ip]["hourly"].append(entry.copy())
                self._downloads[ip]["daily"].append(entry.copy())

            self._clean_old_entries(ip)


# Global download tracker instance
download_tracker = DownloadTracker()


def register_routes(app, limiter):
    """Registers all application routes."""

    @app.route("/")
    def index():
        """Redirects to the main dashboard."""
        return redirect(url_for("dashboard"))

    @app.route("/dashboard")
    def dashboard():
        """Main dashboard - configuration is handled client-side with localStorage."""
        current_year = datetime.utcnow().year
        return render_template(
            "dashboard.html", config=DEFAULT_CONFIG, current_year=current_year
        )

    @app.route("/get_default_config")
    def get_default_config():
        """Returns default configuration to initialize localStorage."""
        return jsonify(DEFAULT_CONFIG)

    @app.route("/playlist_info", methods=["POST"])
    @limiter.limit(app_config.RATE_LIMIT_PLAYLIST)
    def playlist_info():
        """
        Gets information from a YouTube/YouTube Music/Spotify playlist.
        Returns JSON with list of videos/tracks for selection.
        """
        try:
            url = request.form.get("url", "").strip()
            config_json = request.form.get("user_config", "{}")

            try:
                user_config = ModelFile.validate_config(json.loads(config_json))
            except json.JSONDecodeError:
                user_config = DEFAULT_CONFIG.copy()

            if not url:
                return jsonify({"error": "Please enter a playlist URL."}), 400

            from logic import obtener_info_playlist, es_url_playlist

            if not es_url_playlist(url):
                return (
                    jsonify(
                        {
                            "error": "The URL doesn't appear to be a YouTube, YouTube Music, or Spotify playlist/album.",
                            "es_playlist": False,
                        }
                    ),
                    400,
                )

            info = obtener_info_playlist(url, user_config)

            if not info:
                return (
                    jsonify(
                        {
                            "error": "Could not get playlist information. Check the URL.",
                            "es_playlist": True,
                        }
                    ),
                    400,
                )

            if not info["items"]:
                return (
                    jsonify(
                        {
                            "error": "The playlist is empty or has no accessible videos.",
                            "es_playlist": True,
                        }
                    ),
                    400,
                )

            app.logger.info(
                f"Playlist info obtained: '{info['titulo']}' ({info['total']} items)"
            )
            return jsonify({"success": True, "es_playlist": True, "playlist": info})

        except Exception as e:
            app.logger.error(f"Error getting playlist info: {e}")
            return jsonify({"error": "Error processing the playlist."}), 500

    @app.route("/verificar_playlist", methods=["POST"])
    @limiter.limit(app_config.RATE_LIMIT_MEDIA_INFO)
    def verificar_playlist():
        """
        Checks if a URL is a playlist without getting all information.
        Useful for quickly detecting if selection UI should be shown.
        """
        try:
            url = request.form.get("url", "").strip()

            if not url:
                return jsonify({"es_playlist": False})

            from logic import es_url_playlist

            es_playlist = es_url_playlist(url)

            return jsonify({"es_playlist": es_playlist, "url": url})

        except Exception as e:
            app.logger.error(f"Error checking playlist: {e}")
            return jsonify({"es_playlist": False})

    @app.route("/search", methods=["POST"])
    @limiter.limit(app_config.RATE_LIMIT_SEARCH)
    def search_youtube():
        """
        Performs a YouTube search and returns the first 5 results.
        """
        try:
            query = request.form.get("query")
            prefer_ytmusic = request.form.get("prefer_ytmusic") == "true"

            if not query:
                return jsonify({"error": "No query provided"}), 400

            search_results = []

            if prefer_ytmusic:
                app.logger.info(f"Searching YouTube Music for: {query}")
                from logic import ytmusic

                if not ytmusic:
                    return jsonify({"error": "YouTube Music not available"}), 503

                try:
                    results = ytmusic.search(query, filter="songs", limit=5)
                    if not results:
                        results = ytmusic.search(query, limit=5)

                    for entry in results[:5]:
                        duration_str = entry.get("duration", "0:00")
                        duration_seconds = 0
                        try:
                            parts = duration_str.split(":")
                            if len(parts) == 2:
                                duration_seconds = int(parts[0]) * 60 + int(parts[1])
                            elif len(parts) == 3:
                                duration_seconds = (
                                    int(parts[0]) * 3600
                                    + int(parts[1]) * 60
                                    + int(parts[2])
                                )
                        except:
                            pass

                        thumbnails = entry.get("thumbnails", [])
                        thumbnail_url = thumbnails[-1]["url"] if thumbnails else ""
                        artists = entry.get("artists", [])
                        artist_name = artists[0]["name"] if artists else "Unknown"

                        video_id = entry.get("videoId", "")

                        search_results.append(
                            {
                                "id": entry.get("videoId"),
                                "video_id": video_id,
                                "titulo": entry.get("title"),
                                "url": f"https://music.youtube.com/watch?v={entry.get('videoId')}",
                                "thumbnail": thumbnail_url,
                                "autor": artist_name,
                                "duracion": duration_str,
                                "duracion_segundos": duration_seconds,
                                "fuente": "youtube_music",
                            }
                        )

                except Exception as e:
                    app.logger.error(f"Error searching YTM: {e}")

            else:
                app.logger.info(f"Searching YouTube for: {query}")

                ydl_opts = {
                    "quiet": True,
                    "extract_flat": True,
                    "noplaylist": True,
                    "nocheckcertificate": True,
                }

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    result = ydl.extract_info(f"ytsearch5:{query}", download=False)

                    if "entries" not in result:
                        return jsonify({"error": "No results found"}), 404

                    for entry in result["entries"]:
                        duration = entry.get("duration", 0)
                        if isinstance(duration, (int, float)):
                            minutes = int(duration // 60)
                            seconds = int(duration % 60)
                            duration_str = f"{minutes}:{seconds:02d}"
                        else:
                            duration_str = str(duration)

                        video_id = entry.get("id", "")

                        search_results.append(
                            {
                                "id": video_id,
                                "video_id": video_id,
                                "titulo": entry.get("title"),
                                "url": entry.get("url")
                                or f"https://www.youtube.com/watch?v={video_id}",
                                "thumbnail": entry.get("thumbnail")
                                or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                                "autor": entry.get("uploader")
                                or entry.get("channel", "Unknown"),
                                "duracion": duration_str,
                                "duracion_segundos": duration,
                                "fuente": "youtube",
                            }
                        )

            if not search_results:
                return jsonify({"error": "No results found"}), 404

            return jsonify(
                {
                    "success": True,
                    "es_playlist": True,
                    "playlist": {
                        "titulo": f"Results for: {query}",
                        "autor": (
                            "YouTube Music Search"
                            if prefer_ytmusic
                            else "YouTube Search"
                        ),
                        "items": search_results,
                        "total": len(search_results),
                    },
                }
            )

        except Exception as e:
            app.logger.error(f"Error searching: {str(e)}")
            return jsonify({"error": str(e)}), 500

    @app.route("/media_info", methods=["POST"])
    @limiter.limit(app_config.RATE_LIMIT_MEDIA_INFO)
    def media_info():
        """
        Gets basic information from an individual video/track.
        Returns title, thumbnail, author, duration, and detected source.
        """
        try:
            url = request.form.get("url", "").strip()
            config_json = request.form.get("user_config", "{}")

            try:
                user_config = ModelFile.validate_config(json.loads(config_json))
            except json.JSONDecodeError:
                user_config = DEFAULT_CONFIG.copy()

            if not url:
                return jsonify({"error": "Empty URL"}), 400

            from logic import obtener_info_media, es_url_playlist, detectar_fuente_url

            if es_url_playlist(url):
                return jsonify(
                    {"es_playlist": True, "fuente": detectar_fuente_url(url)}
                )

            info = obtener_info_media(url, user_config)

            if not info:
                return jsonify({"error": "Could not get information"}), 400

            # Extract video_id if it's a YouTube video
            video_id = None
            if info.get("fuente") in ["youtube", "youtube_music"]:
                from logic import extraer_video_id_youtube

                video_id = extraer_video_id_youtube(url)

            return jsonify(
                {
                    "success": True,
                    "es_playlist": False,
                    "titulo": info.get("titulo", "No title"),
                    "thumbnail": info.get("thumbnail", ""),
                    "autor": info.get("autor", "Unknown"),
                    "duracion": info.get("duracion", "0:00"),
                    "duracion_segundos": info.get("duracion_segundos", 0),
                    "fuente": info.get("fuente", "youtube"),
                    "video_id": video_id,
                }
            )

        except Exception as e:
            app.logger.error(f"Error getting media info: {e}")
            return jsonify({"error": "Error processing the URL"}), 500

    @app.route("/sponsorblock_info", methods=["POST"])
    @limiter.limit(app_config.RATE_LIMIT_MEDIA_INFO)
    def sponsorblock_info():
        """
        Gets SponsorBlock information for a video.
        Returns segments and adjusted duration.
        """
        try:
            video_id = request.form.get("video_id", "").strip()
            categories_json = request.form.get("categories", "[]")
            original_duration = float(request.form.get("duration", 0))

            if not video_id:
                return jsonify({"error": "Video ID required"}), 400

            try:
                categories = json.loads(categories_json)
            except json.JSONDecodeError:
                categories = None

            from logic import obtener_segmentos_sponsorblock

            sb_info = obtener_segmentos_sponsorblock(video_id, categories)

            # Calculate adjusted duration
            adjusted_duration = max(
                0, original_duration - sb_info["total_duration_removed"]
            )

            # Format durations
            def format_duration(seconds):
                mins = int(seconds // 60)
                secs = int(seconds % 60)
                return f"{mins}:{secs:02d}"

            return jsonify(
                {
                    "success": True,
                    "has_segments": sb_info["has_segments"],
                    "total_duration_removed": sb_info["total_duration_removed"],
                    "adjusted_duration": adjusted_duration,
                    "adjusted_duration_str": format_duration(adjusted_duration),
                    "categories_found": sb_info["categories_found"],
                    "segment_count": len(sb_info["segments"]),
                }
            )

        except Exception as e:
            app.logger.error(f"Error getting SponsorBlock info: {e}")
            return jsonify({"error": "Error processing SponsorBlock data"}), 500

    @app.route("/descargar", methods=["POST"])
    @limiter.limit(app_config.RATE_LIMIT_DOWNLOAD)
    def descargar():
        """Processes music/video download."""
        temp_dir = None
        try:
            input_url = request.form.get("inputURL", "").strip()
            is_playlist_mode = request.form.get("is_playlist_mode", "false") == "true"
            selected_urls_json = request.form.get("selected_urls", "")
            config_json = request.form.get("user_config", "{}")
            item_configs_json = request.form.get("item_configs", "{}")

            if not input_url and not is_playlist_mode:
                return jsonify({"error": "Please enter a URL or song name."}), 400

            # Get user IP for tracking
            user_ip = request.remote_addr or "unknown"

            # Parse configuration
            try:
                user_config = json.loads(config_json)
                user_config = ModelFile.validate_config(user_config)
            except json.JSONDecodeError:
                user_config = DEFAULT_CONFIG.copy()

            # Parse individual item configurations
            try:
                item_configs = (
                    json.loads(item_configs_json) if item_configs_json else {}
                )
            except json.JSONDecodeError:
                item_configs = {}

            selected_urls = []
            total_duration = 0
            item_count = 0

            if is_playlist_mode and selected_urls_json:
                try:
                    selected_urls = json.loads(selected_urls_json)
                    if not selected_urls:
                        return (
                            jsonify(
                                {"error": "Select at least one item from the playlist."}
                            ),
                            400,
                        )

                    # Check playlist size limit
                    if len(selected_urls) > app_config.MAX_PLAYLIST_ITEMS:
                        return (
                            jsonify(
                                {
                                    "error": f"Playlist exceeds maximum allowed items. Maximum: {app_config.MAX_PLAYLIST_ITEMS}, Selected: {len(selected_urls)}"
                                }
                            ),
                            400,
                        )

                    # Calculate total duration from selected items
                    item_count = len(selected_urls)
                    for url_data in selected_urls:
                        if isinstance(url_data, dict):
                            total_duration += url_data.get("duracion_segundos", 0)

                except json.JSONDecodeError:
                    return jsonify({"error": "Error in playlist data."}), 400
            else:
                # Single item - get duration
                item_count = 1
                try:
                    from logic import obtener_info_media

                    media_info = obtener_info_media(input_url, user_config)
                    if media_info:
                        total_duration = media_info.get("duracion_segundos", 0)
                except Exception as e:
                    app.logger.warning(f"Could not get media duration: {e}")
                    total_duration = 0

            # Check download limits
            limit_check = download_tracker.check_limits(user_ip, total_duration)

            if not limit_check["allowed"]:
                reason = limit_check["reason"]
                error_messages = {
                    "content_duration_exceeded": f"This content exceeds the maximum allowed duration. Maximum: {limit_check['max_allowed']} minutes, Requested: {limit_check['requested']} minutes.",
                    "hourly_downloads_exceeded": f"You have exceeded the hourly download limit. Maximum: {limit_check['max_allowed']} downloads per hour. Current: {limit_check['current']}. Please try again later.",
                    "daily_downloads_exceeded": f"You have exceeded the daily download limit. Maximum: {limit_check['max_allowed']} downloads per day. Current: {limit_check['current']}. Please try again tomorrow.",
                    "hourly_duration_exceeded": f"Adding this content would exceed your hourly duration limit. Current: {limit_check['current']} minutes, Requested: {limit_check['requested']} minutes, Maximum: {limit_check['max_allowed']} minutes per hour.",
                    "daily_duration_exceeded": f"Adding this content would exceed your daily duration limit. Current: {limit_check['current']} minutes, Requested: {limit_check['requested']} minutes, Maximum: {limit_check['max_allowed']} minutes per day.",
                }

                error_msg = error_messages.get(reason, "Download limit exceeded.")
                app.logger.warning(f"Download limit exceeded for {user_ip}: {reason}")

                return (
                    jsonify(
                        {"error": error_msg, "limit_exceeded": True, "reason": reason}
                    ),
                    429,  # Too Many Requests
                )

            task_id = str(uuid.uuid4())
            ProgressStore = _get_progress_store()
            ProgressStore.create(task_id)

            nombre_archivo = f"descarga-{uuid.uuid4()}.zip"
            temp_dir = os.path.join(BASE_DIR, "Downloads", "Temp", task_id)

            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            os.makedirs(temp_dir, exist_ok=True)

            ProgressStore.update(task_id, temp_dir=temp_dir)

            # --- Enqueue download task on RQ (replaces threading.Thread) ---
            from logic import execute_download_task

            queue = get_rq_queue()
            queue.enqueue(
                execute_download_task,
                user_config=user_config,
                input_url=input_url,
                task_id=task_id,
                nombre_archivo=nombre_archivo,
                temp_dir=temp_dir,
                is_playlist_mode=is_playlist_mode,
                selected_urls=selected_urls if is_playlist_mode else None,
                item_configs=item_configs if is_playlist_mode else None,
                redis_url=app_config.REDIS_URL,
                job_timeout="30m",  # generous timeout for large playlists
            )

            return jsonify({"request_id": task_id})

        except Exception as e:
            app.logger.error(f"Error in download: {e}")
            return jsonify({"error": "An error occurred during download."}), 500

    @app.route("/stream_progress/<request_id>")
    def stream_progress(request_id):
        """SSE endpoint for real-time download progress."""
        ProgressStore = _get_progress_store()

        def generate():
            while True:
                progress = ProgressStore.get(request_id)
                data = json.dumps(progress)
                try:
                    yield f"data: {data}\n\n"
                except (GeneratorExit, BrokenPipeError, ConnectionResetError, OSError):
                    # Client disconnected; request cancellation of server-side work
                    try:
                        ProgressStore.request_cancel(request_id)
                        ProgressStore.update(
                            request_id,
                            status="Client disconnected",
                            detail="Cancelling on client disconnect...",
                            phase="cancelled",
                        )
                    except Exception:
                        app.logger.exception("Failed to request cancel on disconnect")
                    break

                if progress.get("complete") or progress.get("error"):
                    break

                time.sleep(0.3)

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.route("/download_file/<request_id>")
    def download_file(request_id):
        """Serve the completed download file and clean up."""
        ProgressStore = _get_progress_store()
        progress = ProgressStore.get(request_id)
        file_path = progress.get("file_path")
        temp_dir_path = progress.get("temp_dir")

        if not file_path or not os.path.exists(file_path):
            return jsonify({"error": "File not found or download not complete"}), 404

        filename = os.path.basename(file_path)

        response = send_file(
            file_path,
            as_attachment=True,
            download_name=filename,
        )

        @response.call_on_close
        def _cleanup():
            try:
                if temp_dir_path and os.path.exists(temp_dir_path):
                    shutil.rmtree(temp_dir_path, ignore_errors=True)
                    app.logger.info(f"Temp directory cleaned: {temp_dir_path}")
            except Exception as e:
                app.logger.error(f"Error cleaning temp dir: {e}")

            # Delayed removal of progress store entry
            def _delayed():
                time.sleep(30)
                ProgressStore.remove(request_id)

            threading.Thread(target=_delayed, daemon=True).start()

        return response


def register_error_handlers(app):
    """Registers HTTP error handlers."""
    from werkzeug.exceptions import HTTPException

    @app.errorhandler(404)
    def not_found(error):
        """Handles page not found."""
        return (
            render_template(
                "error.html",
                error_code=404,
                error_title="Page Not Found",
                error_message="The page you're looking for doesn't exist.",
            ),
            404,
        )

    @app.errorhandler(429)
    def ratelimit_handler(error):
        """Handles rate limit exceeded."""
        # Get current configuration for display
        limits = {
            "requests_per_hour": app_config.RATE_LIMIT_PER_HOUR,
            "requests_per_day": app_config.RATE_LIMIT_PER_DAY,
            "downloads_per_hour": app_config.MAX_DOWNLOADS_PER_HOUR,
            "downloads_per_day": app_config.MAX_DOWNLOADS_PER_DAY,
        }

        return (
            render_template(
                "error.html",
                error_code=429,
                error_title="Too Many Requests",
                error_message="You have exceeded the rate limit. Please wait a moment before trying again.",
                error_details=f"Current limits: {limits['requests_per_hour']} requests per hour, {limits['requests_per_day']} per day.",
                limits=limits,
            ),
            429,
        )

    @app.errorhandler(500)
    def internal_error(error):
        """Handles internal server errors."""
        app.logger.error(f"Internal error: {error}")
        return (
            render_template(
                "error.html",
                error_code=500,
                error_title="Internal Server Error",
                error_message="An unexpected error occurred. Please try again later.",
            ),
            500,
        )

    @app.errorhandler(Exception)
    def handle_exception(e):
        """Handles unhandled exceptions."""
        if isinstance(e, HTTPException):
            return e
        app.logger.error(f"Unhandled exception: {e}")
        return (
            render_template(
                "error.html",
                error_code=500,
                error_title="Unexpected Error",
                error_message="Something went wrong. Please try again later.",
            ),
            500,
        )
