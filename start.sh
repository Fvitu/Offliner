#!/bin/bash

# 1. Start the RQ Worker in the background (&)
echo "🚀 Starting Worker..."
python -m rq worker &

# 2. Start the web server (e.g., using Gunicorn for Flask or FastAPI)
echo "🌐 Starting Web Server..."

gunicorn app:app --bind 0.0.0.0:$PORT