#!/bin/bash

set -e

# 1. Start the RQ Worker in the background (&)
echo "🚀 Starting Worker..."
python rq_worker.py "${REDIS_URL:-redis://localhost:6379/0}" &

# 2. Start the web server (e.g., using Gunicorn for Flask or FastAPI)
echo "🌐 Starting Web Server..."

exec gunicorn app:app \
	--bind 0.0.0.0:${PORT:-5000} \
	--worker-class gthread \
	--workers ${WEB_CONCURRENCY:-1} \
	--threads ${GUNICORN_THREADS:-4} \
	--timeout ${GUNICORN_TIMEOUT:-0}