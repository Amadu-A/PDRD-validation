#!/usr/bin/env bash
# scripts/create-superuser.sh
# Интерактивно создаёт первого локального администратора через контейнер Auth.
# Пароль читается Python из TTY и не попадает в shell history или docker args.

set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${REPO_DIR}"
if [[ ! -t 0 || ! -t 2 ]]; then
    printf 'Нужен интерактивный терминал.\n' >&2
    exit 1
fi
exec docker compose --profile identity --profile auth exec auth-service \
    python -m pdrd_auth_service.create_superuser "$@"
