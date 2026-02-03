"""
Aplicación principal de Music Downloader.
Una aplicación web para descargar música de YouTube y Spotify.
Sin almacenamiento de datos de usuario - respetando la privacidad.
"""

import os
import uuid
import json
import logging
import threading
import shutil
import yt_dlp
from logging.handlers import RotatingFileHandler

# Definir directorio base
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from flask import (
    Flask,
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
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import HTTPException

from config import config

# Modelos
from models.ModelFile import ModelFile, DEFAULT_CONFIG

# Almacenamiento de progreso de descargas (thread-safe)
download_progress = {}
progress_lock = threading.Lock()


def create_app(config_name="development"):
    """
    Factory function para crear la aplicación Flask.

    Args:
        config_name: Nombre de la configuración a usar ('development', 'production', 'testing')

    Returns:
        Flask: Instancia de la aplicación configurada
    """
    app = Flask(__name__)

    # Cargar configuración
    app.config.from_object(config[config_name])

    # Configurar logging
    setup_logging(app)

    # Limpieza inicial de temporales
    cleanup_temp_dirs(app)

    # Inicializar extensiones
    csrf = CSRFProtect(app)

    # Rate limiting para prevenir abuso
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=["200 per day", "50 per hour"],
        storage_uri="memory://",
    )

    # Registrar rutas
    register_routes(app, limiter)

    # Registrar manejadores de errores
    register_error_handlers(app)

    return app


def setup_logging(app):
    # Configura el sistema de logging de la aplicación.

    # Eliminar handlers duplicados de Flask si existen
    if app.logger.hasHandlers():
        app.logger.handlers.clear()

    # Crear directorio de logs si no existe
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Configurar formato de logs
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Handler para archivo con encoding UTF-8 para soportar emojis
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"),
        maxBytes=10240000,  # 10MB
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    # Handler para consola en desarrollo con encoding UTF-8
    if app.debug:
        import sys

        # Usar stdout con UTF-8 encoding para soportar emojis
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.DEBUG)
        # Configurar encoding si es posible
        if hasattr(console_handler.stream, "reconfigure"):
            console_handler.stream.reconfigure(encoding="utf-8", errors="replace")
        app.logger.addHandler(console_handler)

    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.setLevel(logging.INFO)
    app.logger.info("Aplicación Music Downloader iniciada")


def cleanup_temp_dirs(app):
    """Limpia el directorio de descargas temporales al inicio."""
    try:
        temp_base = os.path.join(BASE_DIR, "Descargas", "Temp")
        if os.path.exists(temp_base):
            shutil.rmtree(temp_base)
            app.logger.info(f"Limpieza inicial: {temp_base} eliminado.")

        # Recrear directorio vacío
        os.makedirs(temp_base, exist_ok=True)
    except Exception as e:
        app.logger.error(f"Error limpiando directorio temporal: {e}")


def register_routes(app, limiter):
    """Registra todas las rutas de la aplicación."""

    @app.route("/")
    def index():
        """Redirige al dashboard principal."""
        return redirect(url_for("dashboard"))

    @app.route("/dashboard")
    def dashboard():
        """Panel principal - la configuración se maneja en el cliente con localStorage."""
        # Pasamos la configuración por defecto al template
        # El JavaScript del cliente usará localStorage para persistir cambios
        return render_template("dashboard.html", config=DEFAULT_CONFIG)

    @app.route("/get_default_config")
    def get_default_config():
        """Retorna la configuración por defecto para inicializar localStorage."""
        return jsonify(DEFAULT_CONFIG)

    @app.route("/playlist_info", methods=["POST"])
    @limiter.limit("30 per minute")
    def playlist_info():
        """
        Obtiene información de una playlist de YouTube/YouTube Music/Spotify.
        Retorna JSON con lista de videos/tracks para selección.
        """
        try:
            url = request.form.get("url", "").strip()

            if not url:
                return (
                    jsonify({"error": "Por favor, ingresa una URL de playlist."}),
                    400,
                )

            from main import obtener_info_playlist, es_url_playlist

            # Verificar si es una URL de playlist válida
            if not es_url_playlist(url):
                return (
                    jsonify(
                        {
                            "error": "La URL no parece ser una playlist de YouTube, YouTube Music o Spotify.",
                            "es_playlist": False,
                        }
                    ),
                    400,
                )

            # Obtener información de la playlist
            info = obtener_info_playlist(url)

            if not info:
                return (
                    jsonify(
                        {
                            "error": "No se pudo obtener información de la playlist. Verifica la URL.",
                            "es_playlist": True,
                        }
                    ),
                    400,
                )

            if not info["items"]:
                return (
                    jsonify(
                        {
                            "error": "La playlist está vacía o no tiene videos accesibles.",
                            "es_playlist": True,
                        }
                    ),
                    400,
                )

            app.logger.info(
                f"Playlist info obtenida: '{info['titulo']}' ({info['total']} items)"
            )

            return jsonify({"success": True, "es_playlist": True, "playlist": info})

        except Exception as e:
            app.logger.error(f"Error obteniendo info de playlist: {e}")
            return jsonify({"error": "Error al procesar la playlist."}), 500

    @app.route("/verificar_playlist", methods=["POST"])
    @limiter.limit("60 per minute")
    def verificar_playlist():
        """
        Verifica si una URL es una playlist sin obtener toda la información.
        Útil para detectar rápidamente si mostrar UI de selección.
        """
        try:
            url = request.form.get("url", "").strip()

            if not url:
                return jsonify({"es_playlist": False})

            from main import es_url_playlist

            es_playlist = es_url_playlist(url)

            return jsonify({"es_playlist": es_playlist, "url": url})

        except Exception as e:
            app.logger.error(f"Error verificando playlist: {e}")
            return jsonify({"es_playlist": False})

    @app.route("/search", methods=["POST"])
    @limiter.limit("10 per minute")
    def search_youtube():
        """
        Realiza una búsqueda en YouTube y retorna los 3 primeros resultados.
        """
        try:
            query = request.form.get("query")
            prefer_ytmusic = request.form.get("prefer_ytmusic") == "true"

            if not query:
                return jsonify({"error": "No query provided"}), 400

            search_results = []

            # Si prefiere YouTube Music, usar ytmusicapi
            if prefer_ytmusic:
                app.logger.info(f"Searching YouTube Music for: {query}")
                from main import ytmusic

                if not ytmusic:
                    return jsonify({"error": "YouTube Music not available"}), 503

                try:
                    # Buscar canciones
                    results = ytmusic.search(query, filter="songs", limit=5)

                    if not results:
                        results = ytmusic.search(query, limit=5)

                    for entry in results[:5]:
                        # Extraer duración (string "3:45" a segundos y mantener string)
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

                        # Obtener mejor thumbnail
                        thumbnails = entry.get("thumbnails", [])
                        thumbnail_url = thumbnails[-1]["url"] if thumbnails else ""

                        # Artistas
                        artists = entry.get("artists", [])
                        artist_name = artists[0]["name"] if artists else "Unknown"

                        search_results.append(
                            {
                                "id": entry.get("videoId"),
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
                    # Fallback to standard YouTube search if YTM fails?
                    # User specifically asked for YTM, so maybe return error or empty.
                    # Let's just log and return what we have (empty).
                    pass

            else:
                # Búsqueda estándar en YouTube (yt-dlp)
                app.logger.info(f"Searching YouTube for: {query}")

                ydl_opts = {
                    "quiet": True,
                    "extract_flat": True,
                    "noplaylist": True,
                    "nocheckcertificate": True,
                }

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # ytsearch5: busca 5 resultados
                    result = ydl.extract_info(f"ytsearch5:{query}", download=False)

                    if "entries" not in result:
                        return jsonify({"error": "No results found"}), 404

                    for entry in result["entries"]:
                        # Formatear duración
                        duration = entry.get("duration", 0)
                        if isinstance(duration, (int, float)):
                            minutes = int(duration // 60)
                            seconds = int(duration % 60)
                            duration_str = f"{minutes}:{seconds:02d}"
                        else:
                            duration_str = str(duration)

                        search_results.append(
                            {
                                "id": entry.get("id"),
                                "titulo": entry.get("title"),
                                "url": entry.get("url")
                                or f"https://www.youtube.com/watch?v={entry.get('id')}",
                                "thumbnail": entry.get("thumbnail")
                                or f"https://i.ytimg.com/vi/{entry.get('id')}/hqdefault.jpg",
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
                    "es_playlist": True,  # Tratamos como playlist para reutilizar UI
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
    @limiter.limit("60 per minute")
    def media_info():
        """
        Obtiene información básica de un video/track individual.
        Retorna título, thumbnail, autor, duración y fuente detectada.
        """
        try:
            url = request.form.get("url", "").strip()

            if not url:
                return jsonify({"error": "URL vacía"}), 400

            from main import obtener_info_media, es_url_playlist, detectar_fuente_url

            # Verificar si es playlist (no procesar aquí)
            if es_url_playlist(url):
                return jsonify(
                    {"es_playlist": True, "fuente": detectar_fuente_url(url)}
                )

            # Obtener información del media
            info = obtener_info_media(url)

            if not info:
                return jsonify({"error": "No se pudo obtener información"}), 400

            return jsonify(
                {
                    "success": True,
                    "es_playlist": False,
                    "titulo": info.get("titulo", "Sin título"),
                    "thumbnail": info.get("thumbnail", ""),
                    "autor": info.get("autor", "Desconocido"),
                    "duracion": info.get("duracion", "0:00"),
                    "fuente": info.get("fuente", "youtube"),
                }
            )

        except Exception as e:
            app.logger.error(f"Error obteniendo info de media: {e}")
            return jsonify({"error": "Error al procesar la URL"}), 500

    @app.route("/descargar", methods=["POST"])
    @limiter.limit("10 per minute")
    def descargar():
        """Procesa la descarga de música."""
        temp_dir = None
        try:
            input_url = request.form.get("inputURL", "").strip()
            is_playlist_mode = request.form.get("is_playlist_mode", "false") == "true"
            selected_urls_json = request.form.get("selected_urls", "")
            config_json = request.form.get("user_config", "{}")

            # Validar entrada
            if not input_url and not is_playlist_mode:
                return (
                    jsonify(
                        {"error": "Por favor, ingresa una URL o nombre de canción."}
                    ),
                    400,
                )

            # Parsear URLs seleccionadas si es modo playlist
            selected_urls = []
            if is_playlist_mode and selected_urls_json:
                try:
                    selected_urls = json.loads(selected_urls_json)
                    if not selected_urls:
                        return (
                            jsonify(
                                {
                                    "error": "Selecciona al menos un elemento de la playlist."
                                }
                            ),
                            400,
                        )
                except json.JSONDecodeError:
                    return jsonify({"error": "Error en los datos de la playlist."}), 400

            # Generar ID único para esta descarga
            task_id = str(uuid.uuid4())

            # Inicializar progreso
            with progress_lock:
                download_progress[task_id] = {
                    "percent": 0,
                    "status": "Iniciando...",
                    "detail": "Preparando descarga",
                    "complete": False,
                    "error": None,
                }

            # Obtener configuración del cliente (enviada desde localStorage)
            try:
                user_config = json.loads(config_json)
                # Validar y sanitizar la configuración
                user_config = ModelFile.validate_config(user_config)
            except json.JSONDecodeError:
                user_config = DEFAULT_CONFIG.copy()

            nombre_archivo = f"descarga-{uuid.uuid4()}.zip"

            def progress_callback(percent, status, detail=""):
                with progress_lock:
                    if task_id in download_progress:
                        download_progress[task_id].update(
                            {"percent": percent, "status": status, "detail": detail}
                        )

            # Definir directorio del script y temporal único
            # SCRIPT_DIR ahora usa la constante global BASE_DIR
            temp_dir = os.path.join(BASE_DIR, "Descargas", "Temp", task_id)

            # Asegurar que el directorio temporal existe (y empezar limpio)
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            os.makedirs(temp_dir, exist_ok=True)

            if is_playlist_mode and selected_urls:
                # Modo playlist: descargar URLs seleccionadas
                from main import iniciar_descarga_selectiva

                archivo_a_descargar = iniciar_descarga_selectiva(
                    user_config,
                    selected_urls,
                    nombre_archivo,
                    progress_callback,
                    base_folder=temp_dir,
                )
            else:
                # Modo normal: usar la función existente
                from main import iniciar_con_progreso

                archivo_a_descargar = iniciar_con_progreso(
                    user_config,
                    input_url,
                    nombre_archivo,
                    progress_callback,
                    base_folder=temp_dir,
                )

            # Marcar como completado
            with progress_lock:
                if task_id in download_progress:
                    download_progress[task_id]["complete"] = True
                    download_progress[task_id]["percent"] = 100

            if not archivo_a_descargar or not os.path.exists(archivo_a_descargar):
                # Limpiar directorio temporal si falló
                if os.path.exists(temp_dir):
                    try:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                    except Exception:
                        pass
                with progress_lock:
                    if task_id in download_progress:
                        download_progress[task_id]["error"] = "No se pudo descargar"
                return jsonify({"error": "No se pudo descargar el archivo."}), 500

            nombre_archivo_final = os.path.basename(archivo_a_descargar)

            # Sanitizar nombre para logging (remover emojis problemáticos)
            nombre_log = nombre_archivo_final.encode("ascii", "replace").decode("ascii")
            app.logger.info(f"Descarga completada: {nombre_log}")

            # Limpiar progreso después de un tiempo
            def cleanup_progress():
                import time

                time.sleep(60)  # Mantener por 60 segundos
                with progress_lock:
                    download_progress.pop(task_id, None)

            threading.Thread(target=cleanup_progress, daemon=True).start()

            # Enviar archivo y luego eliminarlo del servidor
            response = send_file(
                archivo_a_descargar,
                as_attachment=True,
                download_name=nombre_archivo_final,
            )

            # Eliminar el directorio temporal completo después de enviar
            @response.call_on_close
            def cleanup_file():
                try:
                    if os.path.exists(temp_dir):
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        app.logger.info(f"Directorio temporal eliminado: {temp_dir}")
                except Exception as e:
                    app.logger.error(f"Error al eliminar directorio temporal: {e}")

            return response

        except Exception as e:
            app.logger.error(f"Error en descarga: {e}")
            # Limpieza en caso de error inesperado
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    app.logger.info(
                        f"Directorio temporal limpiado tras error: {temp_dir}"
                    )
                except Exception as cleanup_error:
                    app.logger.error(f"Error limpiando tras fallo: {cleanup_error}")

            return jsonify({"error": "Ha ocurrido un error durante la descarga."}), 500

    @app.route("/progress/<task_id>")
    def get_progress(task_id):
        """Endpoint SSE para obtener progreso de descarga en tiempo real."""

        def generate():
            import time

            while True:
                with progress_lock:
                    progress = download_progress.get(
                        task_id,
                        {
                            "percent": 0,
                            1 < "status": "Esperando...",
                            "detail": "",
                            "complete": False,
                            "error": None,
                        },
                    )

                # Enviar evento SSE
                data = json.dumps(progress)
                yield f"data: {data}\n\n"

                # Si está completo o hay error, terminar
                if progress.get("complete") or progress.get("error"):
                    break

                time.sleep(0.5)  # Actualizar cada 500ms

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )


def register_error_handlers(app):
    """Registra los manejadores de errores HTTP."""

    @app.errorhandler(404)
    def not_found(error):
        """Maneja páginas no encontradas."""
        return render_template("error.html"), 404

    @app.errorhandler(429)
    def ratelimit_handler(error):
        """Maneja límite de peticiones excedido."""
        flash("Demasiadas solicitudes. Por favor, espera un momento.")
        return redirect(url_for("dashboard"))

    @app.errorhandler(500)
    def internal_error(error):
        """Maneja errores internos del servidor."""
        app.logger.error(f"Error interno: {error}")
        return render_template("error.html"), 500

    @app.errorhandler(Exception)
    def handle_exception(e):
        """Maneja excepciones no controladas."""
        if isinstance(e, HTTPException):
            return e
        app.logger.error(f"Excepción no manejada: {e}")
        return render_template("error.html"), 500


# Crear instancia de la aplicación
app = create_app(os.getenv("FLASK_ENV", "development"))

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
