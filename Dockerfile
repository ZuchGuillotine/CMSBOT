# CMSBOT Hub: review page, REST API and MCP endpoint. One container serves every project.
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[hub]"
ENV CMSBOT_HUB_DB=/data/hub.sqlite3
VOLUME /data
EXPOSE 8787
CMD ["cmsbot", "hub", "serve", "--host", "0.0.0.0", "--port", "8787"]
