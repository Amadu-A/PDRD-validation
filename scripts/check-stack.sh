#!/usr/bin/env bash
set -euo pipefail
docker version --format 'Docker server: {{.Server.Version}}'
docker compose version
docker compose ps
curl -fsS -o /dev/null -w 'Frontend HTTP %{http_code}\n' http://localhost:${FRONTEND_PORT:-8080}/
curl -fsS -o /dev/null -w 'n8n HTTP %{http_code}\n' http://localhost:${N8N_PORT:-5678}/
curl -fsS http://localhost:${QDRANT_HTTP_PORT:-6333}/readyz && echo
curl -fsS http://localhost:${OLLAMA_PORT:-11434}/api/tags && echo
