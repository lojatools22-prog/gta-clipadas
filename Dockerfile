FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV DATA_DIR=/data PORT=8080
CMD ["sh","-c","pip install -U --no-cache-dir 'yt-dlp[default]' >/dev/null 2>&1; uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
