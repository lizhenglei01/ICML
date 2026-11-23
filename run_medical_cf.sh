#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
medical_cf_config="${1:-medical_cf/config.json}"
medical_cf_output="${2:-medical_cf/artifacts}"
mkdir -p -- "$medical_cf_output/logs"
python -m medical_cf.run --config "$medical_cf_config" --out "$medical_cf_output" 2>&1 | tee "$medical_cf_output/logs/build.log"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q medical_cf/test_medical.py --junitxml="$medical_cf_output/logs/pytest.xml" 2>&1 | tee "$medical_cf_output/logs/pytest.log"
