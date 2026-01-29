# 🎵 Music Downloader - Aplicación Web

Una aplicación web moderna para descargar audio y video de YouTube y Spotify con una interfaz amigable y múltiples opciones de configuración.

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-3.0-green.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)
![Privacy](https://img.shields.io/badge/Privacy-First-green.svg)

## 🔒 Privacidad

**Esta aplicación está diseñada con la privacidad del usuario como prioridad:**

- ❌ **Sin base de datos** - No almacenamos ninguna información tuya
- ❌ **Sin registro de usuarios** - No necesitas crear cuenta
- ❌ **Sin cookies de rastreo** - Solo cookies técnicas necesarias (CSRF)
- ✅ **Configuración local** - Tu configuración se guarda en tu navegador (localStorage)
- ✅ **Sin logs de usuario** - No registramos qué descargas

## ✨ Características

- 🎬 **Descarga videos** de YouTube en múltiples calidades y formatos
- 🎵 **Descarga audio** de YouTube con conversión automática a MP3, WAV, M4A o FLAC
- 🎧 **Integración con Spotify** - Busca automáticamente canciones de Spotify en YouTube
- 📋 **Soporte para playlists** de YouTube y Spotify
- 🏷️ **Metadata automática** - Añade portadas, artistas, álbum y año de lanzamiento
- ⚙️ **Configuración personalizada** guardada en tu navegador
- 🎯 **SponsorBlock** - Elimina automáticamente sponsors, intros y outros
- 🎵 **YouTube Music** - Preferencia de audio puro de YouTube Music
- 🔒 **Seguridad** - Protección CSRF, rate limiting

## 🚀 Instalación

### Requisitos previos

- Python 3.8 o superior
- FFmpeg (para conversión de audio)

### Pasos de instalación

1. **Clonar el repositorio**

```bash
git clone https://github.com/tu-usuario/music-downloader.git
cd music-downloader
```

2. **Crear entorno virtual**

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate
```

3. **Instalar dependencias**

```bash
pip install -r requirements.txt
```

4. **Configurar variables de entorno** (opcional)

```bash
# Copiar archivo de ejemplo
cp .env.example .env
# Editar con tus credenciales
```

5. **Ejecutar la aplicación**

```bash
python app.py
```

La aplicación estará disponible en `http://localhost:5000`

## ⚙️ Configuración

### Variables de entorno (.env) - Opcional

```env
# Flask
SECRET_KEY=tu-clave-secreta-muy-segura
FLASK_ENV=development

# Spotify (opcional, para metadata)
SPOTIFY_CLIENT_ID=tu_client_id
SPOTIFY_CLIENT_SECRET=tu_client_secret
```

### Opciones de configuración del usuario

La configuración se guarda automáticamente en el localStorage de tu navegador:

| Opción              | Descripción                   | Valores             |
| ------------------- | ----------------------------- | ------------------- |
| Calidad Audio/Video | Calidad de descarga           | min, avg, max       |
| Formato Audio       | Formato de salida de audio    | mp3, m4a, flac, wav |
| Formato Video       | Formato de salida de video    | mp4, mov, avi, flv  |
| Descargar Audio     | Extraer solo audio            | true/false          |
| Descargar Video     | Descargar video completo      | true/false          |
| Metadata            | Añadir información automática | true/false          |
| YouTube Music       | Preferir versión de YT Music  | true/false          |
| SponsorBlock        | Eliminar sponsors/intros      | true/false          |

## 🎯 Uso

1. Abre la aplicación en tu navegador
2. (Opcional) Configura tus preferencias de descarga
3. Pega una URL de YouTube/Spotify o escribe el nombre de la canción
4. Si es una playlist, selecciona los elementos a descargar
5. ¡Descarga y disfruta!

## 🛠️ Tecnologías

- **Backend:** Flask, Python 3.8+
- **Frontend:** Bootstrap 5, JavaScript
- **Descarga:** yt-dlp, youtube-search-python
- **Metadata:** Mutagen, Spotipy
- **Almacenamiento:** localStorage (solo en navegador del usuario)

## 📁 Estructura del proyecto

```
music-downloader/
├── app.py              # Aplicación Flask principal
├── main.py             # Lógica de descarga de música
├── config.py           # Configuración de la aplicación
├── requirements.txt    # Dependencias Python
├── models/
│   └── ModelFile.py    # Modelo de configuración
├── static/
│   ├── css/
│   └── img/
├── templates/
│   ├── dashboard.html  # Página principal
│   ├── layout.html     # Template base
│   └── error.html      # Página de error
└── logs/               # Logs de la aplicación
```

## 🤝 Contribuir

Las contribuciones son bienvenidas. Por favor:

1. Fork el repositorio
2. Crea una rama para tu feature (`git checkout -b feature/nueva-caracteristica`)
3. Commit tus cambios (`git commit -am 'Añade nueva característica'`)
4. Push a la rama (`git push origin feature/nueva-caracteristica`)
5. Abre un Pull Request

## 📄 Licencia

Este proyecto está bajo la Licencia MIT. Ver el archivo `LICENSE` para más detalles.

## ⚠️ Aviso Legal

Esta herramienta es para uso personal y educativo. Asegúrate de respetar los derechos de autor y los términos de servicio de las plataformas.

---

Creado con ❤️ por Fede Vitu
