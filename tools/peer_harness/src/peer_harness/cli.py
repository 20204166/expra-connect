"""Command-line entry point for the peer acceptance harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import analyzer
from .flows import _runtime, _runtime_factory, run_initiator, run_target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="peer_harness",
        description="Run an Expra Connect peer as a target, initiator, or analyzer.",
    )
    parser.add_argument(
        "role_pos", nargs="?", choices=("target", "initiator", "analyze")
    )
    parser.add_argument(
        "--role", dest="role_option", choices=("target", "initiator", "analyze")
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="analyze-only: JSON report files and/or directories",
    )
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--report", type=Path, default=Path("peer-report.json"))
    parser.add_argument("--peer-id", help="expected target node ID")
    parser.add_argument(
        "--existing-peer-id",
        help="initiator-only: reuse already trusted peer after a profile restart",
    )
    parser.add_argument(
        "--advertise-address",
        action="append",
        help="explicit address to advertise; repeat for multiple interfaces",
    )
    parser.add_argument(
        "--rotate-after",
        type=float,
        help="target-only: rotate transport after this many seconds",
    )
    parser.add_argument(
        "--reconnect-after-rotation",
        action="store_true",
        help="initiator-only: reconnect and share again after rotation",
    )
    parser.add_argument(
        "--rotation-wait",
        type=float,
        default=15.0,
        help="seconds to wait before reconnecting after a target rotation",
    )
    parser.add_argument(
        "--revoke-self",
        action="store_true",
        help="initiator-only: revoke this caller on the target and verify denial",
    )
    parser.add_argument("--wait", type=float, default=60.0)
    parser.add_argument("--bidirectional-surfaces", action="store_true")
    parser.add_argument("--restart-check", action="store_true")
    parser.add_argument(
        "--explicit-approval",
        action="store_true",
        help="target-only: require an approval file before allowing the caller",
    )
    parser.add_argument(
        "--approval-file",
        type=Path,
        help="target-only: sentinel file to create; defaults to <report>.approve",
    )
    parser.add_argument(
        "--approval-wait",
        type=float,
        default=30.0,
        help="seconds to wait for the approval sentinel before denying",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="analyze-only: emit machine-readable JSON",
    )
    parser.add_argument(
        "--full-identifiers",
        action="store_true",
        help="analyze-only: show full public IDs instead of shortened forms",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="analyze-only: exit 2 if warning/error findings are present",
    )
    return parser


def _run_analyze(args: argparse.Namespace) -> int:
    try:
        report = analyzer.analyze(args.inputs, full_ids=args.full_identifiers)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return analyzer.render(report, as_json=args.json, strict=args.strict)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.role_pos and args.role_option and args.role_pos != args.role_option:
        parser.error("positional role and --role must match")
    args.role = args.role_option or args.role_pos
    if args.role is None:
        parser.error("a role is required: use target, initiator, or analyze")
    if args.role == "analyze":
        return _run_analyze(args)
    if args.profile is None:
        parser.error("--profile is required for target and initiator roles")
    runtime = _runtime(args)
    if args.role == "target":
        return run_target(args, runtime)
    return run_initiator(args, runtime, runtime_factory=_runtime_factory(args))
