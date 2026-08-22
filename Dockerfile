# Build the UI, then serve it and the API from one Python image. One container, one port,
# no CORS, no reverse proxy to configure — the demo is `docker compose up`.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
# git is needed at run time: ingesting a repository shallow-clones it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 oracle
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "uvicorn[standard]>=0.30"

COPY repo_oracle/ ./repo_oracle/
COPY evals/ ./evals/
COPY --from=web /web/dist ./web/dist

# Indexes live here. Mount a volume over it to keep them across restarts.
RUN mkdir -p /app/data && chown oracle /app/data
USER oracle
ENV ORACLE_DATA_DIR=/app/data
EXPOSE 8000

# ALLOWED_REPO_ROOTS is deliberately unset: inside a container no host path is worth
# exposing, so local-path ingestion is refused and only git URLs work.
CMD ["uvicorn", "repo_oracle.app:app", "--host", "0.0.0.0", "--port", "8000"]
