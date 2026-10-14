#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"
PYTHON_BIN=${PYTHON:-python3}
mkdir -p dist
"$PYTHON_BIN" scripts/release.py prepare-build --package-dir .
# Build from a clean source copy so ignored/stale ``build/lib`` contents cannot
# leak removed modules into the release wheel.
BUILD_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/expra-connect-release.XXXXXX")
trap 'rm -rf "$BUILD_ROOT"' EXIT
mkdir -p "$BUILD_ROOT/source"
cp pyproject.toml "$BUILD_ROOT/source/"
cp -a src "$BUILD_ROOT/source/"
"$PYTHON_BIN" -m pip wheel --no-deps --wheel-dir "$ROOT_DIR/dist" "$BUILD_ROOT/source"
wheel=$("$PYTHON_BIN" scripts/release.py newest-wheel --package-dir .)
"$PYTHON_BIN" scripts/verify_wheel.py "$wheel"
"$PYTHON_BIN" scripts/release.py sync-artifacts --package-dir .
printf 'Built %s\n' "$wheel"
cat dist/SHA256SUMS
