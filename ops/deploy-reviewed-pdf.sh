# ops/deploy-reviewed-pdf.sh
# Совместимая команда этапа 6; использует общий проверенный путь развёртывания.

set -euo pipefail
exec bash "$(dirname "${BASH_SOURCE[0]}")/deploy-review-services.sh" "Reviewed PDF"
