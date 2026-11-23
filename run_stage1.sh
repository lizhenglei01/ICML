#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
parameter_file="${1:-configs/obm036.json}"
artifact_dir="${2:-artifacts}"
python -m cfbench.smoke --params "$parameter_file" --out "$artifact_dir"
CFBENCH_ARTIFACT_ROOT="$artifact_dir" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests 2>&1 | tee "$artifact_dir/logs/pytest.log"
