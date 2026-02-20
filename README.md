# 🎵 Offliner — Privacy-First Media Archiver

Offliner is a robust media archiver built with Python and Flask for people who want reliable offline access to music and videos. It supports YouTube and Spotify links, downloads media through `yt-dlp`, processes files with `FFmpeg`, and applies metadata/cover embedding through the yt-dlp post-processing pipeline (which may use Mutagen internally depending on format).

If you are tired of buffering or losing access to your favorite tracks, Offliner is designed to help preserve authorized media locally for personal offline playback.

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-3.0-green.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)
![Privacy](https://img.shields.io/badge/Privacy-First-green.svg)

## 🚀 What this project does

- Downloads audio/video from YouTube with selectable quality and output format.
- Resolves Spotify tracks/playlists and maps them to downloadable sources.
- Supports playlists (YouTube, YouTube Music, Spotify) and item selection.
- Uses SponsorBlock integration to skip non-music segments (optional).
- Embeds metadata and cover art through yt-dlp + FFmpeg postprocessors.
- Provides live progress updates through Redis + RQ background jobs.
- Includes a dark, modern web UI with per-user settings saved locally.

## 🗂️ Preservation-first approach

Offliner is built with an archival mindset: the goal is to help users preserve media locally for uninterrupted offline listening/viewing.

**Important:** before using this tool, users must obtain permission for both:

1. Access/use of the content.
2. Download/storage of the content.

Using content without proper authorization may violate copyright law and platform Terms of Service.

## ⚖️ Copyright and responsibility

**Disclaimer:** We are not responsible for the content downloaded using this tool. Users must verify that the content is free of copyright restrictions and authorized for download prior to initiating any transfer. Copyrighted content is not available for download with this tool.

By choosing to download, you acknowledge that the accessed content is for personal, non-commercial use only. You agree not to distribute, copy, modify, or otherwise use the downloaded content for commercial purposes, including but not limited to resale, public performance, or broadcasting. Any use beyond this scope may violate applicable copyright laws and Terms of Service. We assume no liability for unauthorized or improper use; the user assumes full responsibility for compliance with all relevant laws and contractual obligations.

## 🔒 Privacy

Offliner is privacy-first by design:

- No user accounts.
- No database for user profiles/history.
- No analytics/tracking cookies.
- UI settings are stored in your own browser (`localStorage`).
- Downloads are processed in temporary local folders and packaged locally.

The server includes technical protections (CSRF, rate limiting, temporary in-memory limits) to prevent abuse, not to build user profiles.

## 🧱 Tech stack

- **Backend:** Flask, Python
- **Download engine:** yt-dlp
- **Media processing:** FFmpeg
- **Queue & progress:** Redis + RQ
- **Integrations:** Spotipy, YTMusic API, SponsorBlock API
- **Frontend:** Bootstrap 5 + vanilla JavaScript

## ✅ Prerequisites

Before running Offliner, ensure:

- Python `3.8+`
- `FFmpeg` installed and available in your system `PATH`
- Redis available (details below)

## ⚙️ Installation and run guide (with Redis)

### 1) Clone and enter the project

```bash
git clone https://github.com/Fvitu/Offliner
cd Offliner
```

### 2) Create and activate a virtual environment

```bash
python -m venv .venv
```

**Windows (PowerShell):**

```powershell
.venv\Scripts\Activate.ps1
```

**Linux/macOS:**

```bash
source .venv/bin/activate
```

### 3) Install dependencies

```bash
pip install -r requirements.txt
```

### 4) Create your `.env` file (optional but recommended)

Create a `.env` in the project root. Use the example in the next section.

### 5) Run the app

```bash
python app.py
```

The app starts at: `http://localhost:5000`

---

### Redis notes (important)

Offliner uses Redis + RQ for background downloads and real-time progress updates.

When starting `python app.py`, Offliner attempts to:

1. Connect to Redis (`REDIS_URL`, default `redis://localhost:6379/0`).
2. Auto-start a local `redis-server` binary if available.
3. Start the RQ worker process automatically.

If Redis cannot be found/launched, downloads will fail even if the web UI loads.

#### Common Windows fix

- Place `redis-server.exe` in the project root **or** install Redis and add it to `PATH`.
- Then re-run:

```bash
python app.py
```

#### Manual fallback (any OS)

If needed, start Redis yourself and then run Offliner:

```bash
redis-server --port 6379
python app.py
```

#### Verify Redis URL

Make sure `.env` matches your Redis instance:

```env
REDIS_URL=redis://localhost:6379/0
```

## 🧪 Example `.env`

```env
# Flask app mode: development | production | testing
FLASK_ENV=development

# Flask secret for CSRF/session signing (change in production)
SECRET_KEY=change-this-to-a-long-random-string

# App port (defaults to 5000 if omitted)
PORT=5000

# Redis connection used by queue + progress store
REDIS_URL=redis://localhost:6379/0

# Optional Spotify API credentials (needed for reliable Spotify resolution)
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=

# Global request limits (anti-abuse)
RATE_LIMIT_PER_DAY=200
RATE_LIMIT_PER_HOUR=50

# Endpoint-level rate limits
RATE_LIMIT_SEARCH=10 per minute
RATE_LIMIT_PLAYLIST=30 per minute
RATE_LIMIT_MEDIA_INFO=60 per minute
RATE_LIMIT_DOWNLOAD=10 per minute

# Per-user download caps
MAX_DOWNLOADS_PER_HOUR=10
MAX_DOWNLOADS_PER_DAY=50

# Allowed total media duration (minutes)
MAX_DURATION_PER_HOUR=120
MAX_DURATION_PER_DAY=600

# Max duration for one media item (minutes)
MAX_CONTENT_DURATION=60

# Max items allowed per playlist request
MAX_PLAYLIST_ITEMS=100
```

## 🖥️ AI Disclosure

I used AI (LLMs) as a "pair programmer" to speed up development, specifically for:
- **Frontend Boilerplate:** Generating the initial HTML/Bootstrap structure and CSS for the responsive cards and dark mode.
- **Regex & Parsing:** Helping with the complex patterns needed to validate URLs and sanitize filenames.
- **Library Documentation:** Quickly finding the correct methods for mutagen (ID3 tags) and yt-dlp configuration without digging through pages of docs.
- **Debugging:** Troubleshooting FFmpeg conversion errors and threading issues in the download queue.
- **Documentation:** Structuring and generating the text for this README.md file.
- **Logic:** The core architecture, the integration of SponsorBlock, and the privacy-focused logic (no database) were designed and implemented by me.

## 🖥️ Usage

1. Open the dashboard in your browser.
2. (Optional) adjust your download settings.
3. Paste a YouTube/Spotify URL or type a song query.
4. Select tracks if it is a playlist.
5. Start the download and wait for the ZIP package.

## 🤝 Contributing

Contributions are welcome and appreciated.

1. Fork the repository.
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit your changes: `git commit -m "Add your feature"`
4. Push your branch: `git push origin feature/your-feature`
5. Open a Pull Request.

If you want to contribute bug fixes, UX improvements, or documentation updates, feel free to open an issue first to discuss the proposal.

## 📄 License

This project is licensed under the MIT License.

---

Made with ❤️ by Fede Vitu
