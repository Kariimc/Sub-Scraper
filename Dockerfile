FROM python:3.11-slim

WORKDIR /app

# System deps: ffmpeg for audio conversion, git for spotdl
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer-caches until requirements change)
COPY requirements.txt requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-web.txt

# Copy source
COPY . .

EXPOSE 8080

CMD ["python", "web_run.py"]
