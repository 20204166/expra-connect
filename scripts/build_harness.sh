#!/usr/bin/env bash
set -euo pipefail

# Build a single-file, downloadable peer harness. The archive contains only the
# dev harness package and imports the installed ``expra_connect`` wheel, so a
# machine that cannot install packages can run it after downloading one file.
ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"
PYTHON_BIN=${PYTHON:-python3}
mkdir -p dist
"$PYTHON_BIN" -m zipapp tools/peer_harness/src \
  -m "peer_harness.cli:main" \
  -o dist/peer_harness.pyz \
  -p "/usr/bin/env python3"
"$PYTHON_BIN" - <<'PY'
import hashlib
from pathlib import Path

path = Path("dist/peer_harness.pyz")
print(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}")
PY
printf 'Built %s\n' dist/peer_harness.pyz
