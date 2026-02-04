"""
Backend logic for Offliner.
Contains core functions for downloading audio and video from YouTube and Spotify.
"""

from spotipy.oauth2 import SpotifyClientCredentials
from mutagen.id3 import ID3, ID3NoHeaderError, APIC, TPE1, TALB, TIT2, TDRC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.flac import FLAC, Picture
from mutagen.wave import WAVE
from ytmusicapi import YTMusic
import concurrent.futures
import subprocess
import requests
import zipfile
import spotipy
import shutil
import yt_dlp
import time
import os
import re
import logging
import unicodedata
import glob

# Configure logging
logger = logging.getLogger(__name__)

# ============================================
# Constants and Credentials
# ============================================

# Resolve credentials from environment first, then from config.py
from config import get_config

_app_config = get_config()

SPOTIFY_CLIENT_ID = (
    os.getenv("SPOTIFY_CLIENT_ID")
    or getattr(_app_config, "SPOTIFY_CLIENT_ID", "")
    or ""
)
SPOTIFY_CLIENT_SECRET = (
    os.getenv("SPOTIFY_CLIENT_SECRET")
    or getattr(_app_config, "SPOTIFY_CLIENT_SECRET", "")
    or ""
)

SPONSORBLOCK_CATEGORIES = {
    "sponsor": "Sponsors (promociones pagadas)",
    "intro": "Intros/Animaciones de entrada",
    "outro": "Outros/Créditos finales",
    "selfpromo": "Auto-promoción del creador",
    "preview": "Previews/Avances",
    "filler": "Relleno/Contenido no musical",
    "interaction": "Recordatorios de suscripción/interacción",
    "music_offtopic": "Partes sin música en videos musicales",
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================
# Global Clients Initialization
# ============================================

ytmusic = None
try:
    ytmusic = YTMusic()
except Exception as e:
    logger.warning(f"Could not initialize YTMusic: {e}")

sp = None
try:
    if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
        sp_credentials = SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET
        )
        sp = spotipy.Spotify(
            client_credentials_manager=sp_credentials, requests_timeout=10
        )
        logger.info("Spotify client initialized successfully")
    else:
        logger.info(
            "Spotify credentials not provided; Spotify features will be disabled unless configured at runtime."
        )
except Exception as e:
    logger.warning(f"Could not initialize Spotify: {e}")


# ============================================
# Download Result Class (replaces global variables)
# ============================================


class DownloadResult:
    """Class to store download results without using global variables."""

    def __init__(self, progress_callback=None):
        self.audios_exito = 0
        self.audios_error = 0
        self.videos_exito = 0
        self.videos_error = 0
        self.canciones_descargadas = []
        self.progress_callback = progress_callback
        self.total_items = 0
        self.completed_items = 0

    def update_progress(self, status, detail=""):
        """Updates progress using the callback if available."""
        if self.progress_callback:
            if self.total_items > 0:
                base_percent = int((self.completed_items / self.total_items) * 80) + 10
            else:
                base_percent = 10
            self.progress_callback(min(base_percent, 90), status, detail)

    def reset(self):
        """Resets all counters."""
        self.audios_exito = 0
        self.audios_error = 0
        self.videos_exito = 0
        self.videos_error = 0
        self.canciones_descargadas = []


# ============================================
# yt-dlp Options Helpers
# ============================================


def obtener_opciones_base_ytdlp():
    """
    Generates base options common to all yt-dlp calls.
    Uses web client to access all formats without restrictions.
    """
    return {
        "quiet": True,
        "no_warnings": True,
        "extractor_retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "retry_sleep_functions": {"http": lambda n: min(2**n, 30)},
        "socket_timeout": 60,
        "http_chunk_size": 10485760,
        "extractor_args": {
            "youtube": {
                "player_client": ["android_music"],
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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


def obtener_opciones_sponsorblock(config):
    """Generates yt-dlp options for SponsorBlock based on user config."""
    opciones = {}
    if config.get("SponsorBlock_enabled", False):
        categorias_seleccionadas = config.get("SponsorBlock_categories", [])
        if categorias_seleccionadas:
            opciones["sponsorblock_remove"] = categorias_seleccionadas
            opciones["sponsorblock_mark"] = []
            opciones["sponsorblock_api"] = "https://sponsor.ajay.app"
            logger.info(
                f"SponsorBlock enabled. Removing: {', '.join(categorias_seleccionadas)}"
            )
    return opciones


def obtener_postprocessors_sponsorblock(config):
    """Generates postprocessors needed for SponsorBlock."""
    postprocessors = []
    if config.get("SponsorBlock_enabled", False):
        categorias_seleccionadas = config.get("SponsorBlock_categories", [])
        if categorias_seleccionadas:
            postprocessors.append(
                {
                    "key": "SponsorBlock",
                    "categories": categorias_seleccionadas,
                    "api": "https://sponsor.ajay.app",
                }
            )
            postprocessors.append(
                {
                    "key": "ModifyChapters",
                    "remove_sponsor_segments": categorias_seleccionadas,
                    "force_keyframes": False,
                }
            )
    return postprocessors


# ============================================
# SponsorBlock API
# ============================================


def obtener_segmentos_sponsorblock(video_id, categories=None):
    """
    Gets SponsorBlock segments for a video.

    Args:
        video_id: YouTube video ID
        categories: List of categories to filter (if None, uses all available)

    Returns:
        dict: {
            'has_segments': bool,
            'segments': list of segment dicts,
            'total_duration_removed': float (in seconds),
            'categories_found': list of category names found
        }
    """
    if not categories:
        categories = list(SPONSORBLOCK_CATEGORIES.keys())

    try:
        # SponsorBlock API endpoint
        api_url = f"https://sponsor.ajay.app/api/skipSegments?videoID={video_id}"

        response = requests.get(api_url, timeout=5)

        # Si no hay segmentos, la API devuelve 404
        if response.status_code == 404:
            return {
                "has_segments": False,
                "segments": [],
                "total_duration_removed": 0,
                "categories_found": [],
            }

        if response.status_code != 200:
            logger.warning(f"SponsorBlock API returned status {response.status_code}")
            return {
                "has_segments": False,
                "segments": [],
                "total_duration_removed": 0,
                "categories_found": [],
            }

        all_segments = response.json()

        # Filter by requested categories
        filtered_segments = [
            seg for seg in all_segments if seg.get("category") in categories
        ]

        if not filtered_segments:
            return {
                "has_segments": False,
                "segments": [],
                "total_duration_removed": 0,
                "categories_found": [],
            }

        # Calculate total duration to be removed
        total_removed = sum(
            seg["segment"][1] - seg["segment"][0] for seg in filtered_segments
        )

        # Get unique categories found
        categories_found = list(set(seg["category"] for seg in filtered_segments))

        return {
            "has_segments": True,
            "segments": filtered_segments,
            "total_duration_removed": total_removed,
            "categories_found": categories_found,
        }

    except requests.RequestException as e:
        logger.error(f"Error querying SponsorBlock API: {e}")
        return {
            "has_segments": False,
            "segments": [],
            "total_duration_removed": 0,
            "categories_found": [],
        }
    except Exception as e:
        logger.error(f"Unexpected error in SponsorBlock query: {e}")
        return {
            "has_segments": False,
            "segments": [],
            "total_duration_removed": 0,
            "categories_found": [],
        }


def extraer_video_id_youtube(url):
    """
    Extracts YouTube video ID from a URL.

    Args:
        url: YouTube URL

    Returns:
        str: Video ID or None if not found
    """
    if not url:
        return None

    # Pattern for various YouTube URL formats
    patterns = [
        r"(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/v\/|music\.youtube\.com\/watch\?v=)([a-zA-Z0-9_-]{11})",
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    # If URL is already just the ID
    if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
        return url

    return None


# ============================================
# Utility Functions
# ============================================


def crear_carpeta(carpeta):
    """Creates a folder if it doesn't exist."""
    if not os.path.isabs(carpeta):
        ruta_completa = os.path.join(SCRIPT_DIR, carpeta)
    else:
        ruta_completa = carpeta
    if not os.path.exists(ruta_completa):
        os.makedirs(ruta_completa)
    return ruta_completa


def sanitizar_nombre_archivo(titulo):
    """
    Sanitizes a title for use as a Windows filename.
    Only removes characters that are illegal in Windows.
    Illegal characters: < > : " / \\ | ? *
    """
    nombre = titulo.strip()
    caracteres_invalidos = r'[<>:"/\\|?*]'
    nombre = re.sub(caracteres_invalidos, "", nombre)
    nombre = re.sub(r"\.+$", "", nombre.strip())
    nombre = re.sub(r"\s+", " ", nombre).strip()
    if len(nombre) > 200:
        nombre = nombre[:200].strip()

    # Normalize to closest ASCII representation to avoid problems with ffmpeg on Windows
    try:
        nombre_ascii = (
            unicodedata.normalize("NFKD", nombre)
            .encode("ascii", "ignore")
            .decode("ascii")
        )
        nombre_ascii = re.sub(r"\s+", " ", nombre_ascii).strip()
        if nombre_ascii:
            nombre = nombre_ascii
    except Exception:
        pass

    return nombre


def limpiar_titulo(titulo):
    """Alias for sanitizar_nombre_archivo for compatibility."""
    return sanitizar_nombre_archivo(titulo)


def limpiar_nombre_archivo(nombre):
    """Cleans a filename by removing invalid Windows characters."""
    nombre_limpio = re.sub(r'[<>:"/\\|?*]', "", nombre)
    nombre_limpio = re.sub(r"\.+$", "", nombre_limpio.strip())
    return nombre_limpio


def archivo_duplicado(directorio, tipo, nombre):
    """Checks if a file already exists."""
    try:
        carpeta_tipo = tipo.capitalize()
        directorio_completo = os.path.join(SCRIPT_DIR, directorio, carpeta_tipo)
        if not os.path.exists(directorio_completo):
            return False
        archivos_en_ruta_actual = os.listdir(directorio_completo)
        for archivo in archivos_en_ruta_actual:
            if archivo == nombre:
                ruta_completa = os.path.abspath(
                    os.path.join(directorio_completo, nombre)
                )
                return ruta_completa
        return False
    except Exception as e:
        logger.error(f"Error checking for duplicate file: {e}")
        return False


# ============================================
# YouTube Music Search
# ============================================


def extraer_artista_principal(titulo):
    """Extracts the main artist from a video title."""
    if not titulo:
        return ""
    titulo = titulo.strip()
    separadores = [
        r"\s*\|\|\s*",
        r"\s*[-–—]\s*",
        r"\s+ft\.?\s+",
        r"\s+feat\.?\s+",
        r"\s+x\s+",
        r"\s*&\s*",
    ]
    for sep in separadores:
        partes = re.split(sep, titulo, maxsplit=1, flags=re.IGNORECASE)
        if len(partes) > 1:
            artista = partes[0].strip()
            if len(artista) >= 2:
                return artista.lower()
    return titulo.lower()


def extraer_palabras_clave(texto):
    """Extracts important keywords from text for comparison."""
    texto = texto.lower()
    texto = re.sub(r"[^\w\s#áéíóúñ]", " ", texto)
    palabras = texto.split()
    palabras_clave = []
    for p in palabras:
        if len(p) >= 2 or p.startswith("#") or p.isdigit():
            palabras_clave.append(p)
    return set(palabras_clave)


def calcular_similitud_mejorada(
    titulo_original,
    titulo_resultado,
    artista_principal,
    artista_resultado,
    lista_artistas,
):
    """Calculates an improved similarity score between the searched title and a result."""
    titulo_original_lower = titulo_original.lower()
    titulo_resultado_lower = titulo_resultado.lower()
    artista_resultado_lower = artista_resultado.lower()

    # Artist verification (35%)
    puntuacion_artista = 0.0
    if artista_principal:
        artista_encontrado = False
        for artista in lista_artistas:
            if artista_principal in artista or artista in artista_principal:
                artista_encontrado = True
                break
            palabras_artista_original = set(artista_principal.split())
            palabras_artista_resultado = set(artista.split())
            if (
                len(palabras_artista_original.intersection(palabras_artista_resultado))
                >= 1
            ):
                if len(palabras_artista_original) <= 2:
                    artista_encontrado = True
                    break
        if not artista_encontrado and artista_principal in titulo_resultado_lower:
            artista_encontrado = True
        puntuacion_artista = 1.0 if artista_encontrado else 0.0
    else:
        puntuacion_artista = 0.5

    # Official artist verification (25%)
    puntuacion_artista_oficial = 1.0
    es_bzrp_session = "sessions" in titulo_original_lower and (
        "bzrp" in titulo_original_lower or "bizarrap" in titulo_original_lower
    )
    if es_bzrp_session:
        es_bizarrap = "bizarrap" in artista_resultado_lower or any(
            "bizarrap" in a for a in lista_artistas
        )
        if not es_bizarrap:
            puntuacion_artista_oficial = 0.0

    # Number verification (20%)
    numeros_original = set(re.findall(r"\d+", titulo_original))
    numeros_resultado = set(re.findall(r"\d+", titulo_resultado_lower))
    if numeros_original:
        numeros_coinciden = bool(numeros_original.intersection(numeros_resultado))
        puntuacion_numeros = 1.0 if numeros_coinciden else 0.0
    else:
        puntuacion_numeros = 1.0

    # Keyword verification (10%)
    palabras_original = extraer_palabras_clave(titulo_original)
    palabras_resultado = extraer_palabras_clave(titulo_resultado)
    if palabras_original:
        coincidencias = palabras_original.intersection(palabras_resultado)
        puntuacion_palabras = len(coincidencias) / len(palabras_original)
    else:
        puntuacion_palabras = 0.5

    # Cover detection (penalty)
    indicadores_cover = [
        "cover",
        "version",
        "versión",
        "banda",
        "karaoke",
        "instrumental",
        "tribute",
        "in the style of",
        "originally performed",
        "made famous",
        "acústic",
        "acoustic",
        "remix",
        "live",
        "en vivo",
        "unplugged",
        "stripped",
    ]
    artistas_sospechosos = [
        "tv musica",
        "tv music",
        "los coleguitas",
        "abordaje",
        "karolina protsenko",
        "claudia leal",
        "carlos ro violin",
        "power music workout",
        "electric lion",
        "the pop posse",
    ]
    es_cover = any(ind in titulo_resultado_lower for ind in indicadores_cover)
    es_artista_sospechoso = any(
        a in artista_resultado_lower for a in artistas_sospechosos
    )
    es_cover_en_original = any(
        ind in titulo_original_lower for ind in indicadores_cover
    )
    penalizacion_cover = (
        0.6 if (es_cover and not es_cover_en_original) or es_artista_sospechoso else 0.0
    )

    # Calculate final score
    puntuacion = (
        puntuacion_artista * 0.35
        + puntuacion_artista_oficial * 0.25
        + puntuacion_numeros * 0.20
        + puntuacion_palabras * 0.10
        + 0.10
    )
    puntuacion = puntuacion * (1 - penalizacion_cover)
    return min(puntuacion, 1.0)


def buscar_en_youtube_music(titulo_video, artista=None):
    """
    Searches for a song on YouTube Music and returns the pure audio URL.
    Only returns a result if there's sufficient match with the original title.
    """
    global ytmusic
    if ytmusic is None:
        logger.warning("YTMusic not available")
        return None, None, None

    try:
        titulo_busqueda = titulo_video.strip()
        patrones_video = [
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
        for patron in patrones_video:
            titulo_busqueda = re.sub(patron, "", titulo_busqueda, flags=re.IGNORECASE)
        titulo_busqueda = (
            titulo_busqueda.replace("||", " ").replace("|", " ").replace("#", " ")
        )
        titulo_busqueda = re.sub(r"\s+", " ", titulo_busqueda).strip()

        logger.info(f"Searching on YouTube Music: '{titulo_busqueda}'")
        artista_principal = extraer_artista_principal(titulo_video)

        resultados = ytmusic.search(titulo_busqueda, filter="songs", limit=15)
        if not resultados:
            resultados = ytmusic.search(titulo_busqueda, limit=15)
        if not resultados:
            logger.warning(f"No results found for '{titulo_busqueda}'")
            return None, None, None

        mejor_resultado = None
        mejor_puntuacion = 0
        umbral_minimo = 0.5

        for resultado in resultados:
            if not resultado.get("videoId"):
                continue
            titulo_resultado = resultado.get("title", "")
            artistas_resultado = resultado.get("artists", [])
            artista_resultado = (
                artistas_resultado[0].get("name", "") if artistas_resultado else ""
            )
            lista_artistas = [a.get("name", "").lower() for a in artistas_resultado]

            puntuacion = calcular_similitud_mejorada(
                titulo_video,
                titulo_resultado,
                artista_principal,
                artista_resultado,
                lista_artistas,
            )

            if puntuacion > mejor_puntuacion:
                mejor_puntuacion = puntuacion
                mejor_resultado = resultado

        if mejor_resultado and mejor_puntuacion >= umbral_minimo:
            video_id = mejor_resultado.get("videoId")
            titulo_cancion = mejor_resultado.get("title", titulo_busqueda)
            artistas = mejor_resultado.get("artists", [])
            artista_encontrado = (
                artistas[0].get("name", "Unknown") if artistas else "Unknown"
            )
            url_ytmusic = f"https://music.youtube.com/watch?v={video_id}"
            logger.info(
                f"Match found ({mejor_puntuacion:.0%}): '{titulo_cancion}' by '{artista_encontrado}'"
            )
            return url_ytmusic, titulo_cancion, artista_encontrado
        else:
            logger.warning(
                f"No sufficient match for '{titulo_video}' (best: {mejor_puntuacion:.0%})"
            )
            return None, None, None

    except Exception as e:
        logger.error(f"Error searching YouTube Music: {e}")
        return None, None, None


# ============================================
# YouTube Search
# ============================================


def buscar_cancion_youtube(query):
    """Searches for a video on YouTube and returns the URL."""
    try:
        logger.info(f"Searching YouTube: {query}")
        ydl_opts = obtener_opciones_base_ytdlp()
        ydl_opts.update(
            {
                "extract_flat": True,
                "default_search": "ytsearch1",
            }
        )

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            if info and "entries" in info and info["entries"]:
                entry = info["entries"][0]
                if entry:
                    video_id = entry.get("id")
                    if video_id:
                        link_youtube = f"https://www.youtube.com/watch?v={video_id}"
                        logger.info(f"Video found: {link_youtube}")
                        return link_youtube
        logger.warning(f"No results for: {query}")
        return ""
    except Exception as e:
        logger.error(f"Error searching YouTube: {e}")
        return ""


# ============================================
# Metadata Functions
# ============================================


def obtener_nombre_artistas(track):
    """Gets artist names as a comma-separated string."""
    if track["artists"]:
        artists = [artist["name"] for artist in track["artists"]]
        return ", ".join(artists)
    return ""


def obtener_nombre_album(spotify_client, album_id):
    """Gets the full album name from Spotify API."""
    try:
        album = spotify_client.album(album_id)
        if "name" in album:
            return album["name"]
        return None
    except Exception as e:
        logger.error(f"Error getting album name: {e}")
        return None


def descargar_metadata(
    ruta_del_archivo, nombre_archivo, nombre_artista, client_id=None, client_secret=None
):
    """Downloads and adds Spotify metadata to an audio/video file."""
    logger.info("Scraping metadata from Spotify...")
    try:
        spotify_client_id = client_id if client_id else SPOTIFY_CLIENT_ID
        spotify_client_secret = (
            client_secret if client_secret else SPOTIFY_CLIENT_SECRET
        )

        global sp
        if sp and spotify_client_id == SPOTIFY_CLIENT_ID:
            spotify_client = sp
        else:
            client_credentials_manager = SpotifyClientCredentials(
                spotify_client_id, spotify_client_secret
            )
            spotify_client = spotipy.Spotify(
                client_credentials_manager=client_credentials_manager,
                requests_timeout=10,
            )

        nombre_archivo = limpiar_titulo(nombre_archivo)
        resultados = spotify_client.search(
            q=f"{nombre_archivo} {nombre_artista}",
            type="track",
            limit=1,
            market="ES",
            offset=0,
        )

        if resultados["tracks"]["items"]:
            track = resultados["tracks"]["items"][0]
            if track["album"]["images"]:
                url_artwork = track["album"]["images"][0]["url"]
                nombre_artistas = obtener_nombre_artistas(track)
                formato_archivo = os.path.splitext(ruta_del_archivo)[1].lower()
                portada_data = requests.get(url_artwork).content

                nombre_album = obtener_nombre_album(
                    spotify_client, track["album"]["id"]
                )
                caracteres_especiales = ':/\\|?*"<>'
                nombre_album_limpio = (
                    "".join(
                        c if c not in caracteres_especiales else " "
                        for c in nombre_album
                    )
                    if nombre_album
                    else None
                )
                anio_publicacion = (
                    track["album"]["release_date"][:4]
                    if "album" in track and "release_date" in track["album"]
                    else None
                )

                if formato_archivo in [".mp3", ".wav", ".m4a", ".flac"]:
                    if formato_archivo == ".mp3":
                        try:
                            audio = ID3(ruta_del_archivo)
                        except ID3NoHeaderError:
                            audio = ID3()
                        audio.delall("APIC")
                        audio.add(
                            APIC(
                                encoding=3,
                                mime="image/jpeg",
                                type=3,
                                desc="Front Cover",
                                data=portada_data,
                            )
                        )
                        audio.delall("TIT2")
                        audio.add(TIT2(encoding=3, text=nombre_archivo))
                        audio.delall("TPE1")
                        audio.add(TPE1(encoding=3, text=nombre_artistas))
                        if nombre_album_limpio:
                            audio.delall("TALB")
                            audio.add(TALB(encoding=3, text=nombre_album_limpio))
                        if anio_publicacion:
                            audio.delall("TDRC")
                            audio.delall("TYER")
                            audio.add(TDRC(encoding=3, text=anio_publicacion))
                        audio.save(ruta_del_archivo, v2_version=3)

                    elif formato_archivo == ".wav":
                        try:
                            audio_wave = WAVE(ruta_del_archivo)
                            if audio_wave.tags is None:
                                audio_wave.add_tags()
                            tags = audio_wave.tags
                            tags.delall("APIC")
                            tags.add(
                                APIC(
                                    encoding=3,
                                    mime="image/jpeg",
                                    type=3,
                                    desc="Cover",
                                    data=portada_data,
                                )
                            )
                            tags.delall("TIT2")
                            tags.add(TIT2(encoding=3, text=nombre_archivo))
                            tags.delall("TPE1")
                            tags.add(TPE1(encoding=3, text=nombre_artistas))
                            tags.delall("TALB")
                            if nombre_album_limpio:
                                tags.add(TALB(encoding=3, text=nombre_album_limpio))
                            tags.delall("TDRC")
                            tags.delall("TYER")
                            if anio_publicacion:
                                tags.add(TDRC(encoding=3, text=anio_publicacion))
                            audio_wave.save(v2_version=3)
                        except Exception as e:
                            logger.error(f"Error adding metadata to WAV: {e}")

                    elif formato_archivo == ".m4a":
                        audio = MP4(ruta_del_archivo)
                        audio["\xa9nam"] = nombre_archivo
                        audio["\xa9ART"] = nombre_artistas
                        audio["covr"] = [
                            MP4Cover(
                                data=portada_data, imageformat=MP4Cover.FORMAT_JPEG
                            )
                        ]
                        if nombre_album_limpio:
                            audio["\xa9alb"] = nombre_album_limpio
                        if anio_publicacion:
                            audio["\xa9day"] = anio_publicacion
                        audio.save()

                    elif formato_archivo == ".flac":
                        audio = FLAC(ruta_del_archivo)
                        audio["title"] = nombre_archivo
                        audio["artist"] = nombre_artistas
                        if nombre_album_limpio:
                            audio["album"] = nombre_album_limpio
                        if anio_publicacion:
                            audio["date"] = anio_publicacion
                        audio.clear_pictures()
                        image = Picture()
                        image.type = 3
                        image.mime = "image/jpeg"
                        image.desc = "Front Cover"
                        image.data = portada_data
                        audio.add_picture(image)
                        audio.save()

                elif formato_archivo in [".mp4", ".mov"]:
                    video = MP4(ruta_del_archivo)
                    video["\xa9nam"] = nombre_archivo
                    video["\xa9ART"] = nombre_artistas
                    video["covr"] = [
                        MP4Cover(data=portada_data, imageformat=MP4Cover.FORMAT_JPEG)
                    ]
                    if nombre_album_limpio:
                        video["\xa9alb"] = nombre_album_limpio
                    if anio_publicacion:
                        video["\xa9day"] = anio_publicacion
                    video.save()

        logger.info("Metadata added successfully.")
    except Exception as e:
        logger.error(f"Error downloading metadata: {e}")


# ============================================
# Video Conversion
# ============================================


def _convertir_video_ffmpeg(archivo_origen, archivo_destino, formato):
    """Converts a video to the specified format using FFmpeg.

    Optimized for speed and CPU usage while maintaining quality.
    Includes audio/video sync fixes and hardware acceleration support.
    """
    try:
        if not os.path.exists(archivo_origen):
            logger.error(f"Source file not found: {archivo_origen}")
            return False

        formato_config = {
            "mp4": {
                "vcodec": "libx264",
                "acodec": "aac",
                "extra_args": [
                    "-preset",
                    "faster",  # Faster encoding, less CPU
                    "-crf",
                    "23",  # Quality control (18-28, 23 is good balance)
                    "-movflags",
                    "+faststart",  # Web optimization
                    "-tune",
                    "film",  # Optimize for film/video content
                    "-x264-params",
                    "ref=4:bframes=2",  # Balanced encoding params
                ],
            },
            "mkv": {
                "vcodec": "libx264",
                "acodec": "aac",
                "extra_args": [
                    "-preset",
                    "faster",
                    "-crf",
                    "23",
                ],
            },
            "webm": {
                "vcodec": "libxvid",
                "acodec": "libmp3lame",
                "extra_args": [
                    "-qscale:v",
                    "3",
                    "-mbd",
                    "2",
                ],
            },
            "mov": {
                "vcodec": "libx264",
                "acodec": "aac",
                "extra_args": [
                    "-preset",
                    "faster",
                    "-crf",
                    "23",
                    "-movflags",
                    "+faststart",
                    "-tune",
                    "film",
                    "-x264-params",
                    "ref=4:bframes=2",
                ],
            },
        }

        config = formato_config.get(formato.lower())
        if not config:
            logger.error(f"Unsupported video format: {formato}")
            return False

        archivo_origen = os.path.abspath(archivo_origen)
        archivo_destino = os.path.abspath(archivo_destino)

        # Base command with optimizations for speed and sync
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-stats",
            # Hardware acceleration (fallback gracefully if not available)
            "-hwaccel",
            "auto",
            "-i",
            archivo_origen,
            # Fix audio/video sync issues
            "-fflags",
            "+genpts",  # Generate presentation timestamps
            "-async",
            "1",  # Audio sync method
            "-vsync",
            "cfr",  # Constant frame rate for better sync
            # Video codec
            "-c:v",
            config["vcodec"],
            # Audio codec
            "-c:a",
            config["acodec"],
            "-b:a",
            "192k",  # Good audio bitrate
            "-ar",
            "48000",  # Standard audio sample rate
        ]

        # Add format-specific args
        cmd.extend(config["extra_args"])

        # Add max interleaving to prevent desync
        cmd.extend(
            [
                "-max_interleave_delta",
                "0",
                "-max_muxing_queue_size",
                "1024",
                # Thread optimization
                "-threads",
                "0",  # Auto-detect optimal thread count
                "-y",
                archivo_destino,
            ]
        )

        logger.info(f"Converting video to {formato} (optimized)...")
        result = subprocess.run(
            cmd, capture_output=False, encoding="utf-8", errors="replace"
        )

        if result.returncode != 0:
            logger.error(f"FFmpeg error")
            return False

        logger.info(f"Video converted successfully to {formato}")
        return True

    except FileNotFoundError:
        logger.error("FFmpeg is not installed or not in PATH")
        return False
    except Exception as e:
        logger.error(f"Error converting video: {e}")
        return False


def _convertir_audio_ffmpeg(
    archivo_origen, archivo_destino, formato, calidad_kbps="128"
):
    """Converts a given audio file to the desired format using ffmpeg.

    Optimized for speed and quality with format-specific settings.
    Returns True on success, False otherwise.
    """
    try:
        if not os.path.exists(archivo_origen):
            logger.error(f"Source audio not found: {archivo_origen}")
            return False

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            logger.error("FFmpeg not found in PATH")
            return False

        archivo_origen = os.path.abspath(archivo_origen)
        archivo_destino = os.path.abspath(archivo_destino)

        formato = formato.lower()

        # Optimized codec configurations per format
        formato_config = {
            "mp3": {
                "codec": ["-c:a", "libmp3lame"],
                "extra": [
                    "-compression_level",
                    "2",  # Faster encoding (0-9, 2 is fast)
                    "-q:a",
                    "2",  # VBR quality (0-9, 2 is high quality)
                ],
                "use_bitrate": False,  # Use VBR instead
            },
            "m4a": {
                "codec": ["-c:a", "aac"],
                "extra": [
                    "-movflags",
                    "+faststart",
                    "-ar",
                    "48000",  # Sample rate
                ],
                "use_bitrate": True,
            },
            "aac": {
                "codec": ["-c:a", "aac"],
                "extra": ["-ar", "48000"],
                "use_bitrate": True,
            },
            "opus": {
                "codec": ["-c:a", "libopus"],
                "extra": [
                    "-compression_level",
                    "5",  # Balance speed/quality (0-10)
                    "-vbr",
                    "on",  # Variable bitrate
                ],
                "use_bitrate": True,
            },
            "flac": {
                "codec": ["-c:a", "flac"],
                "extra": [
                    "-compression_level",
                    "5",  # Fast compression (0-12, 5 is fast)
                ],
                "use_bitrate": False,  # Lossless, no bitrate
            },
            "wav": {
                "codec": ["-c:a", "pcm_s16le"],
                "extra": ["-ar", "48000"],
                "use_bitrate": False,  # PCM, no bitrate
            },
        }

        config = formato_config.get(
            formato,
            {
                "codec": ["-c:a", "copy"],
                "extra": [],
                "use_bitrate": False,
            },
        )

        # Build command with optimizations
        cmd = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            archivo_origen,
            "-vn",  # No video
            "-sn",  # No subtitles
            "-map",
            "0:a:0",  # Select first audio stream only
        ]

        # Add codec
        cmd.extend(config["codec"])

        # Add bitrate if needed
        if config["use_bitrate"]:
            cmd.extend(["-b:a", f"{calidad_kbps}k"])

        # Add format-specific extra args
        cmd.extend(config["extra"])

        # Add output file
        cmd.append(archivo_destino)

        logger.info(f"Converting audio to {formato} (optimized)...")

        result = subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="replace"
        )
        if result.returncode != 0:
            logger.error(f"FFmpeg audio conversion failed: {result.stderr}")
            return False

        logger.info(f"Audio converted successfully to {formato}")
        return True

    except FileNotFoundError:
        logger.error("FFmpeg is not installed or not in PATH")
        return False
    except Exception as e:
        logger.error(f"Error converting audio: {e}")
        return False


# ============================================
# Core Download Functions
# ============================================


def _descargar_audio_interno(config, url, result, base_folder=None):
    """Internal audio download function using result object."""
    try:
        if "spotify.com" in url and "/track/" in url:
            logger.info(f"Detected Spotify URL, converting to YouTube: {url}")
            url_youtube = obtener_cancion_Spotify(config, url)
            if not url_youtube:
                logger.error(f"Could not convert Spotify URL: {url}")
                result.audios_error += 1
                return
            url = url_youtube

        calidad_map = {
            "min": ("worstaudio[abr<=96]/worstaudio/worst", "64"),
            "avg": ("bestaudio[abr<=160]/bestaudio[abr<=192]/bestaudio/best", "128"),
            "max": ("bestaudio/best", "320"),
        }
        calidad_format, calidad_audio = calidad_map.get(
            config.get("Calidad_audio_video", "avg"),
            ("bestaudio[abr<=160]/bestaudio/best", "128"),
        )
        formato_audio = config.get("Formato_audio", "mp3")

        info_opts = obtener_opciones_base_ytdlp()
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            titulo_original = info.get("title", "Unknown Title")
            uploader_original = info.get("uploader", "Unknown Uploader")
            titulo_limpio = limpiar_titulo(titulo_original)

            url_descarga = url
            artista_final = uploader_original

            if config.get("Preferir_YouTube_Music", False):
                logger.info(
                    f"Searching for pure audio on YouTube Music: '{titulo_original}'"
                )
                url_ytmusic, titulo_ytmusic, artista_ytmusic = buscar_en_youtube_music(
                    titulo_original, uploader_original
                )
                if url_ytmusic:
                    url_descarga = url_ytmusic
                    artista_final = artista_ytmusic

            if base_folder:
                carpeta_audio = base_folder
            else:
                carpeta_audio = os.path.join(SCRIPT_DIR, "Downloads", "Audio")
            crear_carpeta(carpeta_audio)

            # Ensure filename is ASCII-safe to avoid ffmpeg/yt-dlp encoding issues
            try:
                titulo_ascii = (
                    unicodedata.normalize("NFKD", titulo_limpio)
                    .encode("ascii", "ignore")
                    .decode("ascii")
                )
            except Exception:
                titulo_ascii = titulo_limpio

            nombre_archivo = f"{titulo_ascii or titulo_limpio}"
            archivo_audio = os.path.join(carpeta_audio, nombre_archivo)
            archivo_audio = os.path.normpath(archivo_audio)

            es_archivo_duplicado = False
            if not base_folder:
                es_archivo_duplicado = archivo_duplicado(
                    "Downloads", "Audio", f"{nombre_archivo}.{formato_audio}"
                )

            if not es_archivo_duplicado:
                logger.info(f"Downloading audio: '{titulo_original}'")
                postprocessors = []
                postprocessors.extend(obtener_postprocessors_sponsorblock(config))
                postprocessors.append(
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": formato_audio,
                        "preferredquality": calidad_audio,
                    }
                )

                ydl_opts = obtener_opciones_base_ytdlp()
                # Use explicit extension placeholder to make output deterministic
                ydl_opts.update(
                    {
                        "format": calidad_format,
                        "postprocessors": postprocessors,
                        "outtmpl": archivo_audio + ".%(ext)s",
                    }
                )
                ydl_opts.update(obtener_opciones_sponsorblock(config))

                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([url_descarga])

                    # Path of final audio produced by FFmpegExtractAudio
                    archivo_audio_con_extension = f"{archivo_audio}.{formato_audio}"
                    if config.get("Scrappear_metadata", False) and os.path.exists(
                        archivo_audio_con_extension
                    ):
                        descargar_metadata(
                            archivo_audio_con_extension, titulo_limpio, artista_final
                        )

                    result.audios_exito += 1
                    result.canciones_descargadas.append(archivo_audio_con_extension)
                    logger.info(f"Audio downloaded: '{titulo_original}'")

                except Exception as e:
                    logger.error(f"Primary yt-dlp postprocessing failed: {e}")
                    # Fallback: download original media without extraction, then convert manually
                    try:
                        ydl_opts_fallback = obtener_opciones_base_ytdlp()
                        ydl_opts_fallback.update(
                            {
                                "format": calidad_format,
                                "outtmpl": archivo_audio + ".%(ext)s",
                            }
                        )
                        ydl_opts_fallback.update(obtener_opciones_sponsorblock(config))

                        with yt_dlp.YoutubeDL(ydl_opts_fallback) as ydl:
                            ydl.download([url_descarga])

                        # Find the downloaded file (original extension)
                        posibles = glob.glob(archivo_audio + ".*")
                        posibles = [p for p in posibles if not p.endswith(".part")]
                        if not posibles:
                            raise Exception(
                                "Downloaded file not found for fallback conversion"
                            )

                        archivo_descargado = posibles[0]
                        archivo_audio_con_extension = f"{archivo_audio}.{formato_audio}"

                        converso_ok = _convertir_audio_ffmpeg(
                            archivo_descargado,
                            archivo_audio_con_extension,
                            formato_audio,
                            calidad_audio,
                        )

                        # Clean up original downloaded file if conversion succeeded
                        if converso_ok:
                            try:
                                if os.path.exists(archivo_descargado) and (
                                    archivo_descargado != archivo_audio_con_extension
                                ):
                                    os.remove(archivo_descargado)
                            except Exception:
                                pass

                            if config.get("Scrappear_metadata", False):
                                descargar_metadata(
                                    archivo_audio_con_extension,
                                    titulo_limpio,
                                    artista_final,
                                )

                            result.audios_exito += 1
                            result.canciones_descargadas.append(
                                archivo_audio_con_extension
                            )
                            logger.info(
                                f"Audio downloaded via fallback: '{titulo_original}'"
                            )
                        else:
                            logger.error("Fallback conversion failed")
                            result.audios_error += 1

                    except Exception as e2:
                        logger.error(f"Fallback download/convert failed: {e2}")
                        result.audios_error += 1
            else:
                logger.info(f"Duplicate audio: '{titulo_original}'")
                result.canciones_descargadas.append(es_archivo_duplicado)
                result.audios_exito += 1

    except Exception as e:
        logger.error(f"Error downloading audio: {e}")
        result.audios_error += 1


def _descargar_video_interno(config, url, result, base_folder=None):
    """Internal video download function using result object."""
    try:
        if "spotify.com" in url and "/track/" in url:
            logger.info(f"Detected Spotify URL, converting to YouTube: {url}")
            url_youtube = obtener_cancion_Spotify(config, url)
            if not url_youtube:
                logger.error(f"Could not convert Spotify URL: {url}")
                result.videos_error += 1
                return
            url = url_youtube

        calidad_map = {
            "min": "worstvideo+worstaudio/worst",
            "avg": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "max": "bestvideo+bestaudio/best",
        }
        calidad_video = calidad_map.get(
            config.get("Calidad_audio_video", "avg"), calidad_map["avg"]
        )
        formato_video = config.get("Formato_video", "mp4")

        info_opts = obtener_opciones_base_ytdlp()
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            titulo_original = info.get("title", "Unknown Title")
            uploader = info.get("uploader", "Unknown Uploader")
            titulo_limpio = limpiar_titulo(titulo_original)

            if base_folder:
                carpeta_video = base_folder
            else:
                carpeta_video = os.path.join(SCRIPT_DIR, "Downloads", "Video")
            crear_carpeta(carpeta_video)

            archivo_video = os.path.join(carpeta_video, titulo_limpio)
            archivo_video = os.path.normpath(archivo_video)

            es_archivo_duplicado = False
            if not base_folder:
                es_archivo_duplicado = archivo_duplicado(
                    "Downloads", "Video", f"{titulo_limpio}.{formato_video}"
                )

            if not es_archivo_duplicado:
                logger.info(f"Downloading video: '{titulo_original}'")
                archivo_temp = archivo_video + "_temp"
                postprocessors = obtener_postprocessors_sponsorblock(config)

                # Clean up any temporary files
                rutas_limpieza = [
                    archivo_temp,
                    f"{archivo_temp}.part",
                    f"{archivo_temp}.ytdl",
                    f"{archivo_temp}.mkv",
                    f"{archivo_temp}.mp4",
                    f"{archivo_temp}.webm",
                ]
                for ruta in rutas_limpieza:
                    if os.path.exists(ruta):
                        try:
                            os.remove(ruta)
                        except Exception:
                            pass

                ydl_opts = obtener_opciones_base_ytdlp()
                ydl_opts.update(
                    {
                        "format": calidad_video,
                        "merge_output_format": "mkv",
                        "outtmpl": archivo_temp,
                        "quiet": True,
                        "no_warnings": True,
                    }
                )
                if postprocessors:
                    ydl_opts["postprocessors"] = postprocessors
                ydl_opts.update(obtener_opciones_sponsorblock(config))

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                archivo_final = f"{archivo_video}.{formato_video}"
                archivo_temp_mkv = f"{archivo_temp}.mkv"
                if not os.path.exists(archivo_temp_mkv):
                    if os.path.exists(archivo_temp):
                        archivo_temp_mkv = archivo_temp
                    else:
                        logger.error("Temporary file not found after download")
                        result.videos_error += 1
                        return

                conversion_exitosa = _convertir_video_ffmpeg(
                    archivo_temp_mkv, archivo_final, formato_video
                )

                if os.path.exists(archivo_temp_mkv):
                    os.remove(archivo_temp_mkv)
                if os.path.exists(archivo_temp) and archivo_temp != archivo_temp_mkv:
                    os.remove(archivo_temp)

                if not conversion_exitosa:
                    logger.error(f"Error converting video to {formato_video}")
                    result.videos_error += 1
                    return

                if config.get("Scrappear_metadata", False):
                    descargar_metadata(archivo_final, titulo_limpio, uploader)

                result.videos_exito += 1
                result.canciones_descargadas.append(archivo_final)
                logger.info(f"Video downloaded: '{titulo_original}'")
            else:
                logger.info(f"Duplicate video: '{titulo_original}'")
                result.canciones_descargadas.append(es_archivo_duplicado)
                result.videos_exito += 1

    except Exception as e:
        logger.error(f"Error downloading video: {e}")
        result.videos_error += 1


# ============================================
# Compression
# ============================================


def comprimir_y_mover_archivos(
    nombre_archivo_final, canciones_descargadas, output_folder=None
):
    """Compresses downloaded files into a ZIP archive."""
    try:
        if output_folder:
            carpeta_zip = output_folder
        else:
            carpeta_zip = os.path.join(SCRIPT_DIR, "Downloads", "Zip")
        os.makedirs(carpeta_zip, exist_ok=True)

        nombre_archivo_final = limpiar_nombre_archivo(nombre_archivo_final)
        if nombre_archivo_final.lower().endswith(".zip"):
            nombre_archivo_final = nombre_archivo_final[:-4]

        nombre_zip = os.path.join(carpeta_zip, f"{nombre_archivo_final}.zip")

        with zipfile.ZipFile(nombre_zip, "w") as zipf:
            for archivo in canciones_descargadas:
                archivo_limpio = limpiar_nombre_archivo(os.path.basename(archivo))
                zipf.write(archivo, archivo_limpio)

        for archivo in canciones_descargadas:
            os.remove(archivo)

        ruta_absoluta = os.path.abspath(nombre_zip)
        logger.info(f"Files compressed to '{os.path.basename(nombre_zip)}'")
        return ruta_absoluta
    except Exception as e:
        logger.error(f"Error compressing files: {e}")
        return None


# ============================================
# Spotify Functions
# ============================================


def obtener_cancion_Spotify(config, link_spotify):
    """Gets the YouTube link for a Spotify track."""
    global sp
    try:
        spotify_client = sp
        if not spotify_client or (config.get("Client_ID") != SPOTIFY_CLIENT_ID):
            try:
                client_credentials_manager = SpotifyClientCredentials(
                    config["Client_ID"], config["Secret_ID"]
                )
                spotify_client = spotipy.Spotify(
                    client_credentials_manager=client_credentials_manager,
                    requests_timeout=10,
                )
            except Exception as e:
                logger.error(f"Error creating Spotify client: {e}")
                if not sp:
                    return ""
                spotify_client = sp

        if "spotify.com" not in link_spotify or "/track/" not in link_spotify:
            logger.warning(f"Invalid Spotify URL: {link_spotify}")
            return ""

        try:
            track_id = link_spotify.split("/track/")[1].split("?")[0].split("/")[0]
            logger.info(f"Extracting Spotify track info: {track_id}")
            track_info = spotify_client.track(track_id)
            nombre_cancion = track_info["name"]
            nombre_artista = track_info["artists"][0]["name"]
            query = f"{nombre_cancion} {nombre_artista}"
            logger.info(f"Searching YouTube for: {query}")
            link_youtube = buscar_cancion_youtube(query)
            return link_youtube if link_youtube else ""
        except IndexError as e:
            logger.error(f"Error parsing Spotify URL: {e}")
            return ""

    except Exception as e:
        logger.error(f"Error getting Spotify track: {e}")
        return ""


def _obtener_info_playlist_spotify(url):
    """Gets information from a Spotify playlist."""
    global sp
    if not sp:
        logger.error("Spotify client not available")
        return None

    try:
        playlist_id = url.split("/playlist/")[1].split("?")[0].split("/")[0]
        logger.info(f"Getting Spotify playlist info: {playlist_id}")
        playlist = sp.playlist(playlist_id)

        if not playlist:
            return None

        playlist_info = {
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

        offset = 0
        limit = 100
        while True:
            results = sp.playlist_tracks(
                playlist_id,
                offset=offset,
                limit=limit,
                fields="items(track(id,name,artists,duration_ms,album(images))),next,total",
            )
            if not results or not results.get("items"):
                break

            for item in results["items"]:
                track = item.get("track")
                if not track:
                    continue
                track_id = track.get("id", "")
                if not track_id:
                    continue

                artists = track.get("artists", [])
                artist_name = ", ".join(
                    [a.get("name", "") for a in artists if a.get("name")]
                )
                duration_ms = track.get("duration_ms", 0)
                duration_seconds = duration_ms // 1000
                minutes = duration_seconds // 60
                seconds = duration_seconds % 60
                duration_str = f"{minutes}:{seconds:02d}"

                album_images = track.get("album", {}).get("images", [])
                thumbnail = album_images[0].get("url", "") if album_images else ""
                track_url = f"https://open.spotify.com/track/{track_id}"

                track_item = {
                    "id": track_id,
                    "titulo": track.get("name", "Sin título"),
                    "url": track_url,
                    "duracion": duration_str,
                    "duracion_segundos": duration_seconds,
                    "thumbnail": thumbnail,
                    "autor": artist_name,
                }
                playlist_info["items"].append(track_item)

            if not results.get("next"):
                break
            offset += limit

        playlist_info["total"] = len(playlist_info["items"])
        logger.info(
            f"Spotify playlist obtained: '{playlist_info['titulo']}' with {playlist_info['total']} tracks"
        )
        return playlist_info

    except Exception as e:
        logger.error(f"Error getting Spotify playlist: {e}")
        return None


# ============================================
# Playlist Functions
# ============================================


def obtener_playlist(config, plataforma, playlist_url):
    """Gets videos from a YouTube or Spotify playlist."""
    try:
        logger.info(f"Getting songs from {plataforma} playlist...")
        urls = []

        if plataforma == "YouTube":
            ydl_opts = obtener_opciones_base_ytdlp()
            ydl_opts.update({"extract_flat": True, "force_generic_extractor": True})
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(playlist_url, download=False)
                if "entries" in result:
                    urls = [entry["url"] for entry in result["entries"]]

        elif plataforma == "Spotify":
            global sp
            if sp and config.get("Client_ID") == SPOTIFY_CLIENT_ID:
                spotify_client = sp
            else:
                client_credentials_manager = SpotifyClientCredentials(
                    config["Client_ID"], config["Secret_ID"]
                )
                spotify_client = spotipy.Spotify(
                    client_credentials_manager=client_credentials_manager,
                    requests_timeout=10,
                )

            canciones_totales = []
            offset = 0
            while True:
                resultados = spotify_client.playlist_items(playlist_url, offset=offset)
                if not resultados["items"]:
                    break
                for item in resultados["items"]:
                    track = item["track"]
                    canciones_totales.append(
                        (track["name"], track["artists"][0]["name"])
                    )
                offset += len(resultados["items"])
                if offset >= resultados["total"]:
                    break

            with concurrent.futures.ThreadPoolExecutor() as executor:
                consultas_youtube = [
                    f"{nombre_cancion} {nombre_artista} Oficial audio"
                    for nombre_cancion, nombre_artista in canciones_totales
                ]
                resultados_youtube = list(
                    executor.map(buscar_cancion_youtube, consultas_youtube)
                )
                urls.extend(resultados_youtube)

        logger.info(f"Got {len(urls)} video(s) from {plataforma} playlist")
        return urls

    except Exception as e:
        logger.error(f"Error getting playlist from {plataforma}: {e}")
        return []


def obtener_info_playlist(url):
    """Gets complete playlist info from YouTube, YouTube Music, or Spotify without downloading."""
    global ytmusic, sp

    try:
        if "spotify.com" in url and "/playlist/" in url:
            return _obtener_info_playlist_spotify(url)

        es_youtube_music = "music.youtube.com" in url
        playlist_id = None
        if "list=" in url:
            import urllib.parse

            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            playlist_id = params.get("list", [None])[0]

        if es_youtube_music and ytmusic and playlist_id:
            try:
                logger.info(
                    f"Getting YouTube Music playlist using ytmusicapi: {playlist_id}"
                )
                yt_playlist = ytmusic.get_playlist(playlist_id, limit=None)

                if yt_playlist and "tracks" in yt_playlist:
                    playlist_info = {
                        "titulo": yt_playlist.get("title", "Playlist sin título"),
                        "descripcion": yt_playlist.get("description", ""),
                        "autor": yt_playlist.get("author", {}).get(
                            "name", "Desconocido"
                        ),
                        "total": len(yt_playlist.get("tracks", [])),
                        "thumbnail": (
                            yt_playlist.get("thumbnails", [{}])[-1].get("url", "")
                            if yt_playlist.get("thumbnails")
                            else ""
                        ),
                        "items": [],
                    }

                    for track in yt_playlist.get("tracks", []):
                        if not track:
                            continue
                        video_id = track.get("videoId", "")
                        if not video_id:
                            continue

                        duration_text = track.get("duration", "")
                        duration_seconds = 0
                        if duration_text:
                            parts = duration_text.split(":")
                            try:
                                if len(parts) == 2:
                                    duration_seconds = int(parts[0]) * 60 + int(
                                        parts[1]
                                    )
                                elif len(parts) == 3:
                                    duration_seconds = (
                                        int(parts[0]) * 3600
                                        + int(parts[1]) * 60
                                        + int(parts[2])
                                    )
                            except ValueError:
                                pass

                        artists = track.get("artists", [])
                        artist_name = (
                            ", ".join(
                                [a.get("name", "") for a in artists if a.get("name")]
                            )
                            if artists
                            else ""
                        )
                        thumbnails = track.get("thumbnails", [])
                        thumbnail = thumbnails[-1].get("url", "") if thumbnails else ""
                        if not thumbnail and video_id:
                            thumbnail = (
                                f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
                            )

                        item = {
                            "id": video_id,
                            "video_id": video_id,
                            "titulo": track.get("title", "Sin título"),
                            "url": f"https://www.youtube.com/watch?v={video_id}",
                            "duracion": duration_text or "--:--",
                            "duracion_segundos": duration_seconds,
                            "thumbnail": thumbnail,
                            "autor": artist_name,
                        }
                        playlist_info["items"].append(item)

                    playlist_info["total"] = len(playlist_info["items"])
                    logger.info(
                        f"Playlist obtained via ytmusicapi: '{playlist_info['titulo']}' with {playlist_info['total']} items"
                    )
                    return playlist_info

            except Exception as e:
                logger.warning(f"Error with ytmusicapi, using yt-dlp: {e}")

        url_normalizada = url
        if es_youtube_music:
            url_normalizada = url.replace("music.youtube.com", "www.youtube.com")

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "ignoreerrors": True,
            "playlistend": None,
            "extractor_retries": 3,
            "socket_timeout": 30,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(url_normalizada, download=False)
            if not result:
                return None
            if result.get("_type") != "playlist" and "entries" not in result:
                return None

            playlist_info = {
                "titulo": result.get("title", "Playlist sin título"),
                "descripcion": result.get("description", ""),
                "autor": result.get("uploader", result.get("channel", "Desconocido")),
                "total": result.get("playlist_count", 0),
                "thumbnail": result.get("thumbnail", ""),
                "items": [],
            }

            entries = result.get("entries", [])
            entries_list = (
                list(entries)
                if entries and not isinstance(entries, list)
                else (entries or [])
            )

            for entry in entries_list:
                if entry is None:
                    continue
                duration_seconds = entry.get("duration", 0) or 0
                if duration_seconds:
                    minutes = int(duration_seconds // 60)
                    seconds = int(duration_seconds % 60)
                    duracion_str = f"{minutes}:{seconds:02d}"
                else:
                    duracion_str = "--:--"

                video_id = entry.get("id", entry.get("url", ""))
                if video_id and not video_id.startswith("http"):
                    video_url = f"https://www.youtube.com/watch?v={video_id}"
                else:
                    video_url = entry.get("url", "")

                thumbnail = entry.get("thumbnail", "")
                if not thumbnail and video_id:
                    thumbnail = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"

                item = {
                    "id": video_id,
                    "video_id": video_id,
                    "titulo": entry.get("title", "Sin título"),
                    "url": video_url,
                    "duracion": duracion_str,
                    "duracion_segundos": duration_seconds,
                    "thumbnail": thumbnail,
                    "autor": entry.get("uploader", entry.get("channel", "")),
                }
                playlist_info["items"].append(item)

            playlist_info["total"] = len(playlist_info["items"])
            logger.info(
                f"Playlist obtained: '{playlist_info['titulo']}' with {playlist_info['total']} items"
            )
            return playlist_info

    except Exception as e:
        logger.error(f"Error getting playlist info: {e}")
        return None


# ============================================
# URL Detection Functions
# ============================================


def detectar_fuente_url(url):
    """Detects the source of a URL (YouTube, Spotify, YouTube Music)."""
    if not url:
        return None
    url_lower = url.lower()
    if "spotify.com" in url_lower:
        return "spotify"
    elif "music.youtube.com" in url_lower:
        return "youtube_music"
    elif "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    return None


def es_url_playlist(url):
    """Checks if a URL is a YouTube, YouTube Music, or Spotify playlist."""
    if not url:
        return False
    url_lower = url.lower()

    playlist_patterns = [
        "youtube.com/playlist?list=",
        "youtube.com/watch?v=.*&list=",
        "youtube.com/watch?.*list=",
        "music.youtube.com/playlist?list=",
        "music.youtube.com/watch?v=.*&list=",
        "youtu.be/.*[?&]list=",
    ]

    for pattern in playlist_patterns:
        if pattern.replace(".*", "") in url_lower or (
            ".*" in pattern and all(part in url_lower for part in pattern.split(".*"))
        ):
            return True

    if "spotify.com" in url_lower and "/playlist/" in url_lower:
        return True

    return False


def obtener_info_media(url):
    """Gets basic info from an individual video/track (title, thumbnail, author, duration)."""
    if not url:
        return None

    try:
        url_lower = url.lower()
        if "spotify.com" in url_lower:
            return _obtener_info_spotify(url)
        elif "music.youtube.com" in url_lower:
            return _obtener_info_youtube(url, fuente="youtube_music")
        elif "youtube.com" in url_lower or "youtu.be" in url_lower:
            return _obtener_info_youtube(url, fuente="youtube")
        else:
            return _obtener_info_youtube(url, fuente="youtube")
    except Exception as e:
        logger.error(f"Error getting media info: {e}")
        return None


def _obtener_info_youtube(url, fuente="youtube"):
    """Gets info from YouTube/YouTube Music using yt-dlp."""
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None

            duracion_segundos = info.get("duration", 0) or 0
            if duracion_segundos:
                minutos = duracion_segundos // 60
                segundos = duracion_segundos % 60
                duracion = f"{minutos}:{segundos:02d}"
            else:
                duracion = "0:00"

            thumbnails = info.get("thumbnails", [])
            thumbnail = ""
            if thumbnails:
                for t in reversed(thumbnails):
                    if t.get("url"):
                        thumbnail = t["url"]
                        break
            if not thumbnail:
                thumbnail = info.get("thumbnail", "")

            return {
                "titulo": info.get("title", "Sin título"),
                "thumbnail": thumbnail,
                "autor": info.get("uploader", info.get("channel", "Desconocido")),
                "duracion": duracion,
                "duracion_segundos": duracion_segundos,
                "fuente": fuente,
            }
    except Exception as e:
        logger.error(f"Error getting YouTube info: {e}")
        return None


def _obtener_info_spotify(url):
    """Gets info from Spotify using spotipy."""
    global sp
    if not sp:
        logger.warning("Spotify not configured")
        return None

    try:
        if "/track/" in url:
            track_id = url.split("/track/")[1].split("?")[0].split("/")[0]
            track = sp.track(track_id)
            if not track:
                return None

            artistas = [a["name"] for a in track.get("artists", [])]
            autor = ", ".join(artistas) if artistas else "Desconocido"
            images = track.get("album", {}).get("images", [])
            thumbnail = images[0]["url"] if images else ""

            duracion_ms = track.get("duration_ms", 0)
            duracion_segundos = duracion_ms // 1000
            minutos = duracion_segundos // 60
            segundos = duracion_segundos % 60
            duracion = f"{minutos}:{segundos:02d}"

            return {
                "titulo": track.get("name", "Sin título"),
                "thumbnail": thumbnail,
                "autor": autor,
                "duracion": duracion,
                "duracion_segundos": duracion_segundos,
                "fuente": "spotify",
            }
        else:
            return None
    except Exception as e:
        logger.error(f"Error getting Spotify info: {e}")
        return None


# ============================================
# Main Download Entry Points
# ============================================


def iniciar_con_progreso(
    config,
    dato,
    nombre_archivo_final="archivos.zip",
    progress_callback=None,
    base_folder=None,
):
    """
    Main function to start the download process with progress support.
    Auto-detects if it's a playlist, individual URL, or search query.
    """
    result = DownloadResult(progress_callback)

    if not base_folder:
        crear_carpeta(os.path.join(SCRIPT_DIR, "Downloads", "Audio"))
        crear_carpeta(os.path.join(SCRIPT_DIR, "Downloads", "Video"))
    else:
        crear_carpeta(base_folder)

    urls = []

    try:
        if progress_callback:
            progress_callback(5, "Preparing...", "Analyzing request")

        fuente = config.get("Fuente_descarga", "YouTube")

        if fuente == "YouTube":
            dato_lower = dato.lower() if dato else ""
            es_url_youtube = any(
                p in dato_lower
                for p in ["youtube.com", "youtu.be", "music.youtube.com"]
            )

            if es_url_youtube:
                if es_url_playlist(dato):
                    if progress_callback:
                        progress_callback(
                            10, "Getting playlist...", "Connecting to YouTube"
                        )
                    urls.extend(obtener_playlist(config, "YouTube", dato))
                else:
                    if progress_callback:
                        progress_callback(
                            10, "Processing link...", "YouTube URL detected"
                        )
                    urls.append(dato)
            else:
                if dato:
                    if progress_callback:
                        progress_callback(
                            10, "Searching YouTube...", f"Searching: {dato[:50]}"
                        )
                    if config.get("Preferir_YouTube_Music"):
                        url_ytmusic, titulo, artista = buscar_en_youtube_music(
                            dato, dato
                        )
                        if url_ytmusic:
                            urls.append(url_ytmusic)
                        else:
                            link_youtube = buscar_cancion_youtube(dato)
                            if link_youtube:
                                urls.append(link_youtube)
                    else:
                        link_youtube = buscar_cancion_youtube(dato)
                        if link_youtube:
                            urls.append(link_youtube)

        elif fuente == "Spotify":
            if dato:
                dato_lower = dato.lower()
                es_url_spotify = (
                    "spotify.com" in dato_lower or "open.spotify" in dato_lower
                )

                if es_url_spotify:
                    if "playlist" in dato_lower:
                        if progress_callback:
                            progress_callback(
                                10, "Getting playlist...", "Connecting to Spotify"
                            )
                        urls.extend(obtener_playlist(config, "Spotify", dato))
                    elif "album" in dato_lower:
                        if progress_callback:
                            progress_callback(
                                10, "Getting album...", "Connecting to Spotify"
                            )
                        urls.extend(obtener_playlist(config, "Spotify", dato))
                    else:
                        if progress_callback:
                            progress_callback(
                                10, "Getting song...", "Searching on Spotify"
                            )
                        url_spotify = obtener_cancion_Spotify(config, dato)
                        if url_spotify:
                            urls.append(url_spotify)
                else:
                    if progress_callback:
                        progress_callback(
                            10, "Searching Spotify...", f"Searching: {dato[:50]}"
                        )
                    link_youtube = buscar_cancion_youtube(dato)
                    if link_youtube:
                        urls.append(link_youtube)

        if not urls:
            if progress_callback:
                progress_callback(100, "No results", "No URLs to download found")
            return None

        total_downloads = 0
        if config.get("Descargar_video"):
            total_downloads += len(urls)
        if config.get("Descargar_audio"):
            total_downloads += len(urls)
        result.total_items = max(total_downloads, 1)

        if progress_callback:
            progress_callback(15, "Starting downloads...", f"{len(urls)} item(s) found")

        inicio = time.time()

        for i, url in enumerate(urls):
            if not url:
                continue
            if config.get("Descargar_video"):
                if progress_callback:
                    percent = 15 + int(
                        (result.completed_items / result.total_items) * 70
                    )
                    progress_callback(
                        percent, "Downloading video...", f"Item {i+1} of {len(urls)}"
                    )
                _descargar_video_interno(config, url, result, base_folder)
                result.completed_items += 1

            if config.get("Descargar_audio"):
                if progress_callback:
                    percent = 15 + int(
                        (result.completed_items / result.total_items) * 70
                    )
                    progress_callback(
                        percent, "Downloading audio...", f"Item {i+1} of {len(urls)}"
                    )
                _descargar_audio_interno(config, url, result, base_folder)
                result.completed_items += 1

        fin = time.time()
        logger.info(f"Execution time: {fin - inicio:.2f} seconds")

        if progress_callback:
            progress_callback(90, "Finishing...", "Processing downloaded files")

        if len(result.canciones_descargadas) > 1:
            if progress_callback:
                progress_callback(92, "Compressing...", "Creating ZIP file")
            output_folder = (
                base_folder
                if base_folder
                else os.path.join(SCRIPT_DIR, "Downloads", "Zip")
            )
            ruta_descarga = comprimir_y_mover_archivos(
                nombre_archivo_final, result.canciones_descargadas, output_folder
            )
        elif len(result.canciones_descargadas) == 1:
            ruta_descarga = os.path.abspath(result.canciones_descargadas[0])
        else:
            ruta_descarga = None

        if progress_callback:
            progress_callback(98, "Done!", "Preparing download")

        if result.audios_exito >= 1 and result.videos_exito >= 1:
            logger.info(
                f"Downloaded {result.audios_exito} audios and {result.videos_exito} videos"
            )
        elif result.audios_exito >= 1:
            logger.info(f"Downloaded {result.audios_exito} audios")
        elif result.videos_exito >= 1:
            logger.info(f"Downloaded {result.videos_exito} videos")

        logger.info(f"File path: {ruta_descarga}")
        return ruta_descarga

    except Exception as e:
        logger.error(f"Error in download process: {e}")
        if progress_callback:
            progress_callback(100, "Error", str(e)[:100])
        return None


def iniciar_descarga_selectiva(
    config,
    urls_seleccionadas,
    nombre_archivo_final="archivos.zip",
    progress_callback=None,
    base_folder=None,
    item_configs=None,
):
    """
    Starts download of selected URLs from a playlist.

    Args:
        config: Global configuration
        urls_seleccionadas: List of URLs to download
        nombre_archivo_final: Name of the final ZIP file
        progress_callback: Callback function for progress updates
        base_folder: Base folder for downloads
        item_configs: Dictionary mapping URLs to individual configurations
                     Format: {url: {'format': 'audio'|'video'}}
    """
    result = DownloadResult(progress_callback)

    if item_configs is None:
        item_configs = {}

    if not base_folder:
        crear_carpeta(os.path.join(SCRIPT_DIR, "Downloads", "Audio"))
        crear_carpeta(os.path.join(SCRIPT_DIR, "Downloads", "Video"))
    else:
        crear_carpeta(base_folder)

    try:
        if progress_callback:
            progress_callback(5, "Preparing...", "Analyzing request")

        if not urls_seleccionadas:
            if progress_callback:
                progress_callback(100, "No selection", "No URLs selected")
            return None

        # Count total downloads considering individual configs
        total_downloads = 0
        for url in urls_seleccionadas:
            item_config = item_configs.get(url, {})
            item_format = item_config.get("format", None)

            if item_format == "video":
                total_downloads += 1
            elif item_format == "audio":
                total_downloads += 1
            else:
                # Use global config
                if config.get("Descargar_video"):
                    total_downloads += 1
                if config.get("Descargar_audio"):
                    total_downloads += 1

        result.total_items = max(total_downloads, 1)

        if progress_callback:
            progress_callback(
                15,
                "Starting downloads...",
                f"{len(urls_seleccionadas)} item(s) selected",
            )

        inicio = time.time()

        for i, url in enumerate(urls_seleccionadas):
            if not url:
                continue

            # Get individual item config
            item_config = item_configs.get(url, {})
            item_format = item_config.get("format", None)

            # Determine what to download for this item
            download_video = (
                item_format == "video" if item_format else config.get("Descargar_video")
            )
            download_audio = (
                item_format == "audio" if item_format else config.get("Descargar_audio")
            )

            if download_video:
                if progress_callback:
                    percent = 15 + int(
                        (result.completed_items / result.total_items) * 70
                    )
                    progress_callback(
                        percent,
                        "Downloading video...",
                        f"Item {i+1} of {len(urls_seleccionadas)}",
                    )
                _descargar_video_interno(config, url, result, base_folder)
                result.completed_items += 1

            if download_audio:
                if progress_callback:
                    percent = 15 + int(
                        (result.completed_items / result.total_items) * 70
                    )
                    progress_callback(
                        percent,
                        "Downloading audio...",
                        f"Item {i+1} of {len(urls_seleccionadas)}",
                    )
                _descargar_audio_interno(config, url, result, base_folder)
                result.completed_items += 1

        fin = time.time()
        logger.info(f"Execution time: {fin - inicio:.2f} seconds")

        if progress_callback:
            progress_callback(90, "Finishing...", "Processing downloaded files")

        if len(result.canciones_descargadas) > 1:
            if progress_callback:
                progress_callback(92, "Compressing...", "Creating ZIP file")
            ruta_descarga = comprimir_y_mover_archivos(
                nombre_archivo_final, result.canciones_descargadas, base_folder
            )
        elif len(result.canciones_descargadas) == 1:
            ruta_descarga = os.path.abspath(result.canciones_descargadas[0])
        else:
            ruta_descarga = None

        if progress_callback:
            progress_callback(98, "Done!", "Preparing download")

        if result.audios_exito >= 1 and result.videos_exito >= 1:
            logger.info(
                f"Downloaded {result.audios_exito} audios and {result.videos_exito} videos"
            )
        elif result.audios_exito >= 1:
            logger.info(f"Downloaded {result.audios_exito} audios")
        elif result.videos_exito >= 1:
            logger.info(f"Downloaded {result.videos_exito} videos")

        logger.info(f"File path(s): {ruta_descarga}")
        return ruta_descarga

    except Exception as e:
        logger.error(f"Error in selective download: {e}")
        if progress_callback:
            progress_callback(100, "Error", str(e)[:100])
        return None
