#!/usr/bin/env bash
set -euo pipefail

DIST_URL="https://raw.githubusercontent.com/20204166/expra-connect/main/dist"
VENV_DIR=${EXPRA_CONNECT_VENV:-"$HOME/.venvs/expra-connect"}

for command_name in curl sha256sum awk python3; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf 'Required command not found: %s\n' "$command_name" >&2
        exit 1
    fi
done

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    printf 'Python 3.10 or newer is required.\n' >&2
    exit 1
fi

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    "$DIST_URL/SHA256SUMS" --output "$work_dir/SHA256SUMS"

wheel_row=$(awk '
    $2 ~ /^expra_connect-[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+-py3-none-any\.whl$/ { row = $0 }
    END {
        if (row == "") exit 1
        print row
    }
' "$work_dir/SHA256SUMS") || {
    printf 'No universal expra-connect wheel is listed in SHA256SUMS.\n' >&2
    exit 1
}

read -r expected_hash wheel_filename extra <<<"$wheel_row"
if [[ ! $expected_hash =~ ^[[:xdigit:]]{64}$ ||
    ! $wheel_filename =~ ^expra_connect-([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)-py3-none-any\.whl$ ||
    -n ${extra:-} ]]; then
    printf 'Invalid universal wheel checksum entry.\n' >&2
    exit 1
fi
expected_version=${BASH_REMATCH[1]}

curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    "$DIST_URL/$wheel_filename" --output "$work_dir/$wheel_filename"
if ! (cd "$work_dir" && printf '%s  %s\n' "$expected_hash" "$wheel_filename" | sha256sum --check --status); then
    printf 'Checksum verification failed for %s.\n' "$wheel_filename" >&2
    exit 1
fi

if [[ -e $VENV_DIR ]]; then
    if [[ ! -x $VENV_DIR/bin/python ]] ||
        ! "$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)' >/dev/null 2>&1; then
        printf 'Refusing to install into existing non-virtual-environment path: %s\n' "$VENV_DIR" >&2
        exit 1
    fi
else
    mkdir -p "$(dirname "$VENV_DIR")"
    python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade --force-reinstall "$work_dir/$wheel_filename"
installed_version=$("$VENV_DIR/bin/python" -c \
    'import importlib.metadata as metadata; print(metadata.version("expra-connect"))')
if [[ $installed_version != "$expected_version" ]]; then
    printf 'Installed version %s does not match expected version %s.\n' \
        "$installed_version" "$expected_version" >&2
    exit 1
fi

printf 'Installed expra-connect %s in %s\n' "$installed_version" "$VENV_DIR"
printf 'Run the CLI with: %s/bin/expra-peer\n' "$VENV_DIR"
