# ops/deploy-experience-catalog.sh
# Приёмка этапа 7: quality gate, отдельный PostgreSQL, миграция и серверный UI 8080.

set -euo pipefail
exec bash "$(dirname "${BASH_SOURCE[0]}")/deploy-review-services.sh" "Этап 7 Experience"
