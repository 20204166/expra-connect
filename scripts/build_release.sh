#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"
PYTHON_BIN=${PYTHON:-python3}
mkdir -p dist
"$PYTHON_BIN" scripts/release.py prepare-build --package-dir .
# Use pip's isolated PEP 517 builder; the repository has a ``build/`` data
# directory, so ``python -m build`` would resolve the wrong local package.
"$PYTHON_BIN" -m pip wheel --no-deps --wheel-dir dist .
wheel=$("$PYTHON_BIN" scripts/release.py newest-wheel --package-dir .)
"$PYTHON_BIN" scripts/verify_wheel.py "$wheel"
"$PYTHON_BIN" scripts/release.py sync-artifacts --package-dir .
printf 'Built %s\n' "$wheel"
cat dist/SHA256SUMS
