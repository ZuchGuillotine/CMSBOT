# CMSBOT Hub: review page, REST API and MCP endpoint. One container serves every project.
# The headless browser is only used to turn carousel slides into images.
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir ".[hub,s3]" playwright \
    && playwright install --with-deps chromium-headless-shell
ENV CMSBOT_HUB_DB=/data/hub.sqlite3
VOLUME /data
EXPOSE 8787
CMD ["cmsbot", "hub", "serve", "--host", "0.0.0.0", "--port", "8787"]
