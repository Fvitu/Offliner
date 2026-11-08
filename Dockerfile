# A lightweight image of Python 3.11
FROM python:3.11-slim

# 1. Install system dependencies (ffmpeg for video processing, git for version control, curl & unzip for Deno)
RUN apt-get update && \
    apt-get install -y ffmpeg git curl unzip && \
    curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh && \
    rm -rf /var/lib/apt/lists/*

# 2. Set the working directory in the container
WORKDIR /app

# 3. Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy the application code into the container
COPY . .

# 5. Make the start script executable and fix Windows line endings
COPY start.sh .
RUN sed -i 's/\r$//' start.sh && chmod +x start.sh

# 6. Expose the port the app runs on (if applicable, e.g., for a web server)
CMD ["./start.sh"]