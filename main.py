"""
Módulo principal de descarga de música.
Contiene las funciones para descargar audio y video de YouTube y Spotify.
"""

from spotipy.oauth2 import SpotifyClientCredentials
from mutagen.id3 import ID3, ID3NoHeaderError, APIC, TPE1, TALB, TIT2, TDRC
from mutagen.mp3 import MP3
from youtubesearchpython import VideosSearch
from mutagen.mp4 import MP4, MP4Cover
from mutagen.flac import FLAC, Picture
from mutagen.wave import WAVE
from ytmusicapi import YTMusic
import concurrent.futures
import subprocess
import requests
import zipfile
import spotipy
import mutagen
import shutil
import yt_dlp
import json
import time
import os
import re
import logging

# Configurar logging
logger = logging.getLogger(__name__)

# ------------------------------ #

# Credenciales de Spotify por defecto (para scraping de metadata)
SPOTIFY_CLIENT_ID = "f8068cf75621448184edc11474e60436"
SPOTIFY_CLIENT_SECRET = "45415e7db4bc4068b6bcc926ff300a6f"

# Categorías de SponsorBlock disponibles
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

# Instancia global de YTMusic (sin autenticación para búsquedas públicas)
try:
    ytmusic = YTMusic()
except Exception as e:
    logger.warning(f"No se pudo inicializar YTMusic: {e}")
    ytmusic = None

# Instancia global de Spotify (para obtener metadata)
sp = None
try:
    from spotipy.oauth2 import SpotifyClientCredentials

    sp_credentials = SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET
    )
    sp = spotipy.Spotify(client_credentials_manager=sp_credentials, requests_timeout=10)
    logger.info("Cliente de Spotify inicializado correctamente")
except Exception as e:
    logger.warning(f"No se pudo inicializar Spotify: {e}")
    sp = None

# ------------------------------ #

# Obtener el directorio donde se encuentra este script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ------------------------------ #


def obtener_opciones_base_ytdlp():
    """
    Genera las opciones base comunes para todas las llamadas a yt-dlp.
    Usa el cliente web para acceder a todos los formatos sin restricciones.

    Returns:
        dict: Diccionario con opciones base para yt-dlp
    """
    opciones = {
        "quiet": True,
        "no_warnings": True,
        "extractor_retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "retry_sleep_functions": {
            "http": lambda n: min(2**n, 30)
        },  # Exponential backoff max 30s
        "socket_timeout": 60,
        # Descargar en chunks de 10MB ayuda a prevenir errores 403 en conexiones largas
        # y permite reanudar mejor si se cae la conexión
        "http_chunk_size": 10485760,
        # Usar configuración default más robusta con cookies, sin forzar cliente
        "extractor_args": {
            "youtube": {
                "player_client": ["android_music"],
            }
        },
        # Headers estándar del navegador
        # Headers estándar del navegador
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
        # Ignorar errores de HTTPS en algunos casos
        "nocheckcertificate": True,
        # Usar cookies desde archivo si existe 'cookies.txt' en el directorio
        # Esto es más estable que extraerlas del navegador y evita el error de "database locked"
        # "cookiefile": "cookies.txt",  # Deshabilitado para evitar errores 403
        # Verificar que el formato seleccionado es realmente descargable
        "check_formats": "selected",
        # Forzar IPv4 para prevenir errores 403 (común en algunas redes/ISP)
        "force_ipv4": True,
        # IMPORTANTE: No reanudar descargas parciales.
        # Las URLs de YouTube expiran y reanudar causa error 403.
        # Es mejor reiniciar la descarga con una URL fresca.
        "continuedl": False,
        "overwrites": True,
        # No usar caché para evitar datos obsoletos
        "cachedir": False,
        # Asegurar que los nombres de archivo usen encoding correcto
        "encoding": "utf-8",
    }

    return opciones


def obtener_opciones_sponsorblock(config):
    """
    Genera las opciones de yt-dlp para SponsorBlock basándose en la configuración del usuario.

    Args:
        config: Diccionario de configuración del usuario

    Returns:
        dict: Diccionario con las opciones de SponsorBlock para yt-dlp
    """
    opciones = {}

    if config.get("SponsorBlock_enabled", False):
        # Obtener las categorías seleccionadas por el usuario
        categorias_seleccionadas = config.get("SponsorBlock_categories", [])

        if categorias_seleccionadas:
            # Configurar SponsorBlock para eliminar las categorías seleccionadas
            # Usamos sponsorblock_remove que indica las categorías a cortar del video/audio
            opciones["sponsorblock_remove"] = categorias_seleccionadas
            # No marcar segmentos, solo eliminar
            opciones["sponsorblock_mark"] = []
            # API de SponsorBlock
            opciones["sponsorblock_api"] = "https://sponsor.ajay.app"

            logger.info(
                f"SponsorBlock activado. Eliminando: {', '.join(categorias_seleccionadas)}"
            )

    return opciones


# ------------------------------ #


def extraer_artista_principal(titulo):
    """
    Extrae el artista principal de un título de video.
    Busca el nombre antes de separadores comunes como ||, -, ft., feat., etc.

    Args:
        titulo: Título del video (ej: "J BALVIN || BZRP Music Sessions #62")

    Returns:
        str: Nombre del artista principal en minúsculas, o cadena vacía si no se encuentra
    """
    if not titulo:
        return ""

    titulo = titulo.strip()

    # Patrones de separación comunes (en orden de prioridad)
    # || es muy común en BZRP sessions
    separadores = [
        r"\s*\|\|\s*",  # J BALVIN || BZRP
        r"\s*[-–—]\s*",  # Artista - Canción
        r"\s+ft\.?\s+",  # ft. o ft
        r"\s+feat\.?\s+",  # feat. o feat
        r"\s+x\s+",  # Artista x Artista
        r"\s*&\s*",  # Artista & Artista
    ]

    # Intentar con cada separador
    for sep in separadores:
        partes = re.split(sep, titulo, maxsplit=1, flags=re.IGNORECASE)
        if len(partes) > 1:
            artista = partes[0].strip()
            # Verificar que no sea muy corto (probablemente no es un artista)
            if len(artista) >= 2:
                return artista.lower()

    # Si no hay separador, devolver el título completo como posible artista
    return titulo.lower()


def calcular_similitud_mejorada(
    titulo_original,
    titulo_resultado,
    artista_principal,
    artista_resultado,
    lista_artistas,
):
    """
    Calcula una puntuación de similitud mejorada entre el título buscado y un resultado.
    Prioriza la verificación del artista y detecta covers/versiones.

    Args:
        titulo_original: Título completo original (ej: "J BALVIN || BZRP Music Sessions #62")
        titulo_resultado: Título del resultado de YouTube Music
        artista_principal: Artista extraído del título original
        artista_resultado: Artista principal del resultado
        lista_artistas: Lista de todos los artistas del resultado

    Returns:
        float: Puntuación entre 0 y 1
    """
    titulo_original_lower = titulo_original.lower()
    titulo_resultado_lower = titulo_resultado.lower()
    artista_resultado_lower = artista_resultado.lower()

    # === 1. VERIFICACIÓN DE ARTISTA PRINCIPAL (35% del peso) ===
    puntuacion_artista = 0.0

    if artista_principal:
        # Verificar si el artista principal está en la lista de artistas del resultado
        artista_encontrado = False
        for artista in lista_artistas:
            # Coincidencia exacta o parcial del artista
            if artista_principal in artista or artista in artista_principal:
                artista_encontrado = True
                break
            # También verificar palabras individuales (para "J Balvin" vs "J. Balvin")
            palabras_artista_original = set(artista_principal.split())
            palabras_artista_resultado = set(artista.split())
            if (
                len(palabras_artista_original.intersection(palabras_artista_resultado))
                >= 1
            ):
                if len(palabras_artista_original) <= 2:  # Para nombres cortos
                    artista_encontrado = True
                    break

        # También verificar si el artista está mencionado en el título del resultado
        if not artista_encontrado and artista_principal in titulo_resultado_lower:
            artista_encontrado = True

        puntuacion_artista = 1.0 if artista_encontrado else 0.0
    else:
        puntuacion_artista = 0.5  # Valor neutral si no hay artista principal

    # === 2. VERIFICACIÓN DE ARTISTA OFICIAL (BZRP/Bizarrap) (25% del peso) ===
    # Para sesiones de BZRP, el resultado DEBE tener a Bizarrap como artista
    puntuacion_artista_oficial = 1.0
    es_bzrp_session = "sessions" in titulo_original_lower and (
        "bzrp" in titulo_original_lower or "bizarrap" in titulo_original_lower
    )

    if es_bzrp_session:
        # El artista del resultado DEBE ser Bizarrap o contenerlo
        es_bizarrap = "bizarrap" in artista_resultado_lower or any(
            "bizarrap" in a for a in lista_artistas
        )
        if not es_bizarrap:
            puntuacion_artista_oficial = 0.0  # Penalización total si no es Bizarrap

    # === 3. VERIFICACIÓN DE NÚMEROS (20% del peso) ===
    # Crítico para sesiones numeradas como BZRP #52, #62, etc.
    # Extraer números, incluyendo los que están en formatos como "62/66"
    numeros_original = set(re.findall(r"\d+", titulo_original))
    # Para el resultado, también considerar números separados por /
    numeros_resultado = set(re.findall(r"\d+", titulo_resultado_lower))

    if numeros_original:
        # Los números DEBEN coincidir exactamente para sesiones numeradas
        numeros_coinciden = bool(numeros_original.intersection(numeros_resultado))
        puntuacion_numeros = 1.0 if numeros_coinciden else 0.0
    else:
        puntuacion_numeros = 1.0  # No hay números que verificar

    # === 4. VERIFICACIÓN DE PALABRAS CLAVE (10% del peso) ===
    palabras_original = extraer_palabras_clave(titulo_original)
    palabras_resultado = extraer_palabras_clave(titulo_resultado)

    if palabras_original:
        coincidencias = palabras_original.intersection(palabras_resultado)
        puntuacion_palabras = len(coincidencias) / len(palabras_original)
    else:
        puntuacion_palabras = 0.5

    # === 5. DETECCIÓN DE COVERS/VERSIONES (penalización) ===
    indicadores_cover = [
        "cover",
        "version",
        "versión",
        "banda",
        "karaoke",
        "instrumental",
        "tribute",
        "tribute to",
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

    # Artistas conocidos que suelen ser covers o canales de TV
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

    # Si el original NO menciona estos términos pero el resultado sí, es sospechoso
    es_cover_en_original = any(
        ind in titulo_original_lower for ind in indicadores_cover
    )

    penalizacion_cover = 0.0
    if (es_cover and not es_cover_en_original) or es_artista_sospechoso:
        penalizacion_cover = 0.6

    # === CALCULAR PUNTUACIÓN FINAL ===
    puntuacion = (
        puntuacion_artista * 0.35
        + puntuacion_artista_oficial * 0.25
        + puntuacion_numeros * 0.20
        + puntuacion_palabras * 0.10
        + 0.10  # Base
    )

    # Aplicar penalización por covers
    puntuacion = puntuacion * (1 - penalizacion_cover)

    return min(puntuacion, 1.0)


def buscar_en_youtube_music(titulo_video, artista=None):
    """
    Busca una canción en YouTube Music y devuelve la URL del audio puro.
    SOLO devuelve un resultado si hay suficiente coincidencia con el título original.

    Args:
        titulo_video: Título del video de YouTube (ej: "J BALVIN || BZRP Music Sessions #62")
        artista: Nombre del artista (opcional)

    Returns:
        tuple: (url_youtube_music, titulo_cancion, artista_encontrado) o (None, None, None) si no se encuentra
    """
    global ytmusic

    if ytmusic is None:
        logger.warning("YTMusic no está disponible")
        return None, None, None

    try:
        # Preparar el título para búsqueda - limpieza para mejorar resultados
        titulo_busqueda = titulo_video.strip()

        # Eliminar etiquetas de video obvias
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

        # Reemplazar || con espacio (común en títulos de BZRP)
        titulo_busqueda = titulo_busqueda.replace("||", " ").replace("|", " ")

        # Reemplazar # con espacio (interfiere con búsquedas en YouTube Music)
        # Pero preservamos el número que viene después
        titulo_busqueda = titulo_busqueda.replace("#", " ")

        # Limpiar espacios múltiples
        titulo_busqueda = re.sub(r"\s+", " ", titulo_busqueda).strip()

        logger.info(f"Buscando en YouTube Music: '{titulo_busqueda}'")

        # Extraer el artista principal del título (antes de || o -)
        artista_principal = extraer_artista_principal(titulo_video)
        logger.debug(f"Artista principal detectado: '{artista_principal}'")

        # Buscar en YouTube Music
        resultados = ytmusic.search(titulo_busqueda, filter="songs", limit=15)

        if not resultados:
            resultados = ytmusic.search(titulo_busqueda, limit=15)

        if not resultados:
            logger.warning(f"No se encontraron resultados para '{titulo_busqueda}'")
            return None, None, None

        # Buscar el mejor resultado que coincida
        mejor_resultado = None
        mejor_puntuacion = 0
        umbral_minimo = 0.5  # Requiere al menos 50% de coincidencia

        for resultado in resultados:
            if not resultado.get("videoId"):
                continue

            titulo_resultado = resultado.get("title", "")
            artistas_resultado = resultado.get("artists", [])
            artista_resultado = (
                artistas_resultado[0].get("name", "") if artistas_resultado else ""
            )

            # Lista completa de artistas para verificación
            lista_artistas = [a.get("name", "").lower() for a in artistas_resultado]

            # Calcular puntuación de similitud
            puntuacion = calcular_similitud_mejorada(
                titulo_video,
                titulo_resultado,
                artista_principal,
                artista_resultado,
                lista_artistas,
            )

            logger.debug(
                f"Resultado: '{titulo_resultado}' de '{artista_resultado}' - Puntuación: {puntuacion:.2f}"
            )

            if puntuacion > mejor_puntuacion:
                mejor_puntuacion = puntuacion
                mejor_resultado = resultado

        # Solo aceptar si la puntuación supera el umbral
        if mejor_resultado and mejor_puntuacion >= umbral_minimo:
            video_id = mejor_resultado.get("videoId")
            titulo_cancion = mejor_resultado.get("title", titulo_busqueda)
            artistas = mejor_resultado.get("artists", [])
            artista_encontrado = (
                artistas[0].get("name", "Unknown") if artistas else "Unknown"
            )

            url_ytmusic = f"https://music.youtube.com/watch?v={video_id}"

            logger.info(
                f"✅ Coincidencia encontrada ({mejor_puntuacion:.0%}): '{titulo_cancion}' de '{artista_encontrado}'"
            )
            return url_ytmusic, titulo_cancion, artista_encontrado
        else:
            logger.warning(
                f"⚠️ No se encontró coincidencia suficiente para '{titulo_video}' "
                f"(mejor puntuación: {mejor_puntuacion:.0%}, mínimo requerido: {umbral_minimo:.0%})"
            )
            return None, None, None
            return None, None, None

    except Exception as e:
        logger.error(f"Error buscando en YouTube Music: {e}")
        return None, None, None


def extraer_palabras_clave(texto):
    """
    Extrae palabras clave importantes de un texto para comparación.
    """
    # Convertir a minúsculas
    texto = texto.lower()

    # Eliminar caracteres especiales pero mantener # y números
    texto = re.sub(r"[^\w\s#áéíóúñ]", " ", texto)

    # Dividir en palabras
    palabras = texto.split()

    # Filtrar palabras muy cortas (excepto números y hashtags)
    palabras_clave = []
    for p in palabras:
        if len(p) >= 2 or p.startswith("#") or p.isdigit():
            palabras_clave.append(p)

    return set(palabras_clave)


def obtener_postprocessors_sponsorblock(config):
    """
    Genera los postprocesadores necesarios para SponsorBlock.
    Debe llamarse ANTES de agregar otros postprocesadores como FFmpegExtractAudio.

    Args:
        config: Diccionario de configuración del usuario

    Returns:
        list: Lista de postprocesadores de SponsorBlock
    """
    postprocessors = []

    if config.get("SponsorBlock_enabled", False):
        categorias_seleccionadas = config.get("SponsorBlock_categories", [])

        if categorias_seleccionadas:
            # Primero: obtener información de SponsorBlock
            postprocessors.append(
                {
                    "key": "SponsorBlock",
                    "categories": categorias_seleccionadas,
                    "api": "https://sponsor.ajay.app",
                }
            )
            # Segundo: modificar los capítulos para eliminar los segmentos marcados
            postprocessors.append(
                {
                    "key": "ModifyChapters",
                    "remove_sponsor_segments": categorias_seleccionadas,
                    "force_keyframes": False,
                }
            )

    return postprocessors


# ------------------------------ #


def crear_carpeta(carpeta):
    """
    Crea una carpeta si no existe.

    Args:
        carpeta: Nombre o ruta de la carpeta a crear

    Returns:
        str: Ruta completa de la carpeta
    """
    ruta_completa = os.path.join(SCRIPT_DIR, carpeta)
    if not os.path.exists(ruta_completa):
        os.makedirs(ruta_completa)
    return ruta_completa


# ------------------------------ #


def mover_archivo(cancion, carpeta):
    """
    Mueve un archivo a una carpeta específica.

    Args:
        cancion: Nombre del archivo a mover
        carpeta: Carpeta de destino
    """
    carpeta_completa = os.path.join(SCRIPT_DIR, carpeta)
    mp4_path = os.path.join(carpeta_completa, cancion)
    shutil.move(cancion, mp4_path)


# ------------------------------ #


def _convertir_video_ffmpeg(archivo_origen, archivo_destino, formato):
    """
    Convierte un video al formato especificado usando FFmpeg directamente.

    Args:
        archivo_origen: Ruta del archivo de origen
        archivo_destino: Ruta del archivo de destino
        formato: Formato de salida (mp4, avi, mkv, etc.)

    Returns:
        bool: True si la conversión fue exitosa, False en caso contrario
    """
    try:
        # Verificar que el archivo origen existe
        if not os.path.exists(archivo_origen):
            logger.error(f"Archivo origen no encontrado: {archivo_origen}")
            return False

        # Configuración de codecs por formato para máxima compatibilidad
        formato_config = {
            "mp4": {
                "vcodec": "libx264",
                "acodec": "aac",
                "extra_args": ["-preset", "medium", "-movflags", "+faststart"],
            },
            "avi": {
                "vcodec": "libxvid",
                "acodec": "libmp3lame",
                "extra_args": ["-qscale:v", "3"],
            },
            "mkv": {
                "vcodec": "libx264",
                "acodec": "aac",
                "extra_args": ["-preset", "medium"],
            },
        }

        config = formato_config.get(formato.lower())
        if not config:
            logger.error(f"Formato de video no soportado: {formato}")
            return False

        # En Windows, usar rutas cortas para evitar problemas con caracteres Unicode
        # Normalizar las rutas para asegurar compatibilidad
        archivo_origen = os.path.abspath(archivo_origen)
        archivo_destino = os.path.abspath(archivo_destino)

        cmd = (
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-stats",
                "-i",
                archivo_origen,
                "-c:v",
                config["vcodec"],
                "-c:a",
                config["acodec"],
            ]
            + config["extra_args"]
            + ["-y", archivo_destino]
        )

        logger.info(f"Convirtiendo video a {formato}...")
        logger.debug(f"Archivo origen: {archivo_origen}")
        logger.debug(f"Archivo destino: {archivo_destino}")

        # Ejecutar FFmpeg con encoding UTF-8 para manejar rutas con caracteres especiales
        # En Windows, especificamos el encoding explícitamente y PERMITIMOS mostrar la ventana/consola para ver stats
        result = subprocess.run(
            cmd,
            capture_output=False,  # Importante: False para que stdout/stderr vayan a la consola
            encoding="utf-8",
            errors="replace",
            # creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, # Comentado para permitir ver output
        )

        if result.returncode != 0:
            logger.error(f"Error de FFmpeg: {result.stderr}")
            return False

        logger.info(f"Video convertido exitosamente a {formato}")
        return True

    except FileNotFoundError:
        logger.error("FFmpeg no está instalado o no está en el PATH del sistema")
        return False
    except Exception as e:
        logger.error(f"Error al convertir video: {e}")
        return False


# ------------------------------ #


# Función para verificar si el archivo ya existe.
def archivo_duplicado(directorio, tipo, nombre):
    try:
        carpeta_tipo = tipo.capitalize()  # Convertir a mayúscula la primera letra
        # Usar una ruta absoluta basada en el directorio del script
        directorio_completo = os.path.join(SCRIPT_DIR, directorio, carpeta_tipo)

        archivos_en_ruta_actual = os.listdir(directorio_completo)

        for archivo in archivos_en_ruta_actual:
            if archivo == nombre:
                ruta_completa = os.path.abspath(
                    os.path.join(directorio_completo, nombre)
                )
                return ruta_completa

        return False

    except Exception as e:
        print(f"❌ Ocurrió un error al comprobar si el archivo es duplicado: {e}")
        return False


# ------------------------------ #


# Función para obtener los nombres de los artistas como una cadena separada por comas.
def obtener_nombre_artistas(track):
    if track["artists"]:
        artists = [artist["name"] for artist in track["artists"]]
        return ", ".join(artists)
    return ""


# ------------------------------ #


# Función para obtener el nombre del álbum completo desde la API de Spotify.
def obtener_nombre_album(sp, album_id):
    try:
        album = sp.album(album_id)
        if "name" in album:
            return album["name"]
        return None
    except Exception as e:
        print(f"❌ Ocurrió un error al obtener el nombre del álbum: {e}")
        return None


# ------------------------------ #


# Función para descargar la portada y agregarla a la canción (MP3 o MP4) usando Spotify.
def descargar_metadata(
    ruta_del_archivo, nombre_archivo, nombre_artista, client_id=None, client_secret=None
):
    """
    Descarga y agrega metadata de Spotify a un archivo de audio/video.

    Args:
        ruta_del_archivo: Ruta del archivo a modificar
        nombre_archivo: Nombre del archivo (para búsqueda)
        nombre_artista: Nombre del artista (para búsqueda)
        client_id: Client ID de Spotify (opcional, usa el por defecto si no se provee)
        client_secret: Client Secret de Spotify (opcional, usa el por defecto si no se provee)
    """
    # print("🔍 Scrappeando metadata de Spotify...")
    logger.info("Scrappeando metadata de Spotify...")
    try:
        # Usar credenciales por defecto si no se proporcionan
        spotify_client_id = client_id if client_id else SPOTIFY_CLIENT_ID
        spotify_client_secret = (
            client_secret if client_secret else SPOTIFY_CLIENT_SECRET
        )

        # Usar cliente global si las credenciales son las mismas
        global sp
        if sp and spotify_client_id == SPOTIFY_CLIENT_ID:
            spotify_client = sp
        else:
            # Inicializar cliente de autenticación de Spotify
            client_credentials_manager = SpotifyClientCredentials(
                spotify_client_id, spotify_client_secret
            )
            spotify_client = spotipy.Spotify(
                client_credentials_manager=client_credentials_manager,
                requests_timeout=10,
            )

        caracteres_especiales = ':/\\|?*"<>'

        # Buscar la canción en Spotify
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
                # Obtener la URL de la carátula del álbum
                url_artwork = track["album"]["images"][0]["url"]
                nombre_artistas = obtener_nombre_artistas(track)

                # Determine el formato de archivo
                formato_archivo = os.path.splitext(ruta_del_archivo)[1].lower()

                # Obtener los datos de la portada
                portada_data = requests.get(url_artwork).content

                # Intentar obtener el nombre del álbum completo de la API de Spotify
                nombre_album = obtener_nombre_album(sp, track["album"]["id"])
                if nombre_album:
                    # Limpiar caracteres especiales del nombre del álbum
                    nombre_album_limpio = "".join(
                        c if c not in caracteres_especiales else " "
                        for c in nombre_album
                    )
                else:
                    nombre_album_limpio = None

                # Obtener el año de publicación
                anio_publicacion = (
                    track["album"]["release_date"][:4]
                    if "album" in track and "release_date" in track["album"]
                    else None
                )

                if formato_archivo in [".mp3", ".wav", ".m4a", ".flac"]:
                    if formato_archivo == ".mp3":
                        # Crear o cargar tags ID3
                        try:
                            audio = ID3(ruta_del_archivo)
                        except ID3NoHeaderError:
                            # Si no hay header ID3, crear uno nuevo
                            audio = ID3()

                        # Limpiar tags existentes de portada para evitar duplicados
                        audio.delall("APIC")

                        # Agregar portada con configuración compatible con Windows Media Player
                        audio.add(
                            APIC(
                                encoding=3,  # UTF-8
                                mime="image/jpeg",
                                type=3,  # Cover (front)
                                desc="Front Cover",
                                data=portada_data,
                            )
                        )

                        # Agregar título de la canción
                        audio.delall("TIT2")
                        audio.add(TIT2(encoding=3, text=nombre_archivo))

                        # Agregar artista
                        audio.delall("TPE1")
                        audio.add(TPE1(encoding=3, text=nombre_artistas))

                        # Agregar álbum
                        if nombre_album_limpio:
                            audio.delall("TALB")
                            audio.add(TALB(encoding=3, text=nombre_album_limpio))

                        # Agregar año (TDRC es el estándar ID3v2.4, más compatible)
                        if anio_publicacion:
                            audio.delall("TDRC")
                            audio.delall("TYER")  # Eliminar tag antiguo si existe
                            audio.add(TDRC(encoding=3, text=anio_publicacion))

                        # Guardar como ID3v2.3 que es más compatible con Windows
                        audio.save(ruta_del_archivo, v2_version=3)
                    elif formato_archivo == ".wav":
                        # WAV: usar contenedor RIFF con chunk ID3 embebido; no reescribir el archivo como MP3
                        try:
                            audio_wave = WAVE(ruta_del_archivo)

                            # Crear tags ID3 dentro del WAV si no existen
                            if audio_wave.tags is None:
                                audio_wave.add_tags()

                            tags = audio_wave.tags

                            # Portada
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

                            # Título y artista
                            tags.delall("TIT2")
                            tags.add(TIT2(encoding=3, text=nombre_archivo))
                            tags.delall("TPE1")
                            tags.add(TPE1(encoding=3, text=nombre_artistas))

                            # Álbum
                            tags.delall("TALB")
                            if nombre_album_limpio:
                                tags.add(TALB(encoding=3, text=nombre_album_limpio))

                            # Año
                            tags.delall("TDRC")
                            tags.delall("TYER")
                            if anio_publicacion:
                                tags.add(TDRC(encoding=3, text=anio_publicacion))

                            # Guardar tags ID3 dentro del WAV (v2.3 para compatibilidad con WMP)
                            audio_wave.save(v2_version=3)
                            logger.info(
                                f"Metadatos ID3 agregados a WAV: {nombre_archivo}"
                            )
                        except Exception as e:
                            logger.error(
                                f"Error agregando metadatos a WAV (ID3 en RIFF): {e}"
                            )
                            # Fallback simple con INFO tags
                            try:
                                audio_wave = WAVE(ruta_del_archivo)
                                audio_wave["IART"] = nombre_artistas
                                audio_wave["INAM"] = nombre_archivo
                                if nombre_album_limpio:
                                    audio_wave["IPRD"] = nombre_album_limpio
                                if anio_publicacion:
                                    audio_wave["ICRD"] = anio_publicacion
                                audio_wave.save()
                            except Exception as e2:
                                logger.error(f"Error en fallback INFO para WAV: {e2}")
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

                        # Agregar imagen de portada
                        audio.clear_pictures()  # Limpiar imágenes existentes
                        image = Picture()
                        image.type = 3  # Cover (front)
                        image.mime = "image/jpeg"
                        image.desc = "Front Cover"
                        image.data = portada_data
                        audio.add_picture(image)
                        audio.save()
                elif formato_archivo in [".mp4", ".mov", ".avi", ".flv"]:
                    # Para formatos de video, usaremos MP4 para MP4 y MOV, y metadata básica para AVI y FLV
                    if formato_archivo in [".mp4", ".mov"]:
                        video = MP4(ruta_del_archivo)
                        video["\xa9nam"] = nombre_archivo
                        video["\xa9ART"] = nombre_artistas
                        video["covr"] = [
                            MP4Cover(
                                data=portada_data, imageformat=MP4Cover.FORMAT_JPEG
                            )
                        ]
                        if nombre_album_limpio:
                            video["\xa9alb"] = nombre_album_limpio
                        if anio_publicacion:
                            video["\xa9day"] = anio_publicacion
                        video.save()
                    else:
                        # Para AVI y FLV, no podemos agregar metadatos complejos
                        print(
                            f"No se pueden agregar metadatos complejos al formato {formato_archivo}"
                        )
                else:
                    print("Formato de archivo no compatible.")

        print("✅ Metadata agregada con éxito.")
    except Exception as e:
        print(f"❌ Ocurrió un error al descargar la portada y los artistas: {e}\n")


# ------------------------------ #


def sanitizar_nombre_archivo(titulo):
    """
    Sanitiza un título para usarlo como nombre de archivo en Windows.
    SOLO elimina caracteres que son ilegales en Windows, preservando el resto.

    Caracteres ilegales en Windows: < > : " / \\ | ? *
    El carácter # SÍ es válido en Windows.
    """
    nombre = titulo.strip()

    # Solo eliminar caracteres que son realmente ilegales en Windows
    # NO incluir # porque es válido
    caracteres_invalidos = r'[<>:"/\\|?*]'
    nombre = re.sub(caracteres_invalidos, "", nombre)

    # Eliminar puntos al final (problemático en Windows)
    nombre = re.sub(r"\.+$", "", nombre.strip())

    # Reemplazar múltiples espacios con uno solo
    nombre = re.sub(r"\s+", " ", nombre).strip()

    # Limitar longitud para evitar problemas con rutas muy largas
    if len(nombre) > 200:
        nombre = nombre[:200].strip()

    return nombre


def limpiar_titulo_para_busqueda(titulo):
    """
    Limpia un título para búsquedas en YouTube Music.
    Esta función es más agresiva porque busca obtener el nombre esencial
    de la canción para encontrar coincidencias.
    """
    titulo_limpio = titulo.strip()

    # Eliminar etiquetas comunes de videos
    patrones = [
        r"\(official\s*(lyric|audio|video|clip|trailer|teaser|stream)?\s*\)",
        r"\[official\s*(lyric|audio|video|clip|trailer|teaser|stream)?\s*\]",
        r"\(video oficial\)|\[video oficial\]",
        r"\(audio.*?\)|\[audio.*?\]",
        r"\(letra\)|\[letra\]",
        r"\(lyrics\)|\[lyrics\]",
        r"\(visualizer\)|\[visualizer\]",
        r"https?://\S+",
        r"www\.\S+",
        r"youtu\.?be\S*",
        r"@\w+",
        r"(4k|8k|uhd|hdr|full hd|1080p|720p|60fps|30fps|hq|lq)\b",
        r"[🧡💙💚❤️💛✨🔥⭐🌟🎉🎶🎵🎼📀💿🚀🙏😍🥳💥😈🤑]",
        r"\(live.*?\)",
    ]

    for patron in patrones:
        titulo_limpio = re.sub(patron, "", titulo_limpio, flags=re.IGNORECASE)

    # Limpiar espacios extra
    titulo_limpio = re.sub(r"\s+", " ", titulo_limpio).strip()

    return titulo_limpio


# Función legacy - mantener por compatibilidad pero usar sanitizar_nombre_archivo
def limpiar_titulo(titulo):
    """
    DEPRECATED: Usar sanitizar_nombre_archivo() para nombres de archivo
    y limpiar_titulo_para_busqueda() para búsquedas.

    Esta función ahora solo sanitiza para nombres de archivo.
    """
    return sanitizar_nombre_archivo(titulo)


# ------------------------------ #


# Función principal para descargar el audio de un video de YouTube.
def descargar_audio(config, url):
    global audios_exito, audios_error
    try:
        # Configurar calidad y formato de audio
        # min: peor calidad disponible, avg: calidad media (~128kbps), max: mejor calidad
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

        # Configurar yt-dlp sin cookies
        with yt_dlp.YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "extractor_retries": 3,
                "fragment_retries": 3,
                "retry_sleep": 1,
            }
        ) as ydl:
            info = ydl.extract_info(url, download=False)
            titulo_original = info.get("title", "Unknown Title")
            uploader_original = info.get("uploader", "Unknown Uploader")

            # Limpiar el título eliminando artistas y etiquetas innecesarias
            titulo_limpio = limpiar_titulo(titulo_original)

            # Variable para la URL final de descarga
            url_descarga = url
            artista_final = uploader_original

            # Si está habilitada la opción de YouTube Music, buscar el audio puro
            if config.get("Preferir_YouTube_Music", False):
                print(f"🔍 Buscando versión de audio puro en YouTube Music...")
                url_ytmusic, titulo_ytmusic, artista_ytmusic = buscar_en_youtube_music(
                    titulo_original, uploader_original
                )

                if url_ytmusic:
                    url_descarga = url_ytmusic
                    artista_final = artista_ytmusic
                    print(
                        f"✅ Encontrado en YouTube Music: '{titulo_ytmusic}' de '{artista_ytmusic}'"
                    )
                else:
                    print(f"⚠️ No se encontró en YouTube Music, usando URL original")

            # Usar rutas absolutas basadas en el directorio del script
            carpeta_audio = os.path.join(SCRIPT_DIR, "Descargas", "Audio")
            crear_carpeta(carpeta_audio)

            # Asegurar que el archivo tenga la extensión correcta sin duplicaciones
            nombre_archivo = f"{titulo_limpio}"
            archivo_audio = os.path.join(carpeta_audio, nombre_archivo)
            archivo_audio = os.path.normpath(archivo_audio)

            es_archivo_duplicado = archivo_duplicado(
                "Descargas",
                "Audio",
                f"{nombre_archivo}.{formato_audio}",
            )

            if not es_archivo_duplicado:
                print(f"🡳  Descargando audio de: '{titulo_original}'...")

                # Construir lista de postprocesadores
                # El orden es importante: SponsorBlock debe ir ANTES de la conversión de audio
                postprocessors = []

                # Agregar postprocesadores de SponsorBlock primero (si están habilitados)
                postprocessors.extend(obtener_postprocessors_sponsorblock(config))

                # Luego agregar el extractor de audio
                postprocessors.append(
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": formato_audio,
                        "preferredquality": calidad_audio,
                    }
                )

                ydl_opts = obtener_opciones_base_ytdlp()
                ydl_opts.update(
                    {
                        "format": calidad_format,
                        "postprocessors": postprocessors,
                        "outtmpl": archivo_audio,
                    }
                )

                # Agregar opciones adicionales de SponsorBlock si están habilitadas
                ydl_opts.update(obtener_opciones_sponsorblock(config))

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url_descarga])

                if config.get("Scrappear_metadata", False):
                    archivo_audio_con_extension = f"{archivo_audio}.{formato_audio}"
                    descargar_metadata(
                        archivo_audio_con_extension,
                        titulo_limpio,
                        artista_final,
                    )

                audios_exito += 1
                canciones_descargadas.append(f"{archivo_audio}.{formato_audio}")

                print(f"✔️  Se ha descargado '{titulo_original}' con éxito.\n")
                return True
            else:
                print(
                    f"✘ Salteando el audio de '{titulo_original}' debido a que ya se ha descargado...\n"
                )
                canciones_descargadas.append(es_archivo_duplicado)
                audios_exito += 1
                return True

    except Exception as e:
        print(f"❌ Ocurrió un error al descargar el audio: {e}\n")
        audios_error += 1
        return False


# ------------------------------ #


# Función principal para descargar el video de YouTube.
# NOTA: Esta función se mantiene por compatibilidad. El nuevo código usa _descargar_video_interno.
def descargar_video_legacy(config, url):
    """
    Función legacy para descargar videos.
    DEPRECATED: Usar _descargar_video_interno con DownloadResult en nuevo código.
    """
    global videos_exito, videos_error
    try:
        # Selectores de formato con sintaxis válida de yt-dlp
        # Orden de prioridad: primero intenta formato específico, luego fallback
        # Prioridad AVC1 (H.264) para máxima compatibilidad y evitar errores 403
        calidad_map = {
            "min": "bestvideo[height<=360][vcodec^=avc1]+bestaudio/bestvideo[height<=360]+bestaudio/best",
            "avg": "bestvideo[height<=720][vcodec^=avc1]+bestaudio/bestvideo[height<=720]+bestaudio/best",
            "max": "bestvideo[vcodec^=avc1]+bestaudio/bestvideo+bestaudio/best",
        }
        calidad_video = calidad_map.get(
            config.get("Calidad_audio_video", "avg"),
            calidad_map["avg"],
        )
        formato_video = config.get("Formato_video", "mp4")

        # Extraer la información del video sin descargarlo
        with yt_dlp.YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "extractor_retries": 3,
                "fragment_retries": 3,
                "retry_sleep": 1,
            }
        ) as ydl:
            info = ydl.extract_info(url, download=False)
            titulo_original = info.get("title", "Unknown Title")
            uploader = info.get("uploader", "Unknown Uploader")

            # Logging de formatos disponibles para debugging
            if info.get("formats"):
                logger.info(f"Formatos disponibles para '{titulo_original}':")
                for fmt in info["formats"][:10]:  # Mostrar primeros 10 formatos
                    resolution = fmt.get("resolution", "audio only")
                    format_note = fmt.get("format_note", "")
                    filesize = fmt.get("filesize", 0)
                    size_mb = filesize / (1024 * 1024) if filesize else 0
                    logger.info(
                        f"  Format {fmt.get('format_id')}: {resolution} {format_note} (~{size_mb:.1f}MB)"
                    )

            # Limpiar el título usando la misma función que en descargar_audio
            titulo_limpio = limpiar_titulo(titulo_original)

            # Usar rutas absolutas basadas en el directorio del script
            carpeta_video = os.path.join(SCRIPT_DIR, "Descargas", "Video")
            crear_carpeta(carpeta_video)

            # Construir la ruta del archivo (sin duplicar la extensión)
            archivo_video = os.path.join(carpeta_video, titulo_limpio)
            archivo_video = os.path.normpath(archivo_video)

            es_archivo_duplicado = archivo_duplicado(
                "Descargas",
                "Video",
                f"{titulo_limpio}.{formato_video}",
            )

            if not es_archivo_duplicado:
                logger.info(f"Descargando video: '{titulo_original}'")
                logger.info(
                    f"Calidad seleccionada: {config.get('Calidad_audio_video', 'avg')}"
                )
                logger.info(f"Selector de formato: {calidad_video}")

                # Descargar primero en formato MKV (contenedor universal)
                archivo_temp = archivo_video + "_temp"

                # Construir lista de postprocesadores para SponsorBlock
                postprocessors = obtener_postprocessors_sponsorblock(config)

                ydl_opts = obtener_opciones_base_ytdlp()
                ydl_opts.update(
                    {
                        "format": calidad_video,
                        "merge_output_format": "mkv",
                        "outtmpl": archivo_temp,
                        # Mostrar información del formato seleccionado para debugging
                        "quiet": False,
                        "no_warnings": False,
                    }
                )

                # Agregar postprocesadores si hay alguno
                if postprocessors:
                    ydl_opts["postprocessors"] = postprocessors

                # Agregar opciones adicionales de SponsorBlock si están habilitadas
                ydl_opts.update(obtener_opciones_sponsorblock(config))

                logger.info(f"Iniciando descarga con opciones: format={calidad_video}")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                # Convertir al formato deseado
                archivo_final = f"{archivo_video}.{formato_video}"

                # yt-dlp puede crear el archivo con o sin extensión .mkv
                # Buscar el archivo temporal de manera flexible
                archivo_temp_mkv = f"{archivo_temp}.mkv"
                if not os.path.exists(archivo_temp_mkv):
                    # Intentar sin extensión
                    if os.path.exists(archivo_temp):
                        archivo_temp_mkv = archivo_temp
                        logger.info(
                            f"Archivo temporal encontrado sin extensión: {archivo_temp}"
                        )
                    else:
                        logger.error(
                            f"Archivo temporal no encontrado después de la descarga"
                        )
                        logger.info("Buscando archivos en el directorio...")
                        # Buscar archivos con nombre similar en el directorio
                        directorio = os.path.dirname(archivo_temp)
                        base_name = os.path.basename(archivo_temp)
                        for archivo in os.listdir(directorio):
                            if base_name in archivo:
                                logger.info(f"Archivo encontrado: {archivo}")
                        videos_error += 1
                        return False

                conversion_exitosa = _convertir_video_ffmpeg(
                    archivo_temp_mkv, archivo_final, formato_video
                )

                # Eliminar archivos temporales (con y sin extensión)
                if os.path.exists(archivo_temp_mkv):
                    os.remove(archivo_temp_mkv)
                if os.path.exists(archivo_temp) and archivo_temp != archivo_temp_mkv:
                    os.remove(archivo_temp)

                if not conversion_exitosa:
                    logger.error(f"Error en conversión de video a {formato_video}")
                    videos_error += 1
                    return False

                if config.get("Scrappear_metadata", False):
                    descargar_metadata(
                        archivo_final,
                        titulo_limpio,
                        uploader,
                    )

                videos_exito += 1
                canciones_descargadas.append(archivo_final)

                logger.info(f"Video descargado: '{titulo_original}'")
                return True
            else:
                logger.info(f"Video duplicado: '{titulo_original}'")
                canciones_descargadas.append(es_archivo_duplicado)
                videos_exito += 1
                return True

    except Exception as e:
        logger.error(f"Error al descargar video: {e}")
        videos_error += 1
        return False


# ------------------------------ #


# Función para buscar la canción en el motor de búsqueda de YouTube y devolver el link.
def buscar_cancion_youtube(query):
    try:
        # Para búsquedas, NO especificar formato - solo obtener la URL
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,  # No descargar, solo buscar
            "default_search": "ytsearch",
            "noplaylist": True,
            "extract_flat": "in_playlist",  # Extracción plana para búsquedas
            "extractor_retries": 3,
            "socket_timeout": 20,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Buscando en YouTube: ytsearch1:{query}")
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)

            if (
                info
                and "entries" in info
                and info["entries"]
                and len(info["entries"]) > 0
            ):
                entry = info["entries"][0]
                if entry:
                    # Construir URL del video
                    video_id = entry.get("id")
                    if video_id:
                        link_youtube = f"https://www.youtube.com/watch?v={video_id}"
                        logger.info(f"Video encontrado en YouTube: {link_youtube}")
                        print("✔️  Video de YouTube obtenido.")
                        return link_youtube

            logger.warning(f"No se encontraron resultados para: {query}")
            print(
                "❌ Ocurrió un error al obtener el video de YouTube, intente de nuevo.\n"
            )
            return ""

    except Exception as e:
        logger.error(f"Error al buscar en YouTube: {e}", exc_info=True)
        print(f"❌ Ocurrió un error al buscar el video: {e}\n")
        return ""


# ------------------------------ #


# Función para obtener los videos de una playlist de YouTube o Spotify en un archivo de texto.
def obtener_playlist(config, plataforma, playlist_url):
    try:
        print(f"↻ Obteniendo canciones de la playlist de {plataforma}...")

        urls = []

        if plataforma == "YouTube":
            ydl_opts = obtener_opciones_base_ytdlp()
            ydl_opts.update(
                {
                    "extract_flat": True,
                    "force_generic_extractor": True,
                }
            )
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(playlist_url, download=False)
                if "entries" in result:
                    urls = [entry["url"] for entry in result["entries"]]

        elif plataforma == "Spotify":
            global sp

            # Usar cliente global si las credenciales son las mismas
            if sp and config.get("Client_ID") == SPOTIFY_CLIENT_ID:
                spotify_client = sp
            else:
                client_credentials_manager = SpotifyClientCredentials(
                    config["Client_ID"],
                    config["Secret_ID"],
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

            # Usamos ThreadPoolExecutor para buscar videos concurrentemente
            with concurrent.futures.ThreadPoolExecutor() as executor:
                consultas_youtube = [
                    f"{nombre_cancion} {nombre_artista} Oficial audio"
                    for nombre_cancion, nombre_artista in canciones_totales
                ]

                resultados_youtube = list(
                    executor.map(buscar_cancion_youtube, consultas_youtube)
                )
                urls.extend(resultados_youtube)

        print(
            f"✔️  Se han obtenido {len(urls)} video(s) de la playlist de {plataforma}.\n"
        )
        return urls

    except Exception as e:
        print(
            f"❌ Ocurrió un error al obtener los video(s) de la playlist de {plataforma}: {e}"
        )
        return []


# ------------------------------ #


# Función para obtener el link de YouTube de una canción de Spotify.
def obtener_cancion_Spotify(config, link_spotify):
    global sp

    try:
        # Intentar usar el cliente global primero
        spotify_client = sp

        # Si no hay cliente global o el usuario tiene credenciales custom, crear uno nuevo
        if not spotify_client or (config.get("Client_ID") != SPOTIFY_CLIENT_ID):
            try:
                client_credentials_manager = SpotifyClientCredentials(
                    config["Client_ID"],
                    config["Secret_ID"],
                )
                spotify_client = spotipy.Spotify(
                    client_credentials_manager=client_credentials_manager,
                    requests_timeout=10,
                )
            except Exception as e:
                logger.error(f"Error creando cliente de Spotify: {e}")
                if not sp:
                    print("❌ No se pudo conectar con Spotify")
                    return ""
                spotify_client = sp

        # Validar que sea una URL de track de Spotify (soporta todos los formatos)
        if "spotify.com" not in link_spotify or "/track/" not in link_spotify:
            print("❌ Por favor ingrese un enlace válido de una canción de Spotify.")
            logger.warning(f"URL de Spotify inválida: {link_spotify}")
            return ""

        # Extraer track ID (funciona con cualquier formato de URL de Spotify)
        try:
            track_id = link_spotify.split("/track/")[1].split("?")[0].split("/")[0]
            logger.info(f"Extrayendo información de track de Spotify: {track_id}")
            track_info = spotify_client.track(track_id)
            nombre_cancion = track_info["name"]
            nombre_artista = track_info["artists"][0]["name"]
            query = f"{nombre_cancion} {nombre_artista}"

            logger.info(f"Buscando en YouTube: {query}")
            print(f"🔍 Buscando '{query}' en YouTube...")

            # Buscar usando yt-dlp (más confiable y sin dependencias problemáticas)
            link_youtube = buscar_cancion_youtube(query)

            if link_youtube:
                print("✔️  Canción de Spotify obtenida.\n")
                return link_youtube
            else:
                print(
                    "❌ Ocurrió un error al obtener el video de Spotify, intente de nuevo."
                )
                return ""
        except IndexError as e:
            logger.error(f"Error al parsear URL de Spotify: {e}")
            print("❌ URL de Spotify mal formada.")
            return ""

    except Exception as e:
        print(f"❌ Ocurrió un error al obtener el video de Spotify: {e}")
        logger.error(f"Error en obtener_cancion_Spotify: {e}", exc_info=True)
        return ""


# ------------------------------ #


# Función para editar el archivo config.json directamente desde la ejecución del programa.
def editar_config():
    while True:
        print("📝 Mostrando configuración...")

        with open("config.json", "r") as f:
            config = json.load(f)

        # Mostrar cada clave y su valor correspondiente
        index = 1
        for key, value in config.items():
            print(f"    {index} - {key}: {value}")
            index += 1

        modificar = input(
            "Ingrese el número de la configuración que desea modificar -> "
        )

        # Verificar si la entrada es un número válido
        try:
            opcion = int(modificar)
            if opcion < 1 or opcion > len(config):
                print("Opción no válida")
            else:
                # Obtener la clave correspondiente al número ingresado
                clave_a_modificar = list(config.keys())[opcion - 1]
                valor_a_modificar = list(config.values())[opcion - 1]

                # Verificar si es un Booleano
                if isinstance(valor_a_modificar, bool):
                    nuevo_valor = not valor_a_modificar
                    config[clave_a_modificar] = nuevo_valor

                    # Aplicar los cambios en el Json
                    with open("config.json", "w") as f:
                        json.dump(config, f, indent=4)
                else:
                    if clave_a_modificar == "Calidad_audio_video":
                        while True:
                            nuevo_valor = input(
                                f"Ingrese el nuevo valor para '{clave_a_modificar}'. Valores disponibles: max, min, avg -> "
                            )
                            if nuevo_valor in ["max", "min", "avg"]:
                                break
                            print(
                                "❌ Ocurrió un error al guardar el archivo. Valores disponibles: max, min, avg"
                            )
                    else:
                        nuevo_valor = input(
                            f"Ingrese el nuevo valor para '{clave_a_modificar}' -> "
                        )
                    config[clave_a_modificar] = nuevo_valor

                    with open("config.json", "w") as f:
                        json.dump(config, f, indent=4)

                print(
                    f"'{clave_a_modificar}' se ha sido modificado a '{nuevo_valor}' con éxito."
                )

        except ValueError:
            print("Por favor, ingrese un número.")

        repetir = input("¿Desea modificar algo más? (S/N) -> ").upper()
        if repetir != "S":
            break


# ------------------------------ #


# Función para limpiar nombres de archivos
def limpiar_nombre_archivo(nombre):
    """
    Limpia el nombre de un archivo eliminando caracteres inválidos para Windows.
    Caracteres inválidos: < > : " / \\ | ? *
    El carácter # SÍ es válido en Windows.
    """
    # Elimina SOLO caracteres inválidos en Windows: < > : " / \ | ? *
    nombre_limpio = re.sub(r'[<>:"/\\|?*]', "", nombre)
    # Eliminar puntos al final (problemático en Windows)
    nombre_limpio = re.sub(r"\.+$", "", nombre_limpio.strip())
    return nombre_limpio


# ------------------------------ #


# Función para comprimir y mover archivos
def comprimir_y_mover_archivos(
    nombre_archivo_final, canciones_descargadas, output_folder=None
):
    try:
        # Usar rutas absolutas basadas en el directorio del script
        if output_folder:
            carpeta_zip = output_folder
        else:
            carpeta_zip = os.path.join(SCRIPT_DIR, "Descargas", "Zip")

        os.makedirs(carpeta_zip, exist_ok=True)  # Crear la carpeta si no existe

        # Limpiar el nombre del archivo ZIP y asegurar que no tenga extensión .zip
        nombre_archivo_final = limpiar_nombre_archivo(nombre_archivo_final)
        # Eliminar la extensión .zip si ya existe en el nombre
        if nombre_archivo_final.lower().endswith(".zip"):
            nombre_archivo_final = nombre_archivo_final[:-4]

        nombre_zip = os.path.join(carpeta_zip, f"{nombre_archivo_final}.zip")

        # Crear archivo ZIP
        with zipfile.ZipFile(nombre_zip, "w") as zipf:
            for archivo in canciones_descargadas:
                archivo_limpio = limpiar_nombre_archivo(os.path.basename(archivo))
                zipf.write(archivo, archivo_limpio)  # Agregar con nombre limpio

        # Eliminar archivos originales
        for archivo in canciones_descargadas:
            os.remove(archivo)

        # Convertir a ruta absoluta
        ruta_absoluta = os.path.abspath(nombre_zip)

        print(f"📂 Archivos comprimidos en '{os.path.basename(nombre_zip)}'.\n")
        return ruta_absoluta
    except Exception as e:
        logger.error(f"Error al comprimir archivos: {e}")
        return None


# ------------------------------ #


class DownloadResult:
    """Clase para almacenar resultados de descargas sin usar variables globales."""

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
        """Actualiza el progreso usando el callback si está disponible."""
        if self.progress_callback:
            # Calcular porcentaje basado en items completados
            if self.total_items > 0:
                base_percent = int((self.completed_items / self.total_items) * 80) + 10
            else:
                base_percent = 10
            self.progress_callback(min(base_percent, 90), status, detail)

    def reset(self):
        """Reinicia los contadores."""
        self.audios_exito = 0
        self.audios_error = 0
        self.videos_exito = 0
        self.videos_error = 0
        self.canciones_descargadas = []


def iniciar(config, dato, nombre_archivo_final="archivos.zip"):
    """
    Función principal para iniciar el proceso de descarga.

    Args:
        config: Diccionario con la configuración del usuario
        dato: URL o término de búsqueda
        nombre_archivo_final: Nombre del archivo ZIP resultante

    Returns:
        str: Ruta del archivo descargado o None si hubo error
    """
    # Usar objeto para almacenar resultados (evita variables globales)
    result = DownloadResult()

    # Crear las carpetas de descarga
    crear_carpeta(os.path.join(SCRIPT_DIR, "Descargas", "Audio"))
    crear_carpeta(os.path.join(SCRIPT_DIR, "Descargas", "Video"))

    # Lista con todas las urls a descargar
    urls = []

    try:
        # Descargar videos de una lista de reproducción de YouTube
        if config.get("Utilizar_playlist_YouTube"):
            urls.extend(obtener_playlist(config, "YouTube", dato))

        # Descargar videos de una lista de reproducción de Spotify
        if config.get("Utilizar_playlist_Spotify"):
            urls.extend(obtener_playlist(config, "Spotify", dato))

        # Descargar desde una búsqueda en YouTube
        if config.get("Busqueda_en_YouTube"):
            if dato:
                # Detectar automáticamente si es una URL de YouTube
                es_url_youtube = any(
                    p in dato.lower()
                    for p in ["youtube.com", "youtu.be", "music.youtube.com"]
                )

                if es_url_youtube:
                    # Es una URL directa, usarla sin buscar
                    urls.append(dato)
                else:
                    # Es un término de búsqueda
                    link_youtube = buscar_cancion_youtube(dato)
                    if link_youtube:
                        urls.append(link_youtube)
            else:
                logger.warning("Campo de búsqueda vacío")

        # Descargar desde un enlace de Spotify
        if config.get("Utilizar_link_de_Spotify"):
            if dato:
                url_spotify = obtener_cancion_Spotify(config, dato)
                if url_spotify:
                    urls.append(url_spotify)

        inicio = time.time()

        # Procesar todas las descargas
        for url in urls:
            if not url:
                continue

            if config.get("Descargar_video"):
                _descargar_video_interno(config, url, result)
            if config.get("Descargar_audio"):
                _descargar_audio_interno(config, url, result)

        fin = time.time()
        logger.info(f"Tiempo de ejecución: {fin - inicio:.2f} segundos")

        # Determinar resultado final
        if len(result.canciones_descargadas) > 1:
            ruta_descarga = comprimir_y_mover_archivos(
                nombre_archivo_final, result.canciones_descargadas
            )
        elif len(result.canciones_descargadas) == 1:
            ruta_descarga = os.path.abspath(result.canciones_descargadas[0])
        else:
            ruta_descarga = None

        # Log de resultados
        if result.audios_exito >= 1 and result.videos_exito >= 1:
            logger.info(
                f"Descargados {result.audios_exito} audios y {result.videos_exito} videos"
            )
        elif result.audios_exito >= 1:
            logger.info(f"Descargados {result.audios_exito} audios")
        elif result.videos_exito >= 1:
            logger.info(f"Descargados {result.videos_exito} videos")

        logger.info(f"Ruta del archivo: {ruta_descarga}")
        return ruta_descarga

    except Exception as e:
        logger.error(f"Error en proceso de descarga: {e}")
        return None


def iniciar_con_progreso(
    config,
    dato,
    nombre_archivo_final="archivos.zip",
    progress_callback=None,
    base_folder=None,
):
    """
    Función principal para iniciar el proceso de descarga con soporte de progreso.
    Detecta automáticamente si es una playlist, URL individual o búsqueda.

    Args:
        config: Diccionario con la configuración del usuario
        dato: URL o término de búsqueda
        nombre_archivo_final: Nombre del archivo ZIP resultante
        progress_callback: Función callback(percent, status, detail) para reportar progreso
        base_folder: Carpeta base para descargas (opcional, para concurrencia)

    Returns:
        str: Ruta del archivo descargado o None si hubo error
    """
    # Usar objeto para almacenar resultados con callback de progreso
    result = DownloadResult(progress_callback)

    # Crear las carpetas de descarga si no se provee base_folder
    if not base_folder:
        crear_carpeta(os.path.join(SCRIPT_DIR, "Descargas", "Audio"))
        crear_carpeta(os.path.join(SCRIPT_DIR, "Descargas", "Video"))
    else:
        crear_carpeta(base_folder)

    # Lista con todas las urls a descargar
    urls = []

    try:
        if progress_callback:
            progress_callback(5, "Preparando...", "Analizando solicitud")

        # Obtener fuente de descarga (YouTube o Spotify)
        fuente = config.get("Fuente_descarga", "YouTube")

        if fuente == "YouTube":
            # Auto-detectar tipo de contenido para YouTube
            dato_lower = dato.lower() if dato else ""

            # Verificar si es una URL de YouTube/YouTube Music
            es_url_youtube = any(
                p in dato_lower
                for p in ["youtube.com", "youtu.be", "music.youtube.com"]
            )

            if es_url_youtube:
                # Es una URL - verificar si es playlist o video individual
                if es_url_playlist(dato):
                    # Es una playlist de YouTube/YouTube Music
                    if progress_callback:
                        progress_callback(
                            10, "Obteniendo playlist...", "Conectando con YouTube"
                        )
                    urls.extend(obtener_playlist(config, "YouTube", dato))
                else:
                    # Es un video/canción individual
                    if progress_callback:
                        progress_callback(
                            10, "Procesando enlace...", "URL de YouTube detectada"
                        )
                    urls.append(dato)
            else:
                # No es una URL, es un término de búsqueda
                if dato:
                    if progress_callback:
                        progress_callback(
                            10, "Buscando en YouTube...", f"Buscando: {dato[:50]}"
                        )

                    # Usar YouTube Music si está preferido
                    if config.get("Preferir_YouTube_Music"):
                        url_ytmusic, titulo, artista = buscar_en_youtube_music(
                            dato, dato
                        )
                        if url_ytmusic:
                            urls.append(url_ytmusic)
                        else:
                            # Fallback a YouTube normal
                            link_youtube = buscar_cancion_youtube(dato)
                            if link_youtube:
                                urls.append(link_youtube)
                    else:
                        link_youtube = buscar_cancion_youtube(dato)
                        if link_youtube:
                            urls.append(link_youtube)
                else:
                    logger.warning("Campo de búsqueda vacío")

        elif fuente == "Spotify":
            # Auto-detectar tipo de contenido para Spotify
            if dato:
                dato_lower = dato.lower()

                # Verificar si es una URL de Spotify
                es_url_spotify = (
                    "spotify.com" in dato_lower or "open.spotify" in dato_lower
                )

                if es_url_spotify:
                    # Es una URL de Spotify - verificar si es playlist o track
                    if "playlist" in dato_lower:
                        # Es una playlist de Spotify
                        if progress_callback:
                            progress_callback(
                                10, "Obteniendo playlist...", "Conectando con Spotify"
                            )
                        urls.extend(obtener_playlist(config, "Spotify", dato))
                    elif "album" in dato_lower:
                        # Es un álbum de Spotify - tratarlo como playlist
                        if progress_callback:
                            progress_callback(
                                10, "Obteniendo álbum...", "Conectando con Spotify"
                            )
                        urls.extend(obtener_playlist(config, "Spotify", dato))
                    else:
                        # Es un track individual de Spotify
                        if progress_callback:
                            progress_callback(
                                10, "Obteniendo canción...", "Buscando en Spotify"
                            )
                        url_spotify = obtener_cancion_Spotify(config, dato)
                        if url_spotify:
                            urls.append(url_spotify)
                else:
                    # No es URL de Spotify, buscar por nombre
                    if progress_callback:
                        progress_callback(
                            10, "Buscando en Spotify...", f"Buscando: {dato[:50]}"
                        )
                    # Buscar canción en YouTube usando el término
                    link_youtube = buscar_cancion_youtube(dato)
                    if link_youtube:
                        urls.append(link_youtube)
            else:
                logger.warning("Campo de búsqueda vacío")

        if not urls:
            if progress_callback:
                progress_callback(
                    100, "Sin resultados", "No se encontraron URLs para descargar"
                )
            return None

        # Calcular total de items para el progreso
        total_downloads = 0
        if config.get("Descargar_video"):
            total_downloads += len(urls)
        if config.get("Descargar_audio"):
            total_downloads += len(urls)
        result.total_items = max(total_downloads, 1)

        if progress_callback:
            progress_callback(
                15, "Iniciando descargas...", f"{len(urls)} elemento(s) encontrado(s)"
            )

        inicio = time.time()

        # Procesar todas las descargas
        for i, url in enumerate(urls):
            if not url:
                continue

            if config.get("Descargar_video"):
                if progress_callback:
                    # Calcular progreso: 15% inicio + hasta 80% para descargas
                    percent = 15 + int(
                        (result.completed_items / result.total_items) * 70
                    )
                    progress_callback(
                        percent,
                        "Descargando video...",
                        f"Elemento {i+1} de {len(urls)}",
                    )
                _descargar_video_interno(config, url, result, base_folder)
                result.completed_items += 1

            if config.get("Descargar_audio"):
                if progress_callback:
                    percent = 15 + int(
                        (result.completed_items / result.total_items) * 70
                    )
                    progress_callback(
                        percent,
                        "Descargando audio...",
                        f"Elemento {i+1} de {len(urls)}",
                    )
                _descargar_audio_interno(config, url, result, base_folder)
                result.completed_items += 1

        fin = time.time()
        logger.info(f"Tiempo de ejecución: {fin - inicio:.2f} segundos")

        if progress_callback:
            progress_callback(90, "Finalizando...", "Procesando archivos descargados")

        # Determinar resultado final
        if len(result.canciones_descargadas) > 1:
            if progress_callback:
                progress_callback(92, "Comprimiendo...", "Creando archivo ZIP")

            # Usar la carpeta base para el ZIP si existe, o default a Descargas/Zip
            output_folder = (
                base_folder
                if base_folder
                else os.path.join(SCRIPT_DIR, "Descargas", "Zip")
            )

            ruta_descarga = comprimir_y_mover_archivos(
                nombre_archivo_final, result.canciones_descargadas, output_folder
            )
        elif len(result.canciones_descargadas) == 1:
            ruta_descarga = os.path.abspath(result.canciones_descargadas[0])
        else:
            ruta_descarga = None

        if progress_callback:
            progress_callback(98, "¡Listo!", "Preparando descarga")

        # Log de resultados
        if result.audios_exito >= 1 and result.videos_exito >= 1:
            logger.info(
                f"Descargados {result.audios_exito} audios y {result.videos_exito} videos"
            )
        elif result.audios_exito >= 1:
            logger.info(f"Descargados {result.audios_exito} audios")
        elif result.videos_exito >= 1:
            logger.info(f"Descargados {result.videos_exito} videos")

        logger.info(f"Ruta del archivo: {ruta_descarga}")
        return ruta_descarga

    except Exception as e:
        logger.error(f"Error en proceso de descarga: {e}")
        if progress_callback:
            progress_callback(100, "Error", str(e)[:100])
        return None


def _descargar_audio_interno(config, url, result, base_folder=None):
    """
    Versión interna de descargar_audio que usa objeto result.

    Args:
        config: Configuración del usuario
        url: URL del video
        result: Objeto DownloadResult para almacenar resultados
        base_folder: Carpeta base para descargas (opcional)
    """
    try:
        # Detectar si es una URL de Spotify y convertir a YouTube
        if "spotify.com" in url and "/track/" in url:
            logger.info(f"Detectada URL de Spotify, convirtiendo a YouTube: {url}")
            url_youtube = obtener_cancion_Spotify(config, url)
            if not url_youtube:
                logger.error(f"No se pudo convertir URL de Spotify a YouTube: {url}")
                result.audios_error += 1
                return
            logger.info(f"URL convertida: {url_youtube}")
            url = url_youtube

        # Configurar calidad de audio
        # min: peor calidad disponible (más liviano), avg: calidad media (~128kbps), max: mejor calidad (más pesado)
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

        # Primero obtener información del video original
        info_opts = obtener_opciones_base_ytdlp()
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            titulo_original = info.get("title", "Unknown Title")
            uploader_original = info.get("uploader", "Unknown Uploader")
            titulo_limpio = limpiar_titulo(titulo_original)

            # Variable para la URL final de descarga
            url_descarga = url
            artista_final = uploader_original

            # Si está habilitada la opción de YouTube Music, buscar el audio puro
            if config.get("Preferir_YouTube_Music", False):
                logger.info(
                    f"Buscando versión de audio puro en YouTube Music para: '{titulo_original}'"
                )
                url_ytmusic, titulo_ytmusic, artista_ytmusic = buscar_en_youtube_music(
                    titulo_original, uploader_original
                )

                if url_ytmusic:
                    url_descarga = url_ytmusic
                    artista_final = artista_ytmusic
                    logger.info(
                        f"Usando audio de YouTube Music: '{titulo_ytmusic}' de '{artista_ytmusic}'"
                    )
            else:
                logger.info(f"No se encontró en YouTube Music, usando URL original")

            if base_folder:
                carpeta_audio = base_folder
            else:
                carpeta_audio = os.path.join(SCRIPT_DIR, "Descargas", "Audio")

            crear_carpeta(carpeta_audio)

            nombre_archivo = f"{titulo_limpio}"
            archivo_audio = os.path.join(carpeta_audio, nombre_archivo)
            archivo_audio = os.path.normpath(archivo_audio)

            es_archivo_duplicado = False
            # Solo verificar duplicados si NO estamos usando una carpeta temporal única
            if not base_folder:
                es_archivo_duplicado = archivo_duplicado(
                    "Descargas", "Audio", f"{nombre_archivo}.{formato_audio}"
                )

            if not es_archivo_duplicado:
                logger.info(f"Descargando audio: '{titulo_original}'")

                # Construir lista de postprocesadores
                # El orden es importante: SponsorBlock debe ir ANTES de la conversión de audio
                postprocessors = []

                # Agregar postprocesadores de SponsorBlock primero (si están habilitados)
                postprocessors.extend(obtener_postprocessors_sponsorblock(config))

                # Luego agregar el extractor de audio
                postprocessors.append(
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": formato_audio,
                        "preferredquality": calidad_audio,
                    }
                )

                # Usar opciones base con cookies y headers
                ydl_opts = obtener_opciones_base_ytdlp()
                ydl_opts.update(
                    {
                        "format": calidad_format,
                        "postprocessors": postprocessors,
                        "outtmpl": archivo_audio,
                    }
                )

                # Agregar opciones adicionales de SponsorBlock si están habilitadas
                ydl_opts.update(obtener_opciones_sponsorblock(config))

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url_descarga])

                if config.get("Scrappear_metadata", False):
                    archivo_audio_con_extension = f"{archivo_audio}.{formato_audio}"
                    descargar_metadata(
                        archivo_audio_con_extension,
                        titulo_limpio,
                        artista_final,
                    )

                result.audios_exito += 1
                result.canciones_descargadas.append(f"{archivo_audio}.{formato_audio}")
                logger.info(f"Audio descargado: '{titulo_original}'")
            else:
                logger.info(f"Audio duplicado: '{titulo_original}'")
                result.canciones_descargadas.append(es_archivo_duplicado)
                result.audios_exito += 1

    except Exception as e:
        logger.error(f"Error al descargar audio: {e}")
        result.audios_error += 1


def _descargar_video_interno(config, url, result, base_folder=None):
    """
    Versión interna de descargar_video que usa objeto result.

    Args:
        config: Configuración del usuario
        url: URL del video
        result: Objeto DownloadResult para almacenar resultados
        base_folder: Carpeta base para descargas (opcional)
    """
    try:
        # Detectar si es una URL de Spotify y convertir a YouTube
        if "spotify.com" in url and "/track/" in url:
            logger.info(f"Detectada URL de Spotify, convirtiendo a YouTube: {url}")
            url_youtube = obtener_cancion_Spotify(config, url)
            if not url_youtube:
                logger.error(f"No se pudo convertir URL de Spotify a YouTube: {url}")
                result.videos_error += 1
                return
            logger.info(f"URL convertida: {url_youtube}")
            url = url_youtube

        # Selectores de formato con sintaxis válida de yt-dlp
        # Orden de prioridad: primero intenta formato específico, luego fallback
        calidad_map = {
            "min": "worstvideo+worstaudio/worst",  # Peor calidad (ahorra datos)
            "avg": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",  # Video hasta 720p (balanceado)
            "max": "bestvideo+bestaudio/best",  # Mejor calidad posible (4K/8K)
        }

        calidad_video = calidad_map.get(
            config.get("Calidad_audio_video", "avg"), calidad_map["avg"]
        )
        formato_video = config.get("Formato_video", "mp4")

        # Usar opciones base con cookies y headers
        info_opts = obtener_opciones_base_ytdlp()
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            titulo_original = info.get("title", "Unknown Title")
            uploader = info.get("uploader", "Unknown Uploader")
            titulo_limpio = limpiar_titulo(titulo_original)

            if base_folder:
                carpeta_video = base_folder
            else:
                carpeta_video = os.path.join(SCRIPT_DIR, "Descargas", "Video")

            crear_carpeta(carpeta_video)

            archivo_video = os.path.join(carpeta_video, titulo_limpio)
            archivo_video = os.path.normpath(archivo_video)

            es_archivo_duplicado = False
            # Solo verificar duplicados si NO estamos usando una carpeta temporal única
            if not base_folder:
                es_archivo_duplicado = archivo_duplicado(
                    "Descargas", "Video", f"{titulo_limpio}.{formato_video}"
                )

            if not es_archivo_duplicado:
                logger.info(f"Descargando video: '{titulo_original}'")
                logger.info(
                    f"Calidad seleccionada: {config.get('Calidad_audio_video', 'avg')}"
                )
                logger.info(f"Selector de formato: {calidad_video}")

                # Primero descargar en formato MKV (contenedor universal)
                # Luego convertir al formato deseado con FFmpeg
                archivo_temp = archivo_video + "_temp"

                # Construir lista de postprocesadores para SponsorBlock
                postprocessors = obtener_postprocessors_sponsorblock(config)

                # LIMPIEZA AGRESIVA: Eliminar cualquier archivo temporal existente
                # Esto es CRÍTICO para evitar que yt-dlp intente reanudar con una URL expirada (Error 403)
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
                            logger.info(
                                f"Limpieza previa: eliminado {os.path.basename(ruta)}"
                            )
                        except Exception as e:
                            logger.warning(
                                f"No se pudo eliminar archivo temporal {ruta}: {e}"
                            )

                # Usar opciones base con cookies y headers
                ydl_opts = obtener_opciones_base_ytdlp()
                ydl_opts.update(
                    {
                        "format": calidad_video,
                        "merge_output_format": "mkv",  # MKV acepta cualquier codec
                        "outtmpl": archivo_temp,
                        # Mostrar información del formato seleccionado para debugging
                        "quiet": True,
                        "no_warnings": True,
                    }
                )

                # Agregar postprocesadores si hay alguno
                if postprocessors:
                    ydl_opts["postprocessors"] = postprocessors

                # Agregar opciones adicionales de SponsorBlock si están habilitadas
                ydl_opts.update(obtener_opciones_sponsorblock(config))

                logger.info(f"Iniciando descarga con opciones: format={calidad_video}")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                # Archivo temporal descargado
                archivo_final = f"{archivo_video}.{formato_video}"

                # yt-dlp puede crear el archivo con o sin extensión .mkv
                # Buscar el archivo temporal de manera flexible
                archivo_temp_mkv = f"{archivo_temp}.mkv"
                if not os.path.exists(archivo_temp_mkv):
                    # Intentar sin extensión
                    if os.path.exists(archivo_temp):
                        archivo_temp_mkv = archivo_temp
                        logger.info(
                            f"Archivo temporal encontrado sin extensión: {archivo_temp}"
                        )
                    else:
                        logger.error(
                            f"Archivo temporal no encontrado después de la descarga"
                        )
                        logger.info("Buscando archivos en el directorio...")
                        # Buscar archivos con nombre similar en el directorio
                        directorio = os.path.dirname(archivo_temp)
                        base_name = os.path.basename(archivo_temp)
                        for archivo in os.listdir(directorio):
                            if base_name in archivo:
                                logger.info(f"Archivo encontrado: {archivo}")
                        result.videos_error += 1
                        return

                # Convertir al formato deseado usando FFmpeg
                conversion_exitosa = _convertir_video_ffmpeg(
                    archivo_temp_mkv, archivo_final, formato_video
                )

                # Eliminar archivos temporales (con y sin extensión)
                if os.path.exists(archivo_temp_mkv):
                    os.remove(archivo_temp_mkv)
                if os.path.exists(archivo_temp) and archivo_temp != archivo_temp_mkv:
                    os.remove(archivo_temp)

                if not conversion_exitosa:
                    logger.error(f"Error en conversión de video a {formato_video}")
                    result.videos_error += 1
                    return

                if config.get("Scrappear_metadata", False):
                    descargar_metadata(
                        archivo_final,
                        titulo_limpio,
                        uploader,
                    )

                result.videos_exito += 1
                result.canciones_descargadas.append(archivo_final)
                logger.info(f"Video descargado: '{titulo_original}'")
            else:
                logger.info(f"Video duplicado: '{titulo_original}'")
                result.canciones_descargadas.append(es_archivo_duplicado)
                result.videos_exito += 1

    except Exception as e:
        logger.error(f"Error al descargar video: {e}")
        result.videos_error += 1


# Mantener funciones originales para compatibilidad
# Variables globales para compatibilidad con código existente
audios_exito = 0
audios_error = 0
videos_exito = 0
videos_error = 0
canciones_descargadas = []


def _obtener_info_playlist_spotify(url):
    """
    Obtiene información de una playlist de Spotify.

    Args:
        url: URL de la playlist de Spotify

    Returns:
        dict: Información de la playlist con lista de tracks
    """
    global sp

    if not sp:
        logger.error("Cliente de Spotify no disponible")
        return None

    try:
        # Extraer playlist ID de la URL
        playlist_id = url.split("/playlist/")[1].split("?")[0].split("/")[0]
        logger.info(f"Obteniendo info de playlist de Spotify: {playlist_id}")

        # Obtener información de la playlist
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

        # Obtener todas las canciones (paginado)
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

                # Obtener artistas
                artists = track.get("artists", [])
                artist_name = ", ".join(
                    [a.get("name", "") for a in artists if a.get("name")]
                )

                # Duración
                duration_ms = track.get("duration_ms", 0)
                duration_seconds = duration_ms // 1000
                minutes = duration_seconds // 60
                seconds = duration_seconds % 60
                duration_str = f"{minutes}:{seconds:02d}"

                # Thumbnail del álbum
                album_images = track.get("album", {}).get("images", [])
                thumbnail = album_images[0].get("url", "") if album_images else ""

                # URL para búsqueda posterior en YouTube
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

            # Verificar si hay más páginas
            if not results.get("next"):
                break

            offset += limit

        playlist_info["total"] = len(playlist_info["items"])
        logger.info(
            f"Playlist de Spotify obtenida: '{playlist_info['titulo']}' con {playlist_info['total']} tracks"
        )

        return playlist_info

    except Exception as e:
        logger.error(f"Error obteniendo playlist de Spotify: {e}", exc_info=True)
        return None


def obtener_info_playlist(url):
    """
    Obtiene información completa de una playlist de YouTube, YouTube Music o Spotify sin descargar.
    Maneja playlists de cualquier tamaño sin límites.

    Para YouTube Music usa ytmusicapi (sin límite de items).
    Para YouTube regular usa yt-dlp sin extract_flat para evitar el límite de 99-100 items.
    Para Spotify usa spotipy para obtener todas las canciones.

    Args:
        url: URL de la playlist de YouTube, YouTube Music o Spotify

    Returns:
        dict: Información de la playlist con lista de videos/tracks
            {
                'titulo': str,
                'descripcion': str,
                'autor': str,
                'total': int,
                'items': [
                    {
                        'id': str,
                        'titulo': str,
                        'url': str,
                        'duracion': str,
                        'duracion_segundos': int,
                        'thumbnail': str,
                        'autor': str,
                    }
                ]
            }
    """
    global ytmusic, sp

    try:
        # Detectar si es Spotify
        if "spotify.com" in url and "/playlist/" in url:
            return _obtener_info_playlist_spotify(url)

        # Detectar si es URL de YouTube Music
        es_youtube_music = "music.youtube.com" in url

        # Extraer el ID de la playlist
        playlist_id = None
        if "list=" in url:
            import urllib.parse

            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            playlist_id = params.get("list", [None])[0]

        # Para YouTube Music, intentar usar ytmusicapi primero (sin límite de items)
        if es_youtube_music and ytmusic and playlist_id:
            try:
                logger.info(
                    f"Obteniendo playlist de YouTube Music usando ytmusicapi: {playlist_id}"
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

                        # Obtener duración
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

                        # Obtener artistas
                        artists = track.get("artists", [])
                        artist_name = (
                            ", ".join(
                                [a.get("name", "") for a in artists if a.get("name")]
                            )
                            if artists
                            else ""
                        )

                        # Obtener thumbnail
                        thumbnails = track.get("thumbnails", [])
                        thumbnail = thumbnails[-1].get("url", "") if thumbnails else ""
                        if not thumbnail and video_id:
                            thumbnail = (
                                f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
                            )

                        item = {
                            "id": video_id,
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
                        f"Playlist obtenida via ytmusicapi: '{playlist_info['titulo']}' con {playlist_info['total']} items"
                    )
                    return playlist_info

            except Exception as e:
                logger.warning(f"Error con ytmusicapi, usando yt-dlp: {e}")

        # Usar yt-dlp sin extract_flat para obtener todos los items
        # Esto es más lento pero no tiene el límite de 99-100 items
        url_normalizada = url
        if es_youtube_music:
            url_normalizada = url.replace("music.youtube.com", "www.youtube.com")
            logger.info(
                f"URL de YouTube Music detectada, convirtiendo a: {url_normalizada}"
            )

        # Usar opciones simplificadas para playlists
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",  # Extraer info básica de playlist sin descargar
            "skip_download": True,  # No descargar, solo extraer info
            "ignoreerrors": True,
            "playlistend": None,
            "extractor_retries": 3,
            "socket_timeout": 30,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(url_normalizada, download=False)

            if not result:
                return None

            # Verificar si es una playlist
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
            if entries is not None:
                entries_list = (
                    list(entries) if not isinstance(entries, list) else entries
                )
            else:
                entries_list = []

            logger.info(f"Procesando {len(entries_list)} entradas de la playlist")

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
                f"Playlist obtenida: '{playlist_info['titulo']}' con {playlist_info['total']} items"
            )
            return playlist_info

    except Exception as e:
        logger.error(f"Error al obtener información de playlist: {e}")
        return None


def obtener_info_media(url):
    """
    Obtiene información básica de un video/track individual (título y thumbnail).
    Funciona con YouTube, YouTube Music y Spotify.

    Args:
        url: URL del video o track

    Returns:
        dict: Información del media
            {
                'titulo': str,
                'thumbnail': str,
                'autor': str,
                'duracion': str,
                'fuente': str ('youtube', 'spotify', 'youtube_music')
            }
        None si hay error
    """
    if not url:
        return None

    try:
        url_lower = url.lower()

        # Detectar fuente
        if "spotify.com" in url_lower:
            # Es Spotify
            return _obtener_info_spotify(url)
        elif "music.youtube.com" in url_lower:
            # Es YouTube Music
            return _obtener_info_youtube(url, fuente="youtube_music")
        elif "youtube.com" in url_lower or "youtu.be" in url_lower:
            # Es YouTube
            return _obtener_info_youtube(url, fuente="youtube")
        else:
            # Intentar con yt-dlp como fallback
            return _obtener_info_youtube(url, fuente="youtube")

    except Exception as e:
        logger.error(f"Error al obtener info de media: {e}")
        return None


def _obtener_info_youtube(url, fuente="youtube"):
    """Obtiene info de YouTube/YouTube Music usando yt-dlp."""
    try:
        # Opciones simples solo para obtener información
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

            # Obtener duración formateada
            duracion_segundos = info.get("duration", 0) or 0
            if duracion_segundos:
                minutos = duracion_segundos // 60
                segundos = duracion_segundos % 60
                duracion = f"{minutos}:{segundos:02d}"
            else:
                duracion = "0:00"

            # Obtener mejor thumbnail
            thumbnails = info.get("thumbnails", [])
            thumbnail = ""
            if thumbnails:
                # Buscar thumbnail de buena calidad
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
                "fuente": fuente,
            }

    except Exception as e:
        logger.error(f"Error obteniendo info de YouTube: {e}")
        return None


def _obtener_info_spotify(url):
    """Obtiene info de Spotify usando spotipy."""
    global sp

    if not sp:
        logger.warning("Spotify no está configurado")
        return None

    try:
        # Extraer ID de la URL
        if "/track/" in url:
            # Extraer track ID
            track_id = url.split("/track/")[1].split("?")[0].split("/")[0]
            track = sp.track(track_id)

            if not track:
                return None

            # Obtener artistas
            artistas = [a["name"] for a in track.get("artists", [])]
            autor = ", ".join(artistas) if artistas else "Desconocido"

            # Obtener thumbnail del álbum
            images = track.get("album", {}).get("images", [])
            thumbnail = images[0]["url"] if images else ""

            # Duración
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
                "fuente": "spotify",
            }
        else:
            return None

    except Exception as e:
        logger.error(f"Error obteniendo info de Spotify: {e}")
        return None


def detectar_fuente_url(url):
    """
    Detecta la fuente de una URL (YouTube, Spotify, YouTube Music).

    Args:
        url: URL a analizar

    Returns:
        str: 'youtube', 'youtube_music', 'spotify' o None
    """
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


def es_url_valida(url):
    """
    Verifica si una URL es válida para descargar (YouTube o Spotify).

    Args:
        url: URL a verificar

    Returns:
        bool: True si es una URL válida
    """
    fuente = detectar_fuente_url(url)
    return fuente is not None


def es_url_playlist(url):
    """
    Verifica si una URL es una playlist de YouTube, YouTube Music o Spotify.

    Args:
        url: URL a verificar

    Returns:
        bool: True si es una URL de playlist
    """
    if not url:
        return False

    url_lower = url.lower()

    # Patrones de playlist de YouTube
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

    # Detectar playlists de Spotify
    if "spotify.com" in url_lower and "/playlist/" in url_lower:
        return True

    return False


def iniciar_descarga_selectiva(
    config,
    urls_seleccionadas,
    nombre_archivo_final="archivos.zip",
    progress_callback=None,
    base_folder=None,
):
    """
    Inicia descarga de URLs seleccionadas.

    Args:
        config: Diccionario con la configuración del usuario
        urls_seleccionadas: Lista de URLs a descargar
        nombre_archivo_final: Nombre del archivo ZIP resultante
        progress_callback: Función callback(percent, status, detail) para reportar progreso
        base_folder: Carpeta base para descargas (opcional)

    Returns:
        str: Ruta del archivo ZIP o del archivo individual
    """
    result = DownloadResult(progress_callback)

    # Crear las carpetas de descarga si no se provee base_folder
    if not base_folder:
        crear_carpeta(os.path.join(SCRIPT_DIR, "Descargas", "Audio"))
        crear_carpeta(os.path.join(SCRIPT_DIR, "Descargas", "Video"))
    else:
        crear_carpeta(base_folder)

    try:
        if progress_callback:
            progress_callback(5, "Preparando...", "Analizando solicitud")

        if not urls_seleccionadas:
            if progress_callback:
                progress_callback(100, "Sin selección", "No hay URLs seleccionadas")
            return None

        # Calcular total de items para el progreso
        total_downloads = 0
        if config.get("Descargar_video"):
            total_downloads += len(urls_seleccionadas)
        if config.get("Descargar_audio"):
            total_downloads += len(urls_seleccionadas)
        result.total_items = max(total_downloads, 1)

        if progress_callback:
            progress_callback(
                15,
                "Iniciando descargas...",
                f"{len(urls_seleccionadas)} elemento(s) seleccionado(s)",
            )

        inicio = time.time()

        # Procesar todas las descargas
        for i, url in enumerate(urls_seleccionadas):
            if not url:
                continue

            if config.get("Descargar_video"):
                if progress_callback:
                    percent = 15 + int(
                        (result.completed_items / result.total_items) * 70
                    )
                    progress_callback(
                        percent,
                        "Descargando video...",
                        f"Elemento {i+1} de {len(urls_seleccionadas)}",
                    )
                _descargar_video_interno(config, url, result, base_folder)
                result.completed_items += 1

            if config.get("Descargar_audio"):
                if progress_callback:
                    percent = 15 + int(
                        (result.completed_items / result.total_items) * 70
                    )
                    progress_callback(
                        percent,
                        "Descargando audio...",
                        f"Elemento {i+1} de {len(urls_seleccionadas)}",
                    )
                _descargar_audio_interno(config, url, result, base_folder)
                result.completed_items += 1

        fin = time.time()
        logger.info(f"Tiempo de ejecución: {fin - inicio:.2f} segundos")

        if progress_callback:
            progress_callback(90, "Finalizando...", "Procesando archivos descargados")

        # Determinar resultado final
        if len(result.canciones_descargadas) > 1:
            if progress_callback:
                progress_callback(92, "Comprimiendo...", "Creando archivo ZIP")
            ruta_descarga = comprimir_y_mover_archivos(
                nombre_archivo_final, result.canciones_descargadas
            )
        elif len(result.canciones_descargadas) == 1:
            ruta_descarga = os.path.abspath(result.canciones_descargadas[0])
        else:
            ruta_descarga = None

        if progress_callback:
            progress_callback(98, "¡Listo!", "Preparando descarga")

        # Log de resultados
        if result.audios_exito >= 1 and result.videos_exito >= 1:
            logger.info(
                f"Descargados {result.audios_exito} audios y {result.videos_exito} videos"
            )
        elif result.audios_exito >= 1:
            logger.info(f"Descargados {result.audios_exito} audios")
        elif result.videos_exito >= 1:
            logger.info(f"Descargados {result.videos_exito} videos")

        logger.info(f"Ruta(s) del archivo: {ruta_descarga}")
        return ruta_descarga

    except Exception as e:
        logger.error(f"Error en proceso de descarga selectiva: {e}")
        if progress_callback:
            progress_callback(100, "Error", str(e)[:100])
        return None


def descargar_audio(config, url):
    """Función de compatibilidad - usar _descargar_audio_interno en nuevo código."""
    global audios_exito, audios_error, canciones_descargadas
    result = DownloadResult()
    result.canciones_descargadas = canciones_descargadas
    _descargar_audio_interno(config, url, result)
    audios_exito = result.audios_exito
    audios_error = result.audios_error
    canciones_descargadas = result.canciones_descargadas
    return result.audios_exito > 0


def descargar_video(config, url):
    """Función de compatibilidad - usar _descargar_video_interno en nuevo código."""
    global videos_exito, videos_error, canciones_descargadas
    result = DownloadResult()
    result.canciones_descargadas = canciones_descargadas
    _descargar_video_interno(config, url, result)
    videos_exito = result.videos_exito
    videos_error = result.videos_error
    canciones_descargadas = result.canciones_descargadas
    return result.videos_exito > 0
