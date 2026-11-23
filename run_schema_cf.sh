#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
schema_cf_output="${1:-schema_cf/artifacts}"
mkdir -p -- "$schema_cf_output/logs"
python -m schema_cf.demo --out "$schema_cf_output" 2>&1 | tee "$schema_cf_output/logs/build.log"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q schema_cf/test_schema_cf.py medical_cf/test_medical.py --junitxml="$schema_cf_output/logs/pytest.xml" 2>&1 | tee "$schema_cf_output/logs/pytest.log"
