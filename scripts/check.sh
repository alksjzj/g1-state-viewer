#!/usr/bin/env bash
# Pure-data tests need only Python; optional DDS tests stay on lo/domain 197.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
bash -n run.sh
if command -v node >/dev/null 2>&1; then
    node --input-type=module --check < static/app.js
fi
if [[ "${1:-}" == "--docker" ]]; then
    # Launcher fixtures create executable fake commands in the temporary dir.
    docker run --rm --network none --read-only --tmpfs /tmp:rw,exec,nosuid,nodev --cap-drop ALL \
        --security-opt no-new-privileges -e G1_CHECK_ENVIRONMENT=1 \
        --entrypoint python3 "${G1_VIEWER_IMAGE:-g1-state-viewer:local}" \
        -B -m unittest discover -s tests -v
    docker run --rm --network host --read-only --tmpfs /tmp --cap-drop ALL \
        --security-opt no-new-privileges --entrypoint python3 \
        "${G1_VIEWER_IMAGE:-g1-state-viewer:local}" -B /app/tests/integration_dds.py
fi
