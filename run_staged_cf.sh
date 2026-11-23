#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
staged_cf_output="${1:-staged_cf/artifacts}"
mkdir -p -- "$staged_cf_output/logs"
python -m staged_cf.run --out "$staged_cf_output" --medical-check 2>&1 | tee "$staged_cf_output/logs/latest_build.log"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q staged_cf/test_staged.py schema_cf/test_schema_cf.py medical_cf/test_medical.py --junitxml="$staged_cf_output/logs/pytest.xml" 2>&1 | tee "$staged_cf_output/logs/pytest.log"
