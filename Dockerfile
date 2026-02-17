# A lightweight image of Python 3.10
FROM python:3.10-slim

# 1. Install system dependencies (ffmpeg for video processing, git for version control)
RUN apt-get update && \
    apt-get install -y ffmpeg git && \
    rm -rf /var/lib/apt/lists/*

# 2. Set the working directory in the container
WORKDIR /app

# 3. Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy the application code into the container
COPY . .

# 5. Make the start script executable
COPY start.sh .
RUN chmod +x start.sh

# 6. Expose the port the app runs on (if applicable, e.g., for a web server)
CMD ["./start.sh"]