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
mkdir -p "$BUILD_ROOT/source" "$BUILD_ROOT/artifacts"
cp pyproject.toml setup.py "$BUILD_ROOT/source/"
cp -a src "$BUILD_ROOT/source/"
cp -a "$BUILD_ROOT/source" "$BUILD_ROOT/native-source"

# Build from a clean source tree so ignored/stale build outputs cannot leak into
# the Cython wheel.
EXPRA_CONNECT_CYTHON=1 "$PYTHON_BIN" -m pip wheel --no-deps \
  --wheel-dir "$BUILD_ROOT/artifacts" "$BUILD_ROOT/native-source"

for wheel in "$BUILD_ROOT"/artifacts/*.whl; do
  "$PYTHON_BIN" scripts/verify_wheel.py "$wheel" \
    --source-package "$BUILD_ROOT/source/src/expra_connect"
done
"$PYTHON_BIN" scripts/release.py sync-artifacts \
  --package-dir . --artifact-dir "$BUILD_ROOT/artifacts"
printf 'Built release artifacts:\n'
ls -1 "$BUILD_ROOT"/artifacts/*.whl
cat dist/SHA256SUMS
