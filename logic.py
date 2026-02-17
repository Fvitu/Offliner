"""
Backend logic for Offliner.

All download, search, and media-resolution logic is encapsulated inside the
``OfflinerCore`` class.  A module-level singleton (``_core``) is created on
import so that existing callers (routes.py) can keep using the same public
function names via thin backward-compatible wrappers defined at the bottom of
this file.

"""

from __future__ import annotations

import concurrent.futures
import json as _json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable

import redis as _redis
import requests
import spotipy
import yt_dlp
from spotipy.oauth2 import SpotifyClientCredentials
from ytmusicapi import YTMusic

# Optional: rapidfuzz for faster fuzzy matching
try:
    from rapidfuzz import fuzz as _rfuzz

    _RAPIDFUZZ_AVAILABLE = True
except ImportError:
    from difflib import SequenceMatcher as _SequenceMatcher

    _RAPIDFUZZ_AVAILABLE = False

logger = logging.getLogger(__name__)


# ============================================
# Thread-safe TTL cache
# ============================================

_CACHE_MISS = object()


class _TTLCache:
    """Bounded, thread-safe cache with per-entry TTL expiry."""

    def __init__(self, maxsize: int = 256, ttl: float = 600.0) -> None:
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._maxsize = maxsize
        self._ttl = ttl

    def get(self, key: Any) -> Any:
        """Return cached value or ``_CACHE_MISS``."""
        with self._lock:
            entry = self._data.get(key)
            if entry is not None:
                ts, val = entry
                if time.time() - ts < self._ttl:
                    return val
                del self._data[key]
        return _CACHE_MISS

    def put(self, key: Any, value: Any) -> None:
        with self._lock:
            now = time.time()
            # Purge expired
            expired = [k for k, (ts, _) in self._data.items() if now - ts >= self._ttl]
            for k in expired:
                del self._data[k]
            # Evict oldest if at capacity
            while len(self._data) >= self._maxsize:
                oldest_key = min(self._data, key=lambda k: self._data[k][0])
                del self._data[oldest_key]
            self._data[key] = (now, value)


_yt_search_cache = _TTLCache(maxsize=512, ttl=600.0)
_ytm_search_cache = _TTLCache(maxsize=256, ttl=600.0)


# ============================================
# Global Download Progress Store (SSE support) — Redis-backed
# ============================================

# Module-level Redis connection pool.  Initialised lazily the first time any
# DownloadProgressStore method is called, or eagerly via `init_redis()`.
_redis_client: _redis.Redis | None = None

# Default TTL for progress entries (seconds).  Entries auto-expire so that
# stale sessions never accumulate in Redis.
_PROGRESS_TTL: int = 3600  # 1 hour


def init_redis(redis_url: str | None = None) -> _redis.Redis:
    """Initialise (or re-initialise) the module-level Redis client.

    Called once during application startup from ``app.py``.  If *redis_url* is
    ``None``, the ``REDIS_URL`` environment variable is used (falling back to
    ``redis://localhost:6379/0``).
    """
    global _redis_client  # noqa: PLW0603
    url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    _redis_client = _redis.Redis.from_url(url, decode_responses=True)
    logger.info(f"Redis client initialised ({url})")
    return _redis_client


def get_redis() -> _redis.Redis:
    """Return the module-level Redis client, initialising lazily if needed."""
    global _redis_client  # noqa: PLW0603
    if _redis_client is None:
        init_redis()
    assert _redis_client is not None
    return _redis_client


class DownloadProgressStore:
    """Redis-backed global store for real-time download progress per request_id.

    Used by SSE endpoints in routes.py to stream live progress to the frontend.
    Updated by yt-dlp progress hooks during downloads.

    Each request_id maps to a Redis key ``progress:{request_id}`` holding a
    JSON-serialised dict.  Keys expire after ``_PROGRESS_TTL`` seconds.
    """

    _KEY_PREFIX: str = "progress:"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @classmethod
    def _key(cls, request_id: str) -> str:
        return f"{cls._KEY_PREFIX}{request_id}"

    # ------------------------------------------------------------------
    # Public API  (same signatures as the old in-memory implementation)
    # ------------------------------------------------------------------

    @classmethod
    def create(cls, request_id: str, total_items: int = 1) -> None:
        r = get_redis()
        data = {
            "percent": 0,
            "status": "Preparing...",
            "detail": "",
            "speed": "",
            "eta": "",
            "current_file": "",
            "completed_items": 0,
            "total_items": total_items,
            "phase": "preparing",
            "complete": False,
            "error": None,
            "file_path": None,
            "temp_dir": None,
            # Flag that indicates a client requested cancellation (disconnect)
            "cancel_requested": False,
        }
        r.set(cls._key(request_id), _json.dumps(data), ex=_PROGRESS_TTL)

    @classmethod
    def request_cancel(cls, request_id: str) -> None:
        """Mark a running request as cancelled so worker threads can abort."""
        r = get_redis()
        raw = r.get(cls._key(request_id))
        if raw is None:
            return
        data = _json.loads(raw)
        data["cancel_requested"] = True
        r.set(cls._key(request_id), _json.dumps(data), keepttl=True)

    @classmethod
    def is_cancelled(cls, request_id: str) -> bool:
        r = get_redis()
        raw = r.get(cls._key(request_id))
        if raw is None:
            return False
        data = _json.loads(raw)
        return bool(data.get("cancel_requested"))

    @classmethod
    def update(cls, request_id: str, **kwargs: Any) -> None:
        r = get_redis()
        raw = r.get(cls._key(request_id))
        if raw is None:
            return
        data = _json.loads(raw)
        data.update(kwargs)
        r.set(cls._key(request_id), _json.dumps(data), keepttl=True)

    @classmethod
    def get(cls, request_id: str) -> dict:
        r = get_redis()
        raw = r.get(cls._key(request_id))
        if raw is None:
            return {
                "percent": 0,
                "status": "Unknown",
                "detail": "",
                "speed": "",
                "eta": "",
                "complete": False,
                "error": "Session not found",
            }
        return _json.loads(raw)

    @classmethod
    def remove(cls, request_id: str) -> None:
        r = get_redis()
        r.delete(cls._key(request_id))


# ============================================
# Download Result (thread-safe bookkeeping)
# ============================================


class _DownloadResult:
    """Thread-safe accumulator for per-session download statistics."""

    __slots__ = (
        "_lock",
        "audios_exito",
        "audios_error",
        "videos_exito",
        "videos_error",
        "canciones_descargadas",
        "progress_callback",
        "total_items",
        "completed_items",
        "request_id",
    )

    def __init__(
        self,
        progress_callback: Callable | None = None,
        request_id: str | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self.audios_exito: int = 0
        self.audios_error: int = 0
        self.videos_exito: int = 0
        self.videos_error: int = 0
        self.canciones_descargadas: list[str] = []
        self.progress_callback = progress_callback
        self.total_items: int = 0
        self.completed_items: int = 0
        self.request_id: str | None = request_id

    # -- Thread-safe mutators --

    def inc_success(self, mode: str) -> None:
        with self._lock:
            if mode == "audio":
                self.audios_exito += 1
            else:
                self.videos_exito += 1

    def inc_error(self, mode: str) -> None:
        with self._lock:
            if mode == "audio":
                self.audios_error += 1
            else:
                self.videos_error += 1

    def add_file(self, path: str) -> None:
        with self._lock:
            self.canciones_descargadas.append(path)

    def inc_completed(self) -> None:
        with self._lock:
            self.completed_items += 1

    def get_progress_pct(self) -> int:
        with self._lock:
            if self.total_items <= 0:
                return 15
            return 15 + int((self.completed_items / self.total_items) * 70)


# ============================================
# OfflinerCore
# ============================================


class OfflinerCore:
    """Central class encapsulating all Offliner download / search logic.

    Designed as a **singleton** for long-lived API clients (Spotify, YTMusic).
    All request-specific state (paths, cookies, results) is either passed as
    method arguments or scoped locally — never stored on ``self``.
    """

    # --- Class-level constants ------------------------------------------------

    SPONSORBLOCK_CATEGORIES: dict[str, str] = {
        "sponsor": "Sponsors (promociones pagadas)",
        "intro": "Intros/Animaciones de entrada",
        "outro": "Outros/Créditos finales",
        "selfpromo": "Auto-promoción del creador",
        "preview": "Previews/Avances",
        "filler": "Relleno/Contenido no musical",
        "interaction": "Recordatorios de suscripción/interacción",
        "music_offtopic": "Partes sin música en videos musicales",
    }

    _SIDECAR_EXTENSIONS = (".jpg", ".png", ".webp", ".vtt", ".srt", ".ass")

    _VIDEO_TAG_PATTERNS: list[str] = [
        r"\(Official\s*(Music\s*)?Video\)",
        r"\(Official\s*Audio\)",
        r"\(Official\s*Lyric\s*Video\)",
        r"\(Video\s*Oficial\)",
        r"\(Audio\s*Oficial\)",
        r"\(Visualizer\)",
        r"\[Official\s*(Music\s*)?Video\]",
        r"\[Official\s*Audio\]",
        r"\(HD\)",
        r"\(HQ\)",
        r"\(4K\)",
        r"\(1080p\)",
        r"\(720p\)",
    ]

    _DEFAULT_MAX_WORKERS: int = 4

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        self._base_dir: Path = Path(__file__).resolve().parent

        # One-time ffmpeg check at startup
        self._ffmpeg_available: bool = shutil.which("ffmpeg") is not None
        if not self._ffmpeg_available:
            logger.warning("ffmpeg not found in PATH; post-processing will fail")

        # API clients (graceful init — never raise)
        self.ytmusic: YTMusic | None = self._init_ytmusic()
        self._spotify_client_id: str = os.getenv("SPOTIFY_CLIENT_ID", "")
        self._spotify_client_secret: str = os.getenv("SPOTIFY_CLIENT_SECRET", "")
        self._spotify_default: spotipy.Spotify | None = self._init_spotify(
            self._spotify_client_id, self._spotify_client_secret
        )

    @staticmethod
    def _init_ytmusic() -> YTMusic | None:
        try:
            client = YTMusic()
            logger.info("YTMusic client initialized")
            return client
        except Exception as e:
            logger.warning(f"Could not initialize YTMusic: {e}")
            return None

    @staticmethod
    def _init_spotify(client_id: str, client_secret: str) -> spotipy.Spotify | None:
        if not (client_id and client_secret):
            logger.warning("Spotify credentials not configured")
            return None
        try:
            ccm = SpotifyClientCredentials(client_id, client_secret)
            client = spotipy.Spotify(
                client_credentials_manager=ccm, requests_timeout=10
            )
            logger.info("Spotify client initialized successfully")
            return client
        except Exception as e:
            logger.warning(f"Could not initialize Spotify client: {e}")
            return None

    # ------------------------------------------------------------------
    # Fuzzy matching  (rapidfuzz preferred, SequenceMatcher fallback)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Lower-case, strip parenthetical/bracket tags, collapse whitespace."""
        text = text.lower()
        text = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", text)
        text = re.sub(r"[^\w\s]", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _is_match(self, query: str, candidate: str, threshold: float = 0.6) -> bool:
        """Return *True* when *query* and *candidate* are sufficiently similar."""
        return self._match_score(query, candidate) >= threshold

    def _match_score(self, query: str, candidate: str) -> float:
        """Return a similarity ratio in [0, 1]."""
        q = self._normalize_text(query)
        c = self._normalize_text(candidate)
        if _RAPIDFUZZ_AVAILABLE:
            return _rfuzz.ratio(q, c) / 100.0
        return _SequenceMatcher(None, q, c).ratio()

    # ------------------------------------------------------------------
    # Per-session cookie management
    # ------------------------------------------------------------------

    @staticmethod
    def _setup_cookies(config: dict, session_dir: Path) -> Path | None:
        """Provision a session-local cookie file from *config* and return its
        path, or ``None`` when no cookies are configured.

        Supported config keys
        ---------------------
        * ``cookies_content``  – raw Netscape cookie-jar text.
        * ``cookies_filepath`` – path to an existing cookie file on disk.

        The resulting file is written *inside* ``session_dir`` so it is
        automatically destroyed during session cleanup.  Cookie content is
        **never logged** for security.
        """
        cookies_content = config.get("cookies_content")
        cookies_filepath = config.get("cookies_filepath")

        if cookies_content:
            cookie_path = session_dir / "cookies.txt"
            try:
                cookie_path.write_text(cookies_content, encoding="utf-8")
                logger.info("Per-session cookie file created (from content)")
                return cookie_path
            except Exception as e:
                logger.error(f"Failed to write cookie file: {e}")
                return None

        if cookies_filepath:
            src = Path(cookies_filepath)
            if src.is_file():
                cookie_path = session_dir / "cookies.txt"
                try:
                    shutil.copy2(str(src), str(cookie_path))
                    logger.info("Per-session cookie file created (from filepath)")
                    return cookie_path
                except Exception as e:
                    logger.error(f"Failed to copy cookie file: {e}")
                    return None
            else:
                logger.warning("Cookie filepath provided but file not found")
                return None

        return None

    # ------------------------------------------------------------------
    # Path / filename utilities  (pathlib-based)
    # ------------------------------------------------------------------

    def _ensure_dir(self, path: Path | str) -> Path:
        """Create *path* (and parents) if it doesn't exist; return it."""
        p = Path(path) if not isinstance(path, Path) else path
        if not p.is_absolute():
            p = self._base_dir / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def _sanitize_filename(title: str) -> str:
        """Remove illegal Windows characters and normalize to ASCII."""
        name = title.strip()
        name = re.sub(r'[<>:"/\\|?*]', "", name)
        name = re.sub(r"\.+$", "", name.strip())
        name = re.sub(r"\s+", " ", name).strip()
        if len(name) > 200:
            name = name[:200].strip()
        try:
            ascii_name = (
                unicodedata.normalize("NFKD", name)
                .encode("ascii", "ignore")
                .decode("ascii")
            )
            ascii_name = re.sub(r"\s+", " ", ascii_name).strip()
            if ascii_name:
                name = ascii_name
        except Exception:
            pass
        return name

    # ------------------------------------------------------------------
    # yt-dlp option builders
    # ------------------------------------------------------------------

    @staticmethod
    def _base_ytdlp_opts(cookie_file: Path | None = None) -> dict[str, Any]:
        """Base options shared by every yt-dlp invocation.

        When *cookie_file* is provided, ``cookiefile`` is added to the dict
        so yt-dlp authenticates with those cookies.
        """
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "extractor_retries": 10,
            "fragment_retries": 10,
            "file_access_retries": 5,
            "retry_sleep_functions": {"http": lambda n: min(2**n, 30)},
            "socket_timeout": 60,
            "http_chunk_size": 10485760,
            # Use the web client when user cookies are present so yt-dlp sends
            # them to the same surface that issued the cookies. Android client
            # plus web cookies can trigger 400 responses from YouTube.
            "extractor_args": {
                "youtube": {
                    "player_client": ["web"] if cookie_file else ["android_music"]
                },
            },
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
            "nocheckcertificate": True,
            "check_formats": "selected",
            "force_ipv4": True,
            "continuedl": False,
            "overwrites": True,
            "cachedir": False,
            "encoding": "utf-8",
        }
        if cookie_file:
            opts["cookiefile"] = str(cookie_file)
        return opts

    def _sponsorblock_postprocessors(self, config: dict) -> list[dict]:
        """Build SponsorBlock postprocessor chain from user config."""
        if not config.get("SponsorBlock_enabled"):
            return []
        categories = config.get("SponsorBlock_categories", [])
        if not categories:
            return []

        # SponsorBlockPP expects only `categories` and optional `api`.
        # The previous code passed an `action` argument (e.g. 'remove'/'chapter')
        # which is not accepted and caused failures. We instead always request
        # SponsorBlock chapters for the relevant categories and then use
        # `ModifyChapters` to remove the selected categories when needed.
        all_cats = list(self.SPONSORBLOCK_CATEGORIES.keys())

        pp: list[dict] = [
            {
                "key": "SponsorBlock",
                "api": "https://sponsor.ajay.app",
                "categories": all_cats,
            },
            {
                "key": "ModifyChapters",
                "remove_sponsor_segments": categories,
                "force_keyframes": False,
            },
        ]
        logger.info(f"SponsorBlock enabled. Removing: {', '.join(categories)}")
        return pp

    # ------------------------------------------------------------------
    # URL detection  (static — no instance state needed)
    # ------------------------------------------------------------------

    @staticmethod
    def detect_url_source(url: str) -> str | None:
        """Return ``'spotify'``, ``'youtube_music'``, ``'youtube'``, or *None*."""
        if not url:
            return None
        u = url.lower()
        if "spotify.com" in u:
            return "spotify"
        if "music.youtube.com" in u:
            return "youtube_music"
        if "youtube.com" in u or "youtu.be" in u:
            return "youtube"
        return None

    @staticmethod
    def is_playlist_url(url: str) -> bool:
        """Detect YouTube / YouTube Music / Spotify playlist or album URLs."""
        if not url:
            return False
        u = url.lower()
        patterns = [
            "youtube.com/playlist?list=",
            "youtube.com/watch?v=.*&list=",
            "youtube.com/watch?.*list=",
            "music.youtube.com/playlist?list=",
            "music.youtube.com/watch?v=.*&list=",
            "youtu.be/.*[?&]list=",
        ]
        for p in patterns:
            if p.replace(".*", "") in u or (
                ".*" in p and all(part in u for part in p.split(".*"))
            ):
                return True
        if "spotify.com" in u and ("/playlist/" in u or "/album/" in u):
            return True
        return False

    @staticmethod
    def extract_youtube_video_id(url: str) -> str | None:
        """Extract the 11-character YouTube video ID from *url*."""
        if not url:
            return None
        m = re.search(
            r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/"
            r"|youtube\.com/v/|music\.youtube\.com/watch\?v=)"
            r"([a-zA-Z0-9_-]{11})",
            url,
        )
        if m:
            return m.group(1)
        if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
            return url
        return None

    # ------------------------------------------------------------------
    # SponsorBlock API
    # ------------------------------------------------------------------

    def get_sponsorblock_segments(
        self, video_id: str, categories: list[str] | None = None
    ) -> dict:
        """Query the SponsorBlock API for skip-segments."""
        empty: dict = {
            "has_segments": False,
            "segments": [],
            "total_duration_removed": 0,
            "categories_found": [],
        }
        cats = categories or list(self.SPONSORBLOCK_CATEGORIES.keys())
        try:
            resp = requests.get(
                f"https://sponsor.ajay.app/api/skipSegments?videoID={video_id}",
                timeout=5,
            )
            if resp.status_code == 404:
                return empty
            if resp.status_code != 200:
                logger.warning(f"SponsorBlock API returned status {resp.status_code}")
                return empty

            filtered = [s for s in resp.json() if s.get("category") in cats]
            if not filtered:
                return empty

            total = sum(s["segment"][1] - s["segment"][0] for s in filtered)
            return {
                "has_segments": True,
                "segments": filtered,
                "total_duration_removed": total,
                "categories_found": list({s["category"] for s in filtered}),
            }
        except requests.RequestException as e:
            logger.error(f"SponsorBlock request error: {e}")
            return empty
        except Exception as e:
            logger.error(f"SponsorBlock unexpected error: {e}")
            return empty

    # ------------------------------------------------------------------
    # Spotify helpers
    # ------------------------------------------------------------------

    def _get_spotify_client(self, config: dict) -> spotipy.Spotify | None:
        """Return a Spotify client — custom credentials override the default."""
        custom_id = config.get("Client_ID", "")
        custom_secret = config.get("Secret_ID", "")
        if custom_id and custom_id != self._spotify_client_id:
            try:
                ccm = SpotifyClientCredentials(custom_id, custom_secret)
                return spotipy.Spotify(
                    client_credentials_manager=ccm, requests_timeout=10
                )
            except Exception as e:
                logger.error(f"Custom Spotify client failed: {e}")
        return self._spotify_default

    def _resolve_spotify_track(self, config: dict, url: str) -> str:
        """Convert a Spotify track URL into a YouTube URL via search."""
        try:
            client = self._get_spotify_client(config)
            if not client:
                logger.error("No Spotify client available")
                return ""
            if "spotify.com" not in url or "/track/" not in url:
                logger.warning(f"Invalid Spotify URL: {url}")
                return ""
            track_id = url.split("/track/")[1].split("?")[0].split("/")[0]
            logger.info(f"Extracting Spotify track info: {track_id}")
            info = client.track(track_id)
            query = f"{info['name']} {info['artists'][0]['name']}"
            logger.info(f"Searching YouTube for: {query}")
            return self.search_youtube(query)
        except Exception as e:
            logger.error(f"Error resolving Spotify track: {e}")
            return ""

    # ------------------------------------------------------------------
    # Search  (with TTL caching)
    # ------------------------------------------------------------------

    def search_youtube(self, query: str) -> str:
        """Return the URL of the first YouTube result for *query*, or ``""``.

        Results are cached for 10 minutes to save API quota.
        """
        cached = _yt_search_cache.get(query)
        if cached is not _CACHE_MISS:
            logger.info(f"YouTube search cache hit: '{query}'")
            return cached

        result = self._search_youtube_impl(query)
        _yt_search_cache.put(query, result)
        return result

    def _search_youtube_impl(self, query: str) -> str:
        """Actual YouTube search via yt-dlp (uncached)."""
        try:
            logger.info(f"Searching YouTube: {query}")
            opts = self._base_ytdlp_opts()
            opts.update({"extract_flat": True, "default_search": "ytsearch1"})
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                if info and info.get("entries"):
                    entry = info["entries"][0]
                    vid = (entry.get("id") or "") if entry else ""
                    if vid:
                        link = f"https://www.youtube.com/watch?v={vid}"
                        logger.info(f"Video found: {link}")
                        return link
            logger.warning(f"No results for: {query}")
            return ""
        except Exception as e:
            logger.error(f"Error searching YouTube: {e}")
            return ""

    def search_youtube_music(
        self, title: str, artist: str | None = None
    ) -> tuple[str | None, str | None, str | None]:
        """Search YouTube Music and return ``(url, title, artist)`` or three *None*s.

        Results are cached for 10 minutes.
        """
        cache_key = (title.strip().lower(), (artist or "").strip().lower())
        cached = _ytm_search_cache.get(cache_key)
        if cached is not _CACHE_MISS:
            logger.info(f"YouTube Music cache hit: '{title}'")
            return cached

        result = self._search_youtube_music_impl(title, artist)
        _ytm_search_cache.put(cache_key, result)
        return result

    def _search_youtube_music_impl(
        self, title: str, artist: str | None = None
    ) -> tuple[str | None, str | None, str | None]:
        """Actual YouTube Music search (uncached)."""
        if not self.ytmusic:
            logger.warning("YTMusic not available")
            return None, None, None

        try:
            search_q = title.strip()
            for pat in self._VIDEO_TAG_PATTERNS:
                search_q = re.sub(pat, "", search_q, flags=re.IGNORECASE)
            search_q = search_q.replace("||", " ").replace("|", " ").replace("#", " ")
            search_q = re.sub(r"\s+", " ", search_q).strip()

            logger.info(f"Searching on YouTube Music: '{search_q}'")

            results = self.ytmusic.search(search_q, filter="songs", limit=15)
            if not results:
                results = self.ytmusic.search(search_q, limit=15)
            if not results:
                logger.warning(f"No results found for '{search_q}'")
                return None, None, None

            # Build a combined query string for fuzzy comparison
            query_combined = f"{title} {artist or ''}".strip()
            best: dict | None = None
            best_score: float = 0.0

            for r in results:
                if not r.get("videoId"):
                    continue
                r_title = r.get("title", "")
                r_artists = r.get("artists", [])
                r_artist = r_artists[0].get("name", "") if r_artists else ""
                result_combined = f"{r_title} {r_artist}".strip()
                score = self._match_score(query_combined, result_combined)
                if score > best_score:
                    best_score = score
                    best = r

            if best and best_score >= 0.5:
                vid = best["videoId"]
                t = best.get("title", search_q)
                arts = best.get("artists", [])
                a = arts[0].get("name", "Unknown") if arts else "Unknown"
                url = f"https://music.youtube.com/watch?v={vid}"
                logger.info(f"Match found ({best_score:.0%}): '{t}' by '{a}'")
                return url, t, a

            logger.warning(
                f"No sufficient match for '{title}' (best: {best_score:.0%})"
            )
            return None, None, None
        except Exception as e:
            logger.error(f"Error searching YouTube Music: {e}")
            return None, None, None

    # ------------------------------------------------------------------
    # Media info
    # ------------------------------------------------------------------

    def get_media_info(self, url: str, config: dict | None = None) -> dict | None:
        """Return basic info (title, thumbnail, author, duration) for a single item."""
        if not url:
            return None
        probe_dir: Path | None = None
        cookie_file: Path | None = None
        try:
            if config:
                probe_dir = Path(tempfile.mkdtemp(prefix="offliner-probe-"))
                cookie_file = self._setup_cookies(config, probe_dir)

            source = self.detect_url_source(url)
            if source == "spotify":
                return self._get_spotify_info(url)
            return self._get_youtube_info(url, source or "youtube", cookie_file)
        except Exception as e:
            logger.error(f"Error getting media info: {e}")
            return None
        finally:
            if probe_dir and probe_dir.exists():
                shutil.rmtree(probe_dir, ignore_errors=True)

    def _get_youtube_info(
        self, url: str, source: str = "youtube", cookie_file: Path | None = None
    ) -> dict | None:
        try:
            opts = self._base_ytdlp_opts(cookie_file)
            if cookie_file:
                # For metadata probes with authenticated cookies, let yt-dlp
                # auto-select the client surface; forcing a specific client can
                # return 400/sign-in errors in some accounts.
                opts.pop("extractor_args", None)
            opts.update(
                {
                    "extract_flat": False,
                    "skip_download": True,
                    # Probing metadata should not fail just because a selected
                    # format is unavailable for the current auth context.
                    "check_formats": None,
                }
            )
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return None
                dur_s = info.get("duration", 0) or 0
                dur_str = f"{dur_s // 60}:{dur_s % 60:02d}" if dur_s else "0:00"
                thumb = ""
                for t in reversed(info.get("thumbnails", [])):
                    if t.get("url"):
                        thumb = t["url"]
                        break
                thumb = thumb or info.get("thumbnail", "")
                return {
                    "titulo": info.get("title", "Sin título"),
                    "thumbnail": thumb,
                    "autor": info.get("uploader", info.get("channel", "Desconocido")),
                    "duracion": dur_str,
                    "duracion_segundos": dur_s,
                    "fuente": source,
                }
        except Exception as e:
            logger.error(f"Error getting YouTube info: {e}")
            return None

    def _get_spotify_info(self, url: str) -> dict | None:
        client = self._get_spotify_client({})
        if not client:
            logger.warning("Spotify not configured")
            return None
        try:
            if "/track/" in url:
                tid = url.split("/track/")[1].split("?")[0].split("/")[0]
                track = client.track(tid)
                if not track:
                    return None
                artists = (
                    ", ".join(
                        a["name"] for a in track.get("artists", []) if a.get("name")
                    )
                    or "Desconocido"
                )
                imgs = track.get("album", {}).get("images", [])
                dur_ms = track.get("duration_ms", 0)
                dur_s = dur_ms // 1000
                return {
                    "titulo": track.get("name", "Sin título"),
                    "thumbnail": imgs[0]["url"] if imgs else "",
                    "autor": artists,
                    "duracion": f"{dur_s // 60}:{dur_s % 60:02d}",
                    "duracion_segundos": dur_s,
                    "fuente": "spotify",
                }
            elif "/album/" in url:
                aid = url.split("/album/")[1].split("?")[0].split("/")[0]
                album = client.album(aid)
                if not album:
                    return None
                artists = (
                    ", ".join(
                        a["name"] for a in album.get("artists", []) if a.get("name")
                    )
                    or "Desconocido"
                )
                imgs = album.get("images", [])
                total = album.get("total_tracks", 0)
                return {
                    "titulo": album.get("name", "Sin título"),
                    "thumbnail": imgs[0]["url"] if imgs else "",
                    "autor": artists,
                    "duracion": f"{total} tracks",
                    "duracion_segundos": 0,
                    "fuente": "spotify",
                    "es_playlist": True,
                }
            return None
        except Exception as e:
            logger.error(f"Error getting Spotify info: {e}")
            return None

    # ------------------------------------------------------------------
    # Playlist info
    # ------------------------------------------------------------------

    def get_playlist_info(self, url: str, config: dict | None = None) -> dict | None:
        """Full playlist metadata + item list (YouTube / YTM / Spotify)."""
        probe_dir: Path | None = None
        cookie_file: Path | None = None
        try:
            if config:
                probe_dir = Path(tempfile.mkdtemp(prefix="offliner-probe-"))
                cookie_file = self._setup_cookies(config, probe_dir)

            if "spotify.com" in url:
                if "/playlist/" in url:
                    return self._spotify_playlist_info(url)
                if "/album/" in url:
                    return self._spotify_album_info(url)

            is_ytm = "music.youtube.com" in url
            playlist_id: str | None = None
            if "list=" in url:
                parsed = urllib.parse.urlparse(url)
                params = urllib.parse.parse_qs(parsed.query)
                playlist_id = params.get("list", [None])[0]

            if is_ytm and self.ytmusic and playlist_id:
                result = self._ytmusic_playlist_info(playlist_id)
                if result:
                    return result

            normalized = (
                url.replace("music.youtube.com", "www.youtube.com") if is_ytm else url
            )
            return self._ytdlp_playlist_info(normalized, cookie_file)
        except Exception as e:
            logger.error(f"Error getting playlist info: {e}")
            return None
        finally:
            if probe_dir and probe_dir.exists():
                shutil.rmtree(probe_dir, ignore_errors=True)

    # --- Spotify playlist / album info ----------------------------------------

    def _spotify_playlist_info(self, url: str) -> dict | None:
        client = self._get_spotify_client({})
        if not client:
            logger.error("Spotify client not available")
            return None
        try:
            pid = url.split("/playlist/")[1].split("?")[0].split("/")[0]
            logger.info(f"Getting Spotify playlist info: {pid}")
            playlist = client.playlist(pid)
            if not playlist:
                return None

            info: dict[str, Any] = {
                "titulo": playlist.get("name", "Playlist sin título"),
                "descripcion": playlist.get("description", ""),
                "autor": playlist.get("owner", {}).get("display_name", "Desconocido"),
                "total": playlist.get("tracks", {}).get("total", 0),
                "thumbnail": (
                    playlist.get("images", [{}])[0].get("url", "")
                    if playlist.get("images")
                    else ""
                ),
                "items": [],
            }

            offset, limit = 0, 100
            while True:
                page = client.playlist_tracks(
                    pid,
                    offset=offset,
                    limit=limit,
                    fields="items(track(id,name,artists,duration_ms,album(images))),next,total",
                )
                if not page or not page.get("items"):
                    break
                for item in page["items"]:
                    track = item.get("track")
                    if not track or not track.get("id"):
                        continue
                    info["items"].append(self._spotify_track_to_item(track))
                if not page.get("next"):
                    break
                offset += limit

            info["total"] = len(info["items"])
            logger.info(
                f"Spotify playlist obtained: '{info['titulo']}' "
                f"with {info['total']} tracks"
            )
            return info
        except Exception as e:
            logger.error(f"Error getting Spotify playlist: {e}")
            return None

    def _spotify_album_info(self, url: str) -> dict | None:
        client = self._get_spotify_client({})
        if not client:
            logger.error("Spotify client not available")
            return None
        try:
            aid = url.split("/album/")[1].split("?")[0].split("/")[0]
            logger.info(f"Getting Spotify album info: {aid}")
            album = client.album(aid)
            if not album:
                return None

            album_artist = (
                ", ".join(a["name"] for a in album.get("artists", [])) or "Desconocido"
            )
            thumb = (
                album.get("images", [{}])[0].get("url", "")
                if album.get("images")
                else ""
            )

            info: dict[str, Any] = {
                "titulo": album.get("name", "Album sin título"),
                "descripcion": "",
                "autor": album_artist,
                "total": album.get("total_tracks", 0),
                "thumbnail": thumb,
                "items": [],
            }

            offset, limit = 0, 50
            while True:
                page = client.album_tracks(aid, offset=offset, limit=limit)
                if not page or not page.get("items"):
                    break
                for track in page["items"]:
                    if not track or not track.get("id"):
                        continue
                    item = self._spotify_track_to_item(track)
                    # Album tracks don't carry album images; reuse the album thumb
                    item["thumbnail"] = thumb
                    info["items"].append(item)
                if not page.get("next"):
                    break
                offset += limit

            info["total"] = len(info["items"])
            logger.info(
                f"Spotify album obtained: '{info['titulo']}' "
                f"with {info['total']} tracks"
            )
            return info
        except Exception as e:
            logger.error(f"Error getting Spotify album: {e}")
            return None

    @staticmethod
    def _spotify_track_to_item(track: dict) -> dict:
        """Convert a Spotify track dict into a standard playlist item dict."""
        artists = ", ".join(
            a.get("name", "") for a in track.get("artists", []) if a.get("name")
        )
        dur_s = (track.get("duration_ms", 0) or 0) // 1000
        album_imgs = track.get("album", {}).get("images", [])
        return {
            "id": track["id"],
            "titulo": track.get("name", "Sin título"),
            "url": f"https://open.spotify.com/track/{track['id']}",
            "duracion": f"{dur_s // 60}:{dur_s % 60:02d}",
            "duracion_segundos": dur_s,
            "thumbnail": album_imgs[0].get("url", "") if album_imgs else "",
            "autor": artists,
        }

    # --- YouTube Music playlist info ------------------------------------------

    def _ytmusic_playlist_info(self, playlist_id: str) -> dict | None:
        try:
            logger.info(
                f"Getting YouTube Music playlist using ytmusicapi: {playlist_id}"
            )
            pl = self.ytmusic.get_playlist(playlist_id, limit=None)  # type: ignore[union-attr]
            if not pl or "tracks" not in pl:
                return None

            info: dict[str, Any] = {
                "titulo": pl.get("title", "Playlist sin título"),
                "descripcion": pl.get("description", ""),
                "autor": pl.get("author", {}).get("name", "Desconocido"),
                "total": len(pl.get("tracks", [])),
                "thumbnail": (
                    pl.get("thumbnails", [{}])[-1].get("url", "")
                    if pl.get("thumbnails")
                    else ""
                ),
                "items": [],
            }

            for t in pl.get("tracks", []):
                vid = t.get("videoId", "")
                if not vid:
                    continue
                dur_txt = t.get("duration", "")
                dur_s = self._parse_duration_str(dur_txt)
                artists = ", ".join(
                    a.get("name", "") for a in t.get("artists", []) if a.get("name")
                )
                thumbs = t.get("thumbnails", [])
                thumb = (
                    thumbs[-1].get("url", "")
                    if thumbs
                    else f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
                )
                info["items"].append(
                    {
                        "id": vid,
                        "video_id": vid,
                        "titulo": t.get("title", "Sin título"),
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "duracion": dur_txt or "--:--",
                        "duracion_segundos": dur_s,
                        "thumbnail": thumb,
                        "autor": artists,
                    }
                )

            info["total"] = len(info["items"])
            logger.info(
                f"Playlist obtained via ytmusicapi: '{info['titulo']}' "
                f"with {info['total']} items"
            )
            return info
        except Exception as e:
            logger.warning(f"Error with ytmusicapi, using yt-dlp: {e}")
            return None

    # --- yt-dlp playlist info -------------------------------------------------

    def _ytdlp_playlist_info(
        self, url: str, cookie_file: Path | None = None
    ) -> dict | None:
        opts = self._base_ytdlp_opts(cookie_file)
        if cookie_file:
            opts.pop("extractor_args", None)
        opts.update(
            {
                "extract_flat": "in_playlist",
                "skip_download": True,
                "ignoreerrors": True,
                "playlistend": None,
                "extractor_retries": 3,
                "socket_timeout": 30,
                "check_formats": None,
            }
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.extract_info(url, download=False)
            if not result:
                return None
            if result.get("_type") != "playlist" and "entries" not in result:
                return None

            info: dict[str, Any] = {
                "titulo": result.get("title", "Playlist sin título"),
                "descripcion": result.get("description", ""),
                "autor": result.get("uploader", result.get("channel", "Desconocido")),
                "total": result.get("playlist_count", 0),
                "thumbnail": result.get("thumbnail", ""),
                "items": [],
            }

            entries = result.get("entries", [])
            if entries and not isinstance(entries, list):
                entries = list(entries)

            for entry in entries or []:
                if entry is None:
                    continue
                dur_s = entry.get("duration", 0) or 0
                dur_str = (
                    f"{int(dur_s // 60)}:{int(dur_s % 60):02d}" if dur_s else "--:--"
                )
                vid = entry.get("id", entry.get("url", ""))
                if vid and not vid.startswith("http"):
                    v_url = f"https://www.youtube.com/watch?v={vid}"
                else:
                    v_url = entry.get("url", "")
                thumb = entry.get("thumbnail", "") or (
                    f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else ""
                )
                info["items"].append(
                    {
                        "id": vid,
                        "video_id": vid,
                        "titulo": entry.get("title", "Sin título"),
                        "url": v_url,
                        "duracion": dur_str,
                        "duracion_segundos": dur_s,
                        "thumbnail": thumb,
                        "autor": entry.get("uploader", entry.get("channel", "")),
                    }
                )

            info["total"] = len(info["items"])
            logger.info(
                f"Playlist obtained: '{info['titulo']}' " f"with {info['total']} items"
            )
            return info

    # ------------------------------------------------------------------
    # Playlist URL resolution (download-time)
    # ------------------------------------------------------------------

    def _resolve_playlist_urls(
        self, config: dict, platform: str, url: str
    ) -> list[str]:
        """Return a flat list of downloadable URLs from a playlist."""
        try:
            logger.info(f"Getting songs from {platform} playlist...")
            urls: list[str] = []

            if platform == "YouTube":
                opts = self._base_ytdlp_opts()
                opts.update({"extract_flat": True, "force_generic_extractor": True})
                with yt_dlp.YoutubeDL(opts) as ydl:
                    result = ydl.extract_info(url, download=False)
                    if result and "entries" in result:
                        urls = [e["url"] for e in result["entries"]]

            elif platform == "Spotify":
                client = self._get_spotify_client(config)
                if not client:
                    logger.error("Spotify client not available")
                    return []
                tracks = self._collect_spotify_tracks(client, url)
                if tracks:
                    queries = [f"{n} {a}" for n, a in tracks]
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        resolved = list(pool.map(self.search_youtube, queries))
                    urls.extend(u for u in resolved if u)

            logger.info(f"Got {len(urls)} video(s) from {platform} playlist")
            return urls
        except Exception as e:
            logger.error(f"Error getting playlist from {platform}: {e}")
            return []

    @staticmethod
    def _collect_spotify_tracks(
        client: spotipy.Spotify, url: str
    ) -> list[tuple[str, str]]:
        """Return ``[(track_name, artist_name), ...]`` from a Spotify URL."""
        tracks: list[tuple[str, str]] = []

        if "/playlist/" in url:
            pid = url.split("/playlist/")[1].split("?")[0].split("/")[0]
            offset = 0
            while True:
                page = client.playlist_items(pid, offset=offset)
                if not page or not page.get("items"):
                    break
                for item in page["items"]:
                    t = item.get("track")
                    if t and t.get("name") and t.get("artists"):
                        tracks.append((t["name"], t["artists"][0]["name"]))
                offset += len(page["items"])
                if offset >= page.get("total", 0):
                    break

        elif "/album/" in url:
            aid = url.split("/album/")[1].split("?")[0].split("/")[0]
            album = client.album(aid)
            if not album:
                return tracks
            fallback = ([a["name"] for a in album.get("artists", [])] or [""])[0]
            offset = 0
            while True:
                page = client.album_tracks(aid, offset=offset, limit=50)
                if not page or not page.get("items"):
                    break
                for t in page["items"]:
                    if t and t.get("name"):
                        a = t["artists"][0]["name"] if t.get("artists") else fallback
                        tracks.append((t["name"], a))
                if not page.get("next"):
                    break
                offset += len(page["items"])

        return tracks

    # ------------------------------------------------------------------
    # yt-dlp progress hooks (for SSE real-time updates)
    # ------------------------------------------------------------------

    def _make_progress_hook(self, request_id: str) -> Callable:
        """Create a yt-dlp progress_hook that writes to DownloadProgressStore."""

        def hook(d: dict) -> None:
            # If cancellation has been requested (client disconnected), raise to abort yt-dlp
            try:
                if DownloadProgressStore.is_cancelled(request_id):
                    raise yt_dlp.utils.DownloadError("Cancelled by client disconnect")
            except Exception:
                # In case yt_dlp references aren't available yet or other errors,
                # just raise a generic exception to abort.
                raise
            status = d.get("status", "")
            if status == "downloading":
                downloaded = d.get("downloaded_bytes", 0) or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                item_pct = (downloaded / total * 100) if total > 0 else 0

                store = DownloadProgressStore.get(request_id)
                completed = store.get("completed_items", 0)
                total_items = max(store.get("total_items", 1), 1)

                # Map to 15-90% range
                overall = 15 + ((completed + item_pct / 100) / total_items) * 75

                speed = d.get("speed")
                speed_str = self._format_speed(speed) if speed else ""
                eta = d.get("eta")
                eta_str = self._format_eta(eta) if eta else ""
                filename = Path(d.get("filename", "")).stem

                DownloadProgressStore.update(
                    request_id,
                    percent=min(int(overall), 90),
                    status="Downloading...",
                    detail=filename[:60] if filename else "",
                    speed=speed_str,
                    eta=eta_str,
                    current_file=filename,
                    phase="downloading",
                )
            elif status == "finished":
                filename = Path(d.get("filename", "")).stem
                DownloadProgressStore.update(
                    request_id,
                    status="Converting...",
                    detail=f"Processing {filename[:50]}",
                    phase="converting",
                    speed="",
                    eta="",
                )

        return hook

    def _make_postprocessor_hook(self, request_id: str) -> Callable:
        """Create a yt-dlp postprocessor_hook for SSE status updates."""

        def hook(d: dict) -> None:
            status = d.get("status", "")
            pp = d.get("postprocessor", "")
            if status == "started" and pp:
                label = (
                    pp.replace("FFmpeg", "")
                    .replace("Extract", "Extracting ")
                    .replace("Embed", "Embedding ")
                    .replace("Metadata", "metadata")
                    .strip()
                    or pp
                )
                DownloadProgressStore.update(
                    request_id,
                    status="Processing...",
                    detail=label,
                    phase="converting",
                )

        return hook

    @staticmethod
    def _format_speed(speed_bps: float | None) -> str:
        """Format bytes/sec into a human-readable string."""
        if not speed_bps:
            return ""
        if speed_bps >= 1_048_576:
            return f"{speed_bps / 1_048_576:.1f} MB/s"
        if speed_bps >= 1024:
            return f"{speed_bps / 1024:.0f} KB/s"
        return f"{int(speed_bps)} B/s"

    @staticmethod
    def _format_eta(seconds: float | int | None) -> str:
        """Format ETA seconds into a human-readable string."""
        if not seconds or seconds < 0:
            return ""
        seconds = int(seconds)
        if seconds >= 3600:
            return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
        if seconds >= 60:
            return f"{seconds // 60}m {seconds % 60}s"
        return f"{seconds}s"

    # ------------------------------------------------------------------
    # Unified media download  (DRY merge of audio + video)
    # ------------------------------------------------------------------

    def _download_media(
        self,
        url: str,
        format_mode: str,  # "audio" | "video"
        config: dict,
        result: _DownloadResult,
        session_dir: Path,
        cookie_file: Path | None = None,
    ) -> None:
        """Download a single item as audio or video into *session_dir*.

        Handles Spotify conversion, ffmpeg check, quality selection,
        SponsorBlock, metadata embedding, and sidecar cleanup.
        All files are written strictly inside ``session_dir``.
        """
        is_audio = format_mode == "audio"

        try:
            # --- Spotify URL conversion ---
            if "spotify.com" in url and "/track/" in url:
                logger.info("Detected Spotify URL, converting to YouTube")
                url = self._resolve_spotify_track(config, url)
                if not url:
                    result.inc_error(format_mode)
                    return

            # --- ffmpeg check (warn if missing, but don't abort) ---
            if not self._ffmpeg_available:
                logger.warning(
                    "ffmpeg is not installed or not in PATH; some post-processing may fail."
                )

            # --- Pre-flight metadata extraction ---
            try:
                with yt_dlp.YoutubeDL(self._base_ytdlp_opts(cookie_file)) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception:
                if not cookie_file:
                    raise
                # Retry once without forced extractor_args for cookie-auth flows.
                fb_opts = self._base_ytdlp_opts(cookie_file)
                fb_opts.pop("extractor_args", None)
                with yt_dlp.YoutubeDL(fb_opts) as ydl:
                    info = ydl.extract_info(url, download=False)

            def _has_playable_formats(data: dict) -> bool:
                fmts = data.get("formats", []) or []
                return any(
                    (f.get("vcodec") and f.get("vcodec") != "none")
                    or (f.get("acodec") and f.get("acodec") != "none")
                    for f in fmts
                )

            if not _has_playable_formats(info):
                # Try one fallback without forced player_client in case cookies are valid
                # but the selected client surface returned only storyboards.
                if cookie_file:
                    fb_opts = self._base_ytdlp_opts(cookie_file)
                    fb_opts.pop("extractor_args", None)
                    with yt_dlp.YoutubeDL(fb_opts) as ydl:
                        info = ydl.extract_info(url, download=False)

                if not _has_playable_formats(info):
                    logger.error(
                        "No playable formats were returned (video likely needs valid logged-in cookies)."
                    )
                    result.inc_error(format_mode)
                    if result.request_id:
                        DownloadProgressStore.update(
                            result.request_id,
                            status="Error",
                            detail="No playable formats. Check your cookies.",
                            phase="error",
                        )
                    return

            title = info.get("title", "Unknown Title")
            uploader = info.get("uploader", "Unknown Uploader")
            download_url = url

            # --- YouTube Music preference (audio only) ---
            if is_audio and config.get("Preferir_YouTube_Music", False):
                logger.info(f"Searching for pure audio on YouTube Music: '{title}'")
                ytm_url, _, _ = self.search_youtube_music(title, uploader)
                if ytm_url:
                    download_url = ytm_url

            # --- Clean title for filename ---
            clean_title = self._sanitize_filename(
                title if is_audio else f"{title} - {uploader}"
            )

            # --- Output directory — always session_dir ---
            out_dir = self._ensure_dir(session_dir)

            # --- Format / quality ---
            quality_key = config.get("Calidad_audio_video", "avg")

            if is_audio:
                _AUDIO_QUALITY: dict[str, tuple[str, str]] = {
                    "min": ("worstaudio[abr<=96]/worstaudio/worst", "64"),
                    "avg": (
                        "bestaudio[abr<=160]/bestaudio[abr<=192]/bestaudio/best",
                        "128",
                    ),
                    "max": ("bestaudio/best", "320"),
                }
                fmt_str, quality_val = _AUDIO_QUALITY.get(
                    quality_key, _AUDIO_QUALITY["avg"]
                )
                file_format = config.get("Formato_audio", "mp3")
            else:
                # Prefer audio formats compatible with MP4 (e.g. m4a/AAC)
                # when the requested container is mp4; otherwise allow
                # broader selection (webm/opus etc.). This avoids producing
                # MP4 files with Opus audio which some players (Windows)
                # cannot play.
                file_format = config.get("Formato_video", "mp4")

                if file_format == "mp4":
                    _VIDEO_QUALITY: dict[str, str] = {
                        "min": "worstvideo[ext=mp4]+worstaudio[ext=m4a]/worst[ext=mp4]/worst",
                        "avg": "bestvideo[height<=1080]+bestaudio[ext=m4a]/bestaudio[height<=1080]/best[height<=1080]",
                        "max": "bestvideo+bestaudio[ext=m4a]/bestaudio/best",
                    }
                else:
                    _VIDEO_QUALITY = {
                        "min": "worstvideo[ext=mp4]+worstaudio[ext=m4a]/worst[ext=mp4]/worst",
                        "avg": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
                        "max": "bestvideo+bestaudio/best",
                    }

                fmt_str = _VIDEO_QUALITY.get(quality_key, _VIDEO_QUALITY["avg"])
                quality_val = ""  # unused for video

            logger.info(f"Downloading {format_mode}: '{title}'")

            # --- Output template ---
            # Use yt-dlp template macros so its internal postprocessors
            # manage final filenames and thumbnail embedding.
            outtmpl = str(out_dir / "%(title)s - %(uploader)s.%(ext)s")

            # --- Postprocessors ---
            postprocessors = list(self._sponsorblock_postprocessors(config))

            if is_audio:
                postprocessors.append(
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": file_format,
                        "preferredquality": quality_val,
                    }
                )

            # Common: metadata + thumbnail conversion. EmbedThumbnail is only
            # added when the final container/codec supports embedded cover art
            # (yt-dlp/ffmpeg will error otherwise — e.g. WAV does not support it).
            common_pp = [
                {
                    "key": "FFmpegMetadata",
                    "add_chapters": True,
                    "add_metadata": True,
                },
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
            ]

            # Supported targets for thumbnail embedding (yt-dlp/ffmpeg)
            fmt = (file_format or "").lower()
            if is_audio:
                embed_supported = fmt in ("mp3", "ogg", "opus", "flac", "m4a")
            else:
                embed_supported = fmt in ("mp4", "m4v", "mov", "mkv", "mka")

            if embed_supported:
                common_pp.append({"key": "EmbedThumbnail"})

            postprocessors.extend(common_pp)

            # --- yt-dlp options (with cookie support) ---
            ydl_opts = self._base_ytdlp_opts(cookie_file)
            ydl_opts.update(
                {
                    "format": fmt_str,
                    "postprocessors": postprocessors,
                    "outtmpl": outtmpl,
                    "trim_file_name_length": 184,
                    "updatetime": False,
                    "writethumbnail": True,
                    "parse_metadata": [
                        "%(artist,uploader)s:%(artist)s",
                        "%(album,title)s:%(album)s",
                        "%(title)s:%(title)s",
                    ],
                }
            )

            # Improve compatibility for MP3 cover-art tags on Windows players.
            # These ffmpeg options are applied to yt-dlp postprocessing steps
            # and help ensure ID3v2 artwork is recognized reliably.
            if is_audio and file_format.lower() == "mp3":
                ydl_opts["postprocessor_args"] = [
                    "-id3v2_version",
                    "3",
                    "-write_id3v1",
                    "1",
                ]

            if not is_audio:
                video_opts: dict = {
                    "format_sort": ["vext:mp4", "aext:m4a", "aext:mp3"],
                    "merge_output_format": file_format,
                    "concurrent_fragment_downloads": 3,
                    "retries": 3,
                }
                ydl_opts.update(video_opts)

            # --- yt-dlp progress hooks (SSE real-time updates) ---
            if result.request_id:
                ydl_opts["progress_hooks"] = [
                    self._make_progress_hook(result.request_id)
                ]
                ydl_opts["postprocessor_hooks"] = [
                    self._make_postprocessor_hook(result.request_id)
                ]

            # --- Download ---
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    dl_info = ydl.extract_info(download_url, download=True)
            except Exception as e:
                msg = str(e)
                if (
                    "SponsorBlock" in msg
                    or "SponsorBlockPP" in msg
                    or "unexpected keyword argument 'action'" in msg
                ):
                    logger.warning(
                        f"SponsorBlock postprocessor failed: {e}. Retrying without SponsorBlock."
                    )
                    if result.request_id:
                        DownloadProgressStore.update(
                            result.request_id,
                            detail="SponsorBlock falló; continuando sin SponsorBlock.",
                        )

                    # Remove SponsorBlock postprocessors and retry once
                    filtered_pp = [
                        p
                        for p in postprocessors
                        if not (isinstance(p, dict) and p.get("key") == "SponsorBlock")
                    ]
                    ydl_opts["postprocessors"] = filtered_pp
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            dl_info = ydl.extract_info(download_url, download=True)
                    except Exception as e2:
                        logger.error(
                            f"Download failed after disabling SponsorBlock: {e2}"
                        )
                        raise
                else:
                    raise

            # --- Locate output file (robust detection) ---
            final_path: Path | None = None

            # 1) Prefer explicit paths reported by yt-dlp (requested_downloads)
            if dl_info:
                requested = dl_info.get("requested_downloads")
                if requested and isinstance(requested, list):
                    for r in requested:
                        fp = r.get("filepath") or r.get("filename")
                        if not fp:
                            continue
                        candidate = Path(fp)
                        if candidate.exists():
                            # If a postprocessor converted audio, check for the
                            # converted extension next to the originally
                            # downloaded file (common with FFmpegExtractAudio).
                            if is_audio and file_format:
                                alt = candidate.with_suffix(f".{file_format}")
                                if alt.exists():
                                    final_path = alt
                                    break
                            final_path = candidate
                            break

            # 2) Fallback: scan output directory for matching basename
            if final_path is None:
                candidates = list(out_dir.glob(f"{clean_title}.*"))
                # Exclude known sidecar extensions
                candidates = [
                    p
                    for p in candidates
                    if p.suffix.lower() not in self._SIDECAR_EXTENSIONS
                ]
                if is_audio and file_format:
                    # Prefer exact extension match
                    for p in candidates:
                        if p.suffix.lower() == f".{file_format}".lower():
                            final_path = p
                            break
                if final_path is None and candidates:
                    # Pick the largest candidate (likely the media file)
                    candidates.sort(
                        key=lambda p: p.stat().st_size if p.exists() else 0,
                        reverse=True,
                    )
                    final_path = candidates[0]

            # 3) Last-resort: reconstruct expected path
            if final_path is None:
                if is_audio:
                    final_path = out_dir / f"{clean_title}.{file_format}"
                else:
                    final_path = self._resolve_video_output(
                        dl_info, out_dir, clean_title, file_format
                    )

            if final_path and final_path.exists():
                # Rely on yt-dlp's postprocessors (EmbedThumbnail) to have
                # embedded the cover art. Clean sidecars and record result.
                self._cleanup_sidecars(final_path)
                result.inc_success(format_mode)
                result.add_file(str(final_path))
                logger.info(f"{format_mode.capitalize()} downloaded: '{title}'")
            else:
                logger.error(f"Output file not found after download: {final_path}")
                result.inc_error(format_mode)

        except Exception as e:
            logger.error(f"Error downloading {format_mode}: {e}")
            result.inc_error(format_mode)

    @staticmethod
    def _resolve_video_output(
        dl_info: dict | None,
        out_dir: Path,
        clean_title: str,
        file_format: str,
    ) -> Path:
        """Determine the actual video file path produced by yt-dlp."""
        if dl_info:
            requested = dl_info.get("requested_downloads")
            if requested and isinstance(requested, list) and requested:
                fp = requested[0].get("filepath") or requested[0].get("filename")
                if fp:
                    return Path(fp)
        return out_dir / f"{clean_title}.{file_format}"

    def _cleanup_sidecars(self, filepath: Path) -> None:
        """Remove leftover thumbnail / subtitle sidecar files.

        Handles both plain sidecars (``video.jpg``) and language-suffixed
        subtitle sidecars produced by yt-dlp (``video.en.srt``,
        ``video.es-orig.vtt``, etc.).
        """
        stem = filepath.stem
        parent = filepath.parent
        for ext in self._SIDECAR_EXTENSIONS:
            # Plain sidecar: video.srt
            sidecar = filepath.with_suffix(ext)
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except OSError:
                    pass
            # Language-suffixed sidecars: video.en.srt, video.es-orig.vtt
            for lang_file in parent.glob(f"{stem}.*{ext}"):
                if lang_file != filepath:
                    try:
                        lang_file.unlink()
                    except OSError:
                        pass

    # ------------------------------------------------------------------
    # Compression
    # ------------------------------------------------------------------

    def compress_files(
        self,
        zip_name: str,
        files: list[str],
        output_folder: Path | str | None = None,
    ) -> str | None:
        """Compress *files* into a ZIP, delete originals, return ZIP path."""
        try:
            out = self._ensure_dir(
                output_folder or self._base_dir / "Downloads" / "Zip"
            )
            clean_name = self._sanitize_filename(zip_name)
            if clean_name.lower().endswith(".zip"):
                clean_name = clean_name[:-4]
            zip_path = out / f"{clean_name}.zip"

            with zipfile.ZipFile(zip_path, "w") as zf:
                for f in files:
                    zf.write(f, self._sanitize_filename(Path(f).name))
            for f in files:
                Path(f).unlink(missing_ok=True)

            logger.info(f"Files compressed to '{zip_path.name}'")
            return str(zip_path.resolve())
        except Exception as e:
            logger.error(f"Error compressing files: {e}")
            return None

    # ------------------------------------------------------------------
    # Parallel task execution
    # ------------------------------------------------------------------

    def _run_download_tasks(
        self,
        tasks: list[tuple[str, str, dict]],
        result: _DownloadResult,
        session_dir: Path,
        cookie_file: Path | None,
        max_workers: int,
    ) -> None:
        """Fan out ``(url, mode, config)`` tasks across a thread pool.

        Progress is reported via ``result.progress_callback`` in a
        thread-safe manner.
        """
        if not tasks:
            return

        result.total_items = max(len(tasks), 1)
        if result.request_id:
            DownloadProgressStore.update(
                result.request_id, total_items=result.total_items
            )
        callback = result.progress_callback

        def _worker(task: tuple[str, str, dict]) -> None:
            url, mode, cfg = task
            if callback:
                callback(
                    result.get_progress_pct(),
                    f"Downloading {mode}...",
                    f"Processing...",
                )
            # Check cancellation before starting this task
            if result.request_id and DownloadProgressStore.is_cancelled(
                result.request_id
            ):
                logger.info(f"Skipping task due to cancellation: {url}")
                return

            self._download_media(url, mode, cfg, result, session_dir, cookie_file)
            result.inc_completed()
            if result.request_id:
                DownloadProgressStore.update(
                    result.request_id,
                    completed_items=result.completed_items,
                )

        effective_workers = min(max_workers, len(tasks))

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=effective_workers
        ) as executor:
            futures = [executor.submit(_worker, t) for t in tasks]
            # Wait and propagate worker-level exceptions as log entries
            for fut in concurrent.futures.as_completed(futures):
                # If cancellation was requested globally, attempt to cancel remaining futures
                if result.request_id and DownloadProgressStore.is_cancelled(
                    result.request_id
                ):
                    logger.info("Cancellation requested - cancelling remaining tasks")
                    for f in futures:
                        if not f.done():
                            try:
                                f.cancel()
                            except Exception:
                                pass
                    # Update progress store to reflect cancellation
                    if result.request_id:
                        DownloadProgressStore.update(
                            result.request_id,
                            status="Cancelled",
                            detail="Cancelled by client disconnect",
                            complete=True,
                            error="Cancelled by client disconnect",
                            percent=100,
                            phase="cancelled",
                        )
                    break

                try:
                    fut.result()
                except Exception as exc:
                    logger.error(f"Download worker exception: {exc}")

    # ------------------------------------------------------------------
    # Entry points  (UUID isolation + cookie + parallel)
    # ------------------------------------------------------------------

    def download_with_progress(
        self,
        config: dict,
        data: str,
        filename: str = "archivos.zip",
        progress_callback: Callable | None = None,
        base_folder: str | None = None,
        request_id: str | None = None,
    ) -> str | None:
        """Auto-detect URL type and download with progress reporting.

        When *base_folder* is supplied (e.g. by ``routes.py``), it is used
        directly and the **caller** is responsible for cleanup.  Otherwise a
        UUID-scoped temporary directory is created and cleaned automatically
        via ``try … finally``.
        """
        # --- Session directory & ownership ---
        if base_folder:
            session_dir = Path(base_folder)
            owns_session = False
        else:
            session_id = uuid.uuid4().hex
            session_dir = self._base_dir / "Downloads" / "Temp" / session_id
            owns_session = True

        try:
            self._ensure_dir(session_dir)

            # --- Per-session cookie provisioning ---
            cookie_file = self._setup_cookies(config, session_dir)

            result = _DownloadResult(progress_callback, request_id=request_id)
            urls: list[str] = []

            if progress_callback:
                progress_callback(5, "Preparing...", "Analyzing request")

            # --- URL resolution (unchanged logic) ---
            data_lower = (data or "").lower()
            is_yt = any(
                p in data_lower
                for p in ("youtube.com", "youtu.be", "music.youtube.com")
            )
            is_sp = "spotify.com" in data_lower

            if is_sp:
                if self.is_playlist_url(data):
                    if progress_callback:
                        progress_callback(
                            10, "Getting playlist...", "Connecting to Spotify"
                        )
                    urls.extend(self._resolve_playlist_urls(config, "Spotify", data))
                else:
                    if progress_callback:
                        progress_callback(
                            10, "Processing link...", "Spotify URL detected"
                        )
                    yt_url = self._resolve_spotify_track(config, data)
                    if yt_url:
                        urls.append(yt_url)
            elif is_yt:
                if self.is_playlist_url(data):
                    if progress_callback:
                        progress_callback(
                            10,
                            "Getting playlist...",
                            "Connecting to YouTube",
                        )
                    urls.extend(self._resolve_playlist_urls(config, "YouTube", data))
                else:
                    if progress_callback:
                        progress_callback(
                            10, "Processing link...", "YouTube URL detected"
                        )
                    urls.append(data)
            elif data:
                if progress_callback:
                    progress_callback(
                        10, "Searching YouTube...", f"Searching: {data[:50]}"
                    )
                if config.get("Preferir_YouTube_Music"):
                    ytm_url, _, _ = self.search_youtube_music(data, data)
                    if ytm_url:
                        urls.append(ytm_url)
                    else:
                        link = self.search_youtube(data)
                        if link:
                            urls.append(link)
                else:
                    link = self.search_youtube(data)
                    if link:
                        urls.append(link)

            if not urls:
                if progress_callback:
                    progress_callback(100, "No results", "No URLs to download found")
                return None

            if progress_callback:
                progress_callback(
                    15, "Starting downloads...", f"{len(urls)} item(s) found"
                )

            # --- Build task list & run in parallel ---
            tasks: list[tuple[str, str, dict]] = []
            for url in urls:
                if not url:
                    continue
                if config.get("Descargar_video"):
                    tasks.append((url, "video", config))
                if config.get("Descargar_audio"):
                    tasks.append((url, "audio", config))

            max_workers = config.get("max_download_workers", self._DEFAULT_MAX_WORKERS)

            start = time.time()
            self._run_download_tasks(
                tasks, result, session_dir, cookie_file, max_workers
            )
            logger.info(f"Execution time: {time.time() - start:.2f} seconds")

            # If cancellation was requested during task execution, abort early
            if result.request_id and DownloadProgressStore.is_cancelled(
                result.request_id
            ):
                logger.info("Download cancelled by client - aborting before finalize")
                if progress_callback:
                    progress_callback(100, "Cancelled", "Cancelled by client")
                return None

            if progress_callback:
                progress_callback(90, "Finishing...", "Processing downloaded files")
            return self._finalize(
                result, filename, session_dir, progress_callback, owns_session
            )

        except Exception as e:
            logger.error(f"Error in download process: {e}")
            if progress_callback:
                progress_callback(100, "Error", str(e)[:100])
            return None
        finally:
            if owns_session:
                shutil.rmtree(session_dir, ignore_errors=True)
                logger.debug(f"Session directory cleaned: {session_dir.name}")

    def download_selective(
        self,
        config: dict,
        selected_urls: list[str],
        filename: str = "archivos.zip",
        progress_callback: Callable | None = None,
        base_folder: str | None = None,
        item_configs: dict | None = None,
        request_id: str | None = None,
    ) -> str | None:
        """Download selected URLs with optional per-item format overrides.

        Same UUID-isolation and cookie semantics as
        ``download_with_progress``.
        """
        item_configs = item_configs or {}

        # --- Session directory & ownership ---
        if base_folder:
            session_dir = Path(base_folder)
            owns_session = False
        else:
            session_id = uuid.uuid4().hex
            session_dir = self._base_dir / "Downloads" / "Temp" / session_id
            owns_session = True

        try:
            self._ensure_dir(session_dir)

            # --- Per-session cookie provisioning ---
            cookie_file = self._setup_cookies(config, session_dir)

            result = _DownloadResult(progress_callback, request_id=request_id)

            if progress_callback:
                progress_callback(5, "Preparing...", "Analyzing request")

            if not selected_urls:
                if progress_callback:
                    progress_callback(100, "No selection", "No URLs selected")
                return None

            if progress_callback:
                progress_callback(
                    15,
                    "Starting downloads...",
                    f"{len(selected_urls)} item(s) selected",
                )

            # --- Build task list with per-item overrides ---
            tasks: list[tuple[str, str, dict]] = []
            for url in selected_urls:
                if not url:
                    continue

                ic = item_configs.get(url, {})
                item_fmt = ic.get("format")

                # Build effective config with per-item format override
                effective_cfg = config.copy()
                file_fmt = ic.get("fileFormat")
                if file_fmt:
                    _AUDIO_EXTS = ("mp3", "m4a", "flac", "wav")
                    _VIDEO_EXTS = ("mp4", "mkv", "webm", "mov")
                    if file_fmt in _AUDIO_EXTS:
                        effective_cfg["Formato_audio"] = file_fmt
                    elif file_fmt in _VIDEO_EXTS:
                        effective_cfg["Formato_video"] = file_fmt

                # Determine modes to download
                modes: list[str] = []
                if item_fmt:
                    modes.append(item_fmt)
                else:
                    if config.get("Descargar_video"):
                        modes.append("video")
                    if config.get("Descargar_audio"):
                        modes.append("audio")

                for mode in modes:
                    tasks.append((url, mode, effective_cfg))

            max_workers = config.get("max_download_workers", self._DEFAULT_MAX_WORKERS)

            start = time.time()
            self._run_download_tasks(
                tasks, result, session_dir, cookie_file, max_workers
            )
            logger.info(f"Execution time: {time.time() - start:.2f} seconds")

            # If cancellation was requested during task execution, abort early
            if result.request_id and DownloadProgressStore.is_cancelled(
                result.request_id
            ):
                logger.info("Download cancelled by client - aborting before finalize")
                if progress_callback:
                    progress_callback(100, "Cancelled", "Cancelled by client")
                return None

            if progress_callback:
                progress_callback(90, "Finishing...", "Processing downloaded files")
            return self._finalize(
                result, filename, session_dir, progress_callback, owns_session
            )

        except Exception as e:
            logger.error(f"Error in selective download: {e}")
            if progress_callback:
                progress_callback(100, "Error", str(e)[:100])
            return None
        finally:
            if owns_session:
                shutil.rmtree(session_dir, ignore_errors=True)
                logger.debug(f"Session directory cleaned: {session_dir.name}")

    # ------------------------------------------------------------------
    # Shared helpers for entry points
    # ------------------------------------------------------------------

    def _finalize(
        self,
        result: _DownloadResult,
        filename: str,
        session_dir: Path,
        progress_callback: Callable | None,
        owns_session: bool = False,
    ) -> str | None:
        """Compress (if many files), log summary, return final path.

        When *owns_session* is ``True``, the final deliverable is copied to a
        permanent output directory **before** the session directory is removed
        by the caller's ``finally`` block.
        """
        files = result.canciones_descargadas

        if len(files) > 1:
            if progress_callback:
                progress_callback(92, "Compressing...", "Creating ZIP file")
            path = self.compress_files(filename, files, str(session_dir))
        elif len(files) == 1:
            path = str(Path(files[0]).resolve())
        else:
            path = None

        # When we own the session dir, move the deliverable out before cleanup
        if owns_session and path and Path(path).exists():
            output_dir = self._ensure_dir(self._base_dir / "Downloads" / "Output")
            dest = output_dir / Path(path).name
            # Avoid name collision
            if dest.exists():
                stem = dest.stem
                suffix = dest.suffix
                dest = output_dir / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"
            shutil.copy2(str(path), str(dest))
            path = str(dest)

        if progress_callback:
            progress_callback(98, "Done!", "Preparing download")

        self._log_summary(result)
        logger.info(f"File path: {path}")
        return path

    @staticmethod
    def _log_summary(result: _DownloadResult) -> None:
        a, v = result.audios_exito, result.videos_exito
        if a and v:
            logger.info(f"Downloaded {a} audios and {v} videos")
        elif a:
            logger.info(f"Downloaded {a} audios")
        elif v:
            logger.info(f"Downloaded {v} videos")

    @staticmethod
    def _parse_duration_str(text: str) -> int:
        """Parse ``"M:SS"`` or ``"H:MM:SS"`` into total seconds."""
        if not text:
            return 0
        parts = text.split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except ValueError:
            pass
        return 0


# ============================================
# RQ Task — standalone picklable function for the worker process
# ============================================


def execute_download_task(
    user_config: dict,
    input_url: str,
    task_id: str,
    nombre_archivo: str,
    temp_dir: str,
    is_playlist_mode: bool = False,
    selected_urls: list | None = None,
    item_configs: dict | None = None,
    redis_url: str | None = None,
) -> None:
    """Top-level task executed by an RQ worker.

    This function is intentionally defined at module level so that it is
    *picklable* by RQ.  It re-initialises the Redis connection inside the
    worker process (workers do NOT share memory with the Flask app) and
    delegates to the :class:`OfflinerCore` singleton for the actual download.

    Args:
        user_config: Validated user configuration dict.
        input_url: The URL or search query submitted by the user.
        task_id: Unique request_id (used as the Redis progress key).
        nombre_archivo: Desired output filename (ZIP name).
        temp_dir: Absolute path to the session-scoped temp directory.
        is_playlist_mode: Whether the request is a playlist selection.
        selected_urls: List of selected URLs (playlist mode only).
        item_configs: Per-item format overrides (playlist mode only).
        redis_url: Redis connection URL so the worker can write progress.
    """
    # Ensure the worker process has a valid Redis connection.
    if redis_url:
        init_redis(redis_url)

    try:

        def progress_callback(percent: int, status: str, detail: str = "") -> None:
            DownloadProgressStore.update(
                task_id,
                percent=percent,
                status=status,
                detail=detail,
            )

        if is_playlist_mode and selected_urls:
            result_path = _core.download_selective(
                user_config,
                selected_urls,
                nombre_archivo,
                progress_callback,
                base_folder=temp_dir,
                item_configs=item_configs,
                request_id=task_id,
            )
        else:
            result_path = _core.download_with_progress(
                user_config,
                input_url,
                nombre_archivo,
                progress_callback,
                base_folder=temp_dir,
                request_id=task_id,
            )

        if result_path and os.path.exists(result_path):
            logger.info(f"RQ task {task_id}: download completed → {result_path}")
            DownloadProgressStore.update(
                task_id,
                file_path=result_path,
                complete=True,
                percent=100,
                status="Done!",
                detail="Ready to download",
                phase="done",
                speed="",
                eta="",
            )
        else:
            if DownloadProgressStore.is_cancelled(task_id):
                DownloadProgressStore.update(
                    task_id,
                    error="Cancelled by client disconnect",
                    complete=True,
                    percent=100,
                    status="Cancelled",
                    detail="Cancelled by client disconnect",
                    phase="cancelled",
                )
            else:
                DownloadProgressStore.update(
                    task_id,
                    error="Could not download the file.",
                    complete=True,
                    percent=100,
                    status="Error",
                    phase="error",
                )

    except Exception as exc:
        logger.error(f"RQ task {task_id} failed: {exc}")
        DownloadProgressStore.update(
            task_id,
            error=str(exc)[:200],
            complete=True,
            percent=100,
            status="Error",
            phase="error",
        )


# ============================================
# Module-level singleton + backward-compatible API
# ============================================
#
# routes.py imports individual names from this module.  The wrappers below
# delegate to the singleton so that existing call-sites keep working without
# any changes.

_core = OfflinerCore()

# -- Re-exported constants / objects --
SPONSORBLOCK_CATEGORIES = OfflinerCore.SPONSORBLOCK_CATEGORIES
ytmusic = _core.ytmusic
sp = _core._spotify_default  # noqa: SLF001 — kept for backward compat


# -- Thin function wrappers (preserve original signatures) --


def obtener_info_playlist(url, config=None):
    """Backward-compatible wrapper for ``OfflinerCore.get_playlist_info``."""
    return _core.get_playlist_info(url, config)


def es_url_playlist(url):
    """Backward-compatible wrapper for ``OfflinerCore.is_playlist_url``."""
    return OfflinerCore.is_playlist_url(url)


def obtener_info_media(url, config=None):
    """Backward-compatible wrapper for ``OfflinerCore.get_media_info``."""
    return _core.get_media_info(url, config)


def detectar_fuente_url(url):
    """Backward-compatible wrapper for ``OfflinerCore.detect_url_source``."""
    return OfflinerCore.detect_url_source(url)


def extraer_video_id_youtube(url):
    """Backward-compatible wrapper for ``OfflinerCore.extract_youtube_video_id``."""
    return OfflinerCore.extract_youtube_video_id(url)


def obtener_segmentos_sponsorblock(video_id, categories=None):
    """Backward-compatible wrapper for ``OfflinerCore.get_sponsorblock_segments``."""
    return _core.get_sponsorblock_segments(video_id, categories)


def iniciar_con_progreso(
    config,
    dato,
    nombre_archivo_final="archivos.zip",
    progress_callback=None,
    base_folder=None,
    request_id=None,
):
    """Backward-compatible wrapper for ``OfflinerCore.download_with_progress``."""
    return _core.download_with_progress(
        config,
        dato,
        nombre_archivo_final,
        progress_callback,
        base_folder,
        request_id=request_id,
    )


def iniciar_descarga_selectiva(
    config,
    urls_seleccionadas,
    nombre_archivo_final="archivos.zip",
    progress_callback=None,
    base_folder=None,
    item_configs=None,
    request_id=None,
):
    """Backward-compatible wrapper for ``OfflinerCore.download_selective``."""
    return _core.download_selective(
        config,
        urls_seleccionadas,
        nombre_archivo_final,
        progress_callback,
        base_folder,
        item_configs,
        request_id=request_id,
    )


def buscar_cancion_youtube(query):
    """Backward-compatible wrapper for ``OfflinerCore.search_youtube``."""
    return _core.search_youtube(query)


def buscar_en_youtube_music(titulo_video, artista=None):
    """Backward-compatible wrapper for ``OfflinerCore.search_youtube_music``."""
    return _core.search_youtube_music(titulo_video, artista)


def obtener_cancion_Spotify(config, link_spotify):
    """Backward-compatible wrapper for ``OfflinerCore._resolve_spotify_track``."""
    return _core._resolve_spotify_track(config, link_spotify)  # noqa: SLF001


def obtener_playlist(config, plataforma, playlist_url):
    """Backward-compatible wrapper for ``OfflinerCore._resolve_playlist_urls``."""
    return _core._resolve_playlist_urls(
        config, plataforma, playlist_url
    )  # noqa: SLF001
