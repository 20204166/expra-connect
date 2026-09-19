#!/usr/bin/env bash
set -euo pipefail

VERSION=${1:?usage: install.sh VERSION [VENV_DIR]}
VENV_DIR=${2:-"$HOME/.venvs/expra-connect"}
BASE_URL=${EXPRA_CONNECT_RELEASE_BASE_URL:-"https://github.com/20204166/expra-connect/releases/download/v${VERSION}"}
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
WHEEL="expra_connect-${VERSION}-py3-none-any.whl"
python3 -m venv "$VENV_DIR"
curl --fail --silent --show-error --location "$BASE_URL/$WHEEL" --output "$WORK_DIR/$WHEEL"
curl --fail --silent --show-error --location "$BASE_URL/SHA256SUMS" --output "$WORK_DIR/SHA256SUMS"
(cd "$WORK_DIR" && sha256sum --check SHA256SUMS --ignore-missing)
"$VENV_DIR/bin/python" -m pip install --upgrade "$WORK_DIR/$WHEEL"
installed_version=$(
  "$VENV_DIR/bin/python" -c 'from expra_connect import __version__; print(__version__)'
)
test "$installed_version" = "$VERSION"
printf 'Installed expra-peer in %s\n' "$VENV_DIR"
