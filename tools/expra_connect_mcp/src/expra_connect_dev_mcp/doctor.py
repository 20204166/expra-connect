"""Environment identity proof for Expra Connect operations."""

from __future__ import annotations

import json
from typing import Any, Literal

from .command_runner import run_fixed_command
from .config import McpConfig, resolve_python
from .models import DependencyEvidence, WorkspaceDoctorResult
from .source_access import _git_state

_ENVIRONMENT_PROBE = r"""
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
src = root / "src"
sys.path.insert(0, str(src))
result = {
    "python_version": platform.python_version(),
    "imported_expra_connect": None,
    "expra_connect_version": None,
    "source_root_match": False,
    "dependencies": [],
}
try:
    import expra_connect
    origin = Path(expra_connect.__file__).resolve()
    result["imported_expra_connect"] = str(origin)
    result["expra_connect_version"] = getattr(expra_connect, "__version__", None)
    try:
        origin.relative_to(src.resolve())
        result["source_root_match"] = True
    except ValueError:
        pass
except Exception as error:
    result["import_error"] = f"{type(error).__name__}: {error}"
for name in ("zeroconf", "cryptography", "pytest", "ruff", "pyright", "mypy"):
    try:
        result["dependencies"].append({
            "name": name,
            "available": True,
            "version": importlib.metadata.version(name),
        })
    except importlib.metadata.PackageNotFoundError:
        result["dependencies"].append({"name": name, "available": False})
    except Exception as error:
        result["dependencies"].append({
            "name": name,
            "available": False,
            "error": f"{type(error).__name__}: {error}",
        })
print(json.dumps(result, sort_keys=True))
"""


async def workspace_doctor(config: McpConfig) -> WorkspaceDoctorResult:
    root = config.workspace.connect_root
    resolved = resolve_python(root, config.workspace.python)
    probe = await run_fixed_command(
        [str(resolved.path), "-c", _ENVIRONMENT_PROBE, str(root)],
        cwd=root,
        timeout_seconds=config.execution.command_timeout_seconds,
        max_output_kb=config.execution.max_output_kb,
    )
    revision, dirty = await _git_state(config)
    evidence: dict[str, Any] = {}
    reasons: list[str] = []
    if probe.exit_code == 0:
        try:
            evidence = json.loads(probe.stdout)
        except json.JSONDecodeError:
            reasons.append("configured Python returned invalid doctor evidence")
    else:
        reasons.append(
            "configured Python probe failed"
            + (f": {probe.stderr.strip()}" if probe.stderr.strip() else "")
        )
    if resolved.used_fallback and resolved.fallback_reason:
        reasons.append(resolved.fallback_reason)
    if not evidence.get("source_root_match", False):
        reasons.append("expra_connect is not imported from the configured source root")
    if revision is None:
        reasons.append("git revision is unavailable")
    dependencies = [
        DependencyEvidence.model_validate(item)
        for item in evidence.get("dependencies", [])
    ]
    missing = [item.name for item in dependencies if not item.available]
    if missing:
        reasons.append(
            "optional or diagnostic dependencies are unavailable: " + ", ".join(missing)
        )
    status: Literal["READY", "READY_WITH_LIMITATIONS", "NOT_READY"]
    if probe.exit_code != 0 or not evidence.get("source_root_match", False):
        status = "NOT_READY"
    elif reasons:
        status = "READY_WITH_LIMITATIONS"
    else:
        status = "READY"
    return WorkspaceDoctorResult(
        status=status,
        repository_root=str(root),
        configured_python=str(resolved.path),
        python_fallback=resolved.used_fallback,
        python_fallback_reason=resolved.fallback_reason,
        python_version=evidence.get("python_version"),
        imported_expra_connect=evidence.get("imported_expra_connect"),
        expra_connect_version=evidence.get("expra_connect_version"),
        source_root=str(root / "src"),
        source_root_match=bool(evidence.get("source_root_match", False)),
        git_revision=revision,
        git_dirty=dirty,
        dependencies=dependencies,
        reasons=reasons,
    )
