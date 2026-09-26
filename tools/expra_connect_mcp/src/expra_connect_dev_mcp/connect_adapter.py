"""Static, child-interpreter-backed inspection of current Connect state."""

from __future__ import annotations

import json
from typing import Any, Literal

from .command_runner import run_fixed_command
from .config import McpConfig, resolve_python
from .models import InspectionResult

InspectionStatus = Literal[
    "IMPLEMENTED", "PARTIAL", "TEST_ONLY", "DEFERRED", "NOT_PRESENT"
]
_ACTIONS = {
    "architecture",
    "runtime",
    "config",
    "models",
    "capabilities",
    "lifecycle",
    "module",
    "identity",
    "registry",
    "pairing",
    "capability",
    "cluster",
}

_STATIC_PROBE = r"""
import json
import msgpack
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
action = sys.argv[2]
source = root / "src" / "expra_connect"
result = {"action": action, "files": [], "state_files": [], "reasons": []}
skip = {".git", ".venv", "build", "dist", "__pycache__"}

def add_file(path):
    result["files"].append({
        "path": str(path.relative_to(root)),
        "exists": path.is_file(),
    })

module_files = {
    "architecture": ["runtime.py", "discovery_full.py", "registry.py", "connection_manager.py", "pairing.py", "transport.py"],
    "runtime": ["runtime.py", "runtime_diagnostics.py", "runtime_persistence.py"],
    "config": ["runtime.py", "persistence.py"],
    "models": ["models.py", "remote_models.py", "pairing_models.py"],
    "capabilities": ["sharing.py", "surfaces.py", "wire_protocol.py"],
    "lifecycle": ["runtime.py", "connection_state.py", "observability.py"],
    "module": ["__init__.py"],
}
if action in module_files:
    for name in module_files[action]:
        add_file(source / name)
    result["status"] = "IMPLEMENTED" if all(item["exists"] for item in result["files"]) else "PARTIAL"
else:
    names = {"identity.json", "identity.msgpack", "device_identity.json", "device_identity.msgpack", "transport.json", "trust.json", "pairing.json", "cluster.json"}
    documents = []
    for directory, dirs, files in __import__("os").walk(root, followlinks=False):
        dirs[:] = [item for item in dirs if item not in skip]
        for name in files:
            if name in names or name.startswith("transport-"):
                documents.append(Path(directory) / name)
    for path in sorted(documents):
        if path.suffix == ".msgpack":
            try:
                payload = path.read_bytes()
                if len(payload) > 1048576:
                    raise ValueError("state_size_limit")
                value = msgpack.unpackb(
                    payload,
                    raw=False,
                    strict_map_key=True,
                    max_str_len=1048576,
                    max_bin_len=1048576,
                    max_array_len=100000,
                    max_map_len=100000,
                    max_ext_len=0,
                )
            except Exception as error:
                result["state_files"].append({"path": str(path.relative_to(root)), "malformed": type(error).__name__})
                continue
            if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
                result["state_files"].append({"path": str(path.relative_to(root)), "malformed": "root_not_string_keyed_map"})
                continue
            item = {"path": str(path.relative_to(root)), "format": "messagepack", "keys": sorted(value)}
            if isinstance(value.get("node_id"), str):
                item["node_id"] = value["node_id"]
            secret_fields = sorted(field for field in ("secret", "root_private_key", "private_key", "token", "fencing_token") if field in value)
            if secret_fields:
                item["secret_fields_present"] = secret_fields
            result["state_files"].append(item)
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            result["state_files"].append({"path": str(path.relative_to(root)), "malformed": type(error).__name__})
            continue
        if not isinstance(value, dict):
            result["state_files"].append({"path": str(path.relative_to(root)), "malformed": "root_not_object"})
            continue
        item = {"path": str(path.relative_to(root)), "keys": sorted(value)}
        if "node_id" in value:
            item["node_id"] = value["node_id"]
        for field in ("fingerprint", "transport_fingerprint", "root_fingerprint"):
            if isinstance(value.get(field), str):
                item[field] = value[field]
        for field in ("secret", "root_private_key", "token", "fencing_token"):
            if field in value:
                item[field + "_present"] = True
        for field in ("trusted", "grants", "pending", "assignments", "capability_grants", "routes"):
            if isinstance(value.get(field), list):
                item[field + "_count"] = len(value[field])
        if "epoch" in value and isinstance(value["epoch"], dict):
            epoch = value["epoch"]
            item["epoch"] = epoch.get("epoch")
            item["coordinator_id"] = epoch.get("coordinator_id")
            item["fencing_token_present"] = bool(epoch.get("fencing_token"))
        result["state_files"].append(item)
    result["status"] = "IMPLEMENTED" if result["state_files"] else "NOT_PRESENT"
    if not result["state_files"]:
        result["reasons"].append("no supported persisted state documents were found")
print(json.dumps(result, sort_keys=True))
"""


async def inspect_connect(config: McpConfig, action: str) -> InspectionResult:
    if action not in _ACTIONS:
        raise ValueError("unsupported inspection action: " + action)
    python = resolve_python(config.workspace.connect_root, config.workspace.python).path
    result = await run_fixed_command(
        [str(python), "-c", _STATIC_PROBE, str(config.workspace.connect_root), action],
        cwd=config.workspace.connect_root,
        timeout_seconds=config.execution.command_timeout_seconds,
        max_output_kb=config.execution.max_output_kb,
    )
    if result.exit_code != 0:
        return InspectionResult(
            action=action,
            status="NOT_PRESENT",
            execution_mode="STATIC",
            evidence={},
            reasons=[result.stderr.strip() or "static inspection failed"],
        )
    try:
        evidence: dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError:
        evidence = {}
    raw_status = evidence.pop("status", "NOT_PRESENT")
    status: InspectionStatus = (
        raw_status
        if raw_status
        in {"IMPLEMENTED", "PARTIAL", "TEST_ONLY", "DEFERRED", "NOT_PRESENT"}
        else "NOT_PRESENT"
    )
    reasons = evidence.pop("reasons", [])
    return InspectionResult(
        action=action,
        status=status,
        execution_mode="STATIC",
        evidence=evidence,
        reasons=[str(reason) for reason in reasons],
    )
