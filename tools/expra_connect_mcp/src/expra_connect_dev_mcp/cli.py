"""Command-line entry point for stdio and Streamable HTTP transports."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .server import build_server

CONFIG_TEMPLATE = """schema_version = 1

[workspace]
connect_root = "."
python = "auto"

[references.exp_ui]
path = "../exp"
read_only = true

[execution]
allow_network = false
allow_lan_discovery = false
allow_pairing = false
allow_trust_mutation = false
allow_cluster_mutation = false
command_timeout_seconds = 600
max_output_kb = 256
max_parallel_commands = 2
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="expra-connect-mcp")
    parser.add_argument("--config", type=Path, default=Path(".expra-connect-mcp.toml"))
    parser.add_argument(
        "--transport", choices=("stdio", "streamable-http"), default="stdio"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    subparsers = parser.add_subparsers(dest="command")
    config_parser = subparsers.add_parser("config")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    init_parser = config_subparsers.add_parser("init")
    init_parser.add_argument("--path", type=Path)
    init_parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "config":
        if args.config_command != "init":
            _parser().error("config requires the init subcommand")
        path = (args.path or args.config).expanduser()
        if path.exists() and not args.force:
            print(f"configuration already exists: {path}", file=sys.stderr)
            return 2
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        print(path)
        return 0
    try:
        config = load_config(args.config)
        server = build_server(config)
    except (OSError, ValueError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path="/mcp",
        )
    return 0
