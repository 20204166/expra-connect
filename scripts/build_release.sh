#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"
mkdir -p dist
python scripts/release.py prepare-build --package-dir .
python -m build --wheel --outdir dist
wheel=$(python scripts/release.py newest-wheel --package-dir .)
python scripts/verify_wheel.py "$wheel"
python scripts/release.py sync-artifacts --package-dir .
printf 'Built %s\n' "$wheel"
cat dist/SHA256SUMS
