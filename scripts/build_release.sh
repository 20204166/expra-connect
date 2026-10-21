#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"
PYTHON_BIN=${PYTHON:-python3}

BUILD_UNIVERSAL=0
BUILD_PERFORMANCE=1
BUILD_BUMP=
PYPY_PYTHON_BIN=
while (($#)); do
  case "$1" in
    --universal)
      if [[ "$BUILD_UNIVERSAL" -eq 1 ]]; then
        printf 'error: --universal may only be specified once\n' >&2
        exit 2
      fi
      BUILD_UNIVERSAL=1
      ;;
    --skip-performance-benchmarks)
      if [[ "$BUILD_PERFORMANCE" -eq 0 ]]; then
        printf 'error: --skip-performance-benchmarks may only be specified once\n' >&2
        exit 2
      fi
      BUILD_PERFORMANCE=0
      ;;
    --pypy-python)
      if [[ -n "$PYPY_PYTHON_BIN" || $# -lt 2 || -z "${2:-}" ]]; then
        printf 'error: --pypy-python requires one value and may only be specified once\n' >&2
        exit 2
      fi
      PYPY_PYTHON_BIN=$2
      shift
      ;;
    --bump)
      if [[ -n "$BUILD_BUMP" || $# -lt 2 || -z "${2:-}" ]]; then
        printf 'error: --bump requires one value and may only be specified once\n' >&2
        exit 2
      fi
      BUILD_BUMP=$2
      shift
      ;;
    --bump=*)
      if [[ -n "$BUILD_BUMP" || -z "${1#--bump=}" ]]; then
        printf 'error: --bump must be non-empty and may only be specified once\n' >&2
        exit 2
      fi
      BUILD_BUMP=${1#--bump=}
      ;;
    *)
      printf 'usage: %s [--universal] [--skip-performance-benchmarks] [--pypy-python PATH] [--bump auto|none|patch|feature|minor]\n' "$0" >&2
      exit 2
      ;;
  esac
  shift
done

if [[ -n "$PYPY_PYTHON_BIN" && "$BUILD_UNIVERSAL" -eq 0 ]]; then
  printf 'error: --pypy-python requires --universal\n' >&2
  exit 2
fi

mkdir -p dist
PREPARE_ARGS=(prepare-build --package-dir .)
if [[ -n "$BUILD_BUMP" ]]; then
  case "$BUILD_BUMP" in
    auto|none|patch|feature|minor) ;;
    *)
      printf 'error: invalid bump value: %s\n' "$BUILD_BUMP" >&2
      exit 2
      ;;
  esac
  PREPARE_ARGS+=(--bump "$BUILD_BUMP")
fi
"$PYTHON_BIN" scripts/release.py "${PREPARE_ARGS[@]}"
# Build from a clean source copy so ignored/stale ``build/lib`` contents cannot
# leak removed modules into the release wheel.
BUILD_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/expra-connect-release.XXXXXX")
trap 'rm -rf "$BUILD_ROOT"' EXIT
mkdir -p "$BUILD_ROOT/source" "$BUILD_ROOT/artifacts"
cp pyproject.toml setup.py "$BUILD_ROOT/source/"
cp -a src "$BUILD_ROOT/source/"

BUILD_NATIVE=1
if [[ "$BUILD_UNIVERSAL" -eq 1 ]] && compgen -G "dist/expra_connect-*.whl" >/dev/null; then
  BUILD_VERSION=$("$PYTHON_BIN" -c \
    'from pathlib import Path; from scripts.release import read_current_version; print(read_current_version(Path(".")))')
  LATEST_WHEEL=$("$PYTHON_BIN" scripts/release.py newest-wheel --package-dir .)
  LATEST_WHEEL_NAME=${LATEST_WHEEL##*/}
  if [[ "$LATEST_WHEEL_NAME" == "expra_connect-${BUILD_VERSION}-"* \
    && "$LATEST_WHEEL_NAME" != *-py3-none-any.whl ]]; then
    cp "$LATEST_WHEEL" "$BUILD_ROOT/artifacts/"
    BUILD_NATIVE=0
    printf 'Reusing matching native wheel: %s\n' "$LATEST_WHEEL_NAME"
  fi
fi

if [[ "$BUILD_NATIVE" -eq 1 ]]; then
  cp -a "$BUILD_ROOT/source" "$BUILD_ROOT/native-source"
  # Build from a clean source tree so ignored/stale build outputs cannot leak
  # into the Cython wheel.
  EXPRA_CONNECT_CYTHON=1 "$PYTHON_BIN" -m pip wheel --no-deps \
    --wheel-dir "$BUILD_ROOT/artifacts" "$BUILD_ROOT/native-source"
fi

if [[ "$BUILD_UNIVERSAL" -eq 1 ]]; then
  cp -a "$BUILD_ROOT/source" "$BUILD_ROOT/universal-source"
  env -u EXPRA_CONNECT_CYTHON "$PYTHON_BIN" -m pip wheel --no-deps \
    --wheel-dir "$BUILD_ROOT/artifacts" "$BUILD_ROOT/universal-source"
fi

for wheel in "$BUILD_ROOT"/artifacts/*.whl; do
  "$PYTHON_BIN" scripts/verify_wheel.py "$wheel" \
    --source-package "$BUILD_ROOT/source/src/expra_connect"
done
if [[ "$BUILD_PERFORMANCE" -eq 1 ]]; then
  BENCHMARK_ARGS=(
    --wheel-dir "$BUILD_ROOT/artifacts"
    --results-dir "$ROOT_DIR/docs/performance-build-comparison-results"
    --python "$PYTHON_BIN"
    --repository-root "$ROOT_DIR"
  )
  if [[ -n "$PYPY_PYTHON_BIN" ]]; then
    BENCHMARK_ARGS+=(
      --pypy-python "$PYPY_PYTHON_BIN"
      --pypy-results-dir "$ROOT_DIR/docs/pypy-performance-results"
    )
  fi
  "$PYTHON_BIN" scripts/benchmark_release_wheels.py \
    "${BENCHMARK_ARGS[@]}"
  "$PYTHON_BIN" -m scripts.update_performance_diagrams
else
  printf 'Skipping release wheel benchmarks and performance chart refresh.\n'
fi
SYNC_ARGS=(--package-dir . --artifact-dir "$BUILD_ROOT/artifacts")
if [[ "$BUILD_UNIVERSAL" -eq 1 ]]; then
  SYNC_ARGS+=(--allow-universal)
fi
"$PYTHON_BIN" scripts/release.py sync-artifacts "${SYNC_ARGS[@]}"
printf 'Built release artifacts:\n'
ls -1 "$BUILD_ROOT"/artifacts/*.whl
cat dist/SHA256SUMS
