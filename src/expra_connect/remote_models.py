"""Headless data contracts used by the authenticated remote service."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

from .identity import NodeId
from .models import NodeCapability, ProcessActionKind


class ClusterDataError(ValueError):
    """Raised when a remote data payload does not match its schema."""


class NodeStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ResourceSummary:
    key: str
    title: str
    value: str
    subtitle: str
    percent: float | None
    details: tuple[str, ...]
    actionable: bool = False
    failed: bool = False
    capability: str = "unknown"
    temperatures: tuple[Any, ...] = ()


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    system_label: str
    scanned_at: datetime
    resources: tuple[ResourceSummary, ...]


@dataclass(frozen=True, slots=True)
class ProcessCandidate:
    pid: int
    name: str
    memory_bytes: int
    memory_percent: float
    cpu_percent: float
    activity: str
    username: str
    action_allowed: bool
    create_time: float | None = None


@dataclass(frozen=True, slots=True)
class FileCandidate:
    path: Path
    size_bytes: int
    modified_at: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class ProcessActionResult:
    requested: int
    stopped: tuple[int, ...]
    force_required: tuple[int, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProcessRef:
    node_id: NodeId
    pid: int
    create_time: float | None


@dataclass(frozen=True, slots=True)
class ProcessTerminationRequest:
    target_node_id: NodeId
    processes: tuple[ProcessRef, ...]
    action: ProcessActionKind


@dataclass(frozen=True, slots=True)
class NodeSnapshot:
    node_id: NodeId
    display_name: str
    hostname: str
    platform: str | None
    status: NodeStatus
    capabilities: frozenset[NodeCapability]
    scanned_at: datetime
    dashboard: DashboardSnapshot


def _summary_to_dict(value: ResourceSummary) -> dict[str, Any]:
    temperatures = [
        asdict(cast(Any, item)) if is_dataclass(item) else item
        for item in value.temperatures
    ]
    return {
        "key": value.key,
        "title": value.title,
        "value": value.value,
        "subtitle": value.subtitle,
        "percent": value.percent,
        "details": list(value.details),
        "actionable": value.actionable,
        "failed": value.failed,
        "capability": value.capability,
        "temperatures": temperatures,
    }


def resource_summary_to_dict(value: ResourceSummary) -> dict[str, Any]:
    return _summary_to_dict(value)


def resource_summary_from_dict(value: Any) -> ResourceSummary:
    if not isinstance(value, dict):
        raise TypeError("resource summary must be an object")
    return ResourceSummary(
        key=str(value["key"]),
        title=str(value["title"]),
        value=str(value["value"]),
        subtitle=str(value["subtitle"]),
        percent=value.get("percent"),
        details=tuple(str(item) for item in value.get("details", [])),
        actionable=bool(value.get("actionable", False)),
        failed=bool(value.get("failed", False)),
        capability=str(value.get("capability", "unknown")),
        temperatures=tuple(value.get("temperatures", [])),
    )


def _dashboard_to_dict(value: DashboardSnapshot) -> dict[str, Any]:
    return {
        "system_label": value.system_label,
        "scanned_at": value.scanned_at.isoformat(),
        "resources": [_summary_to_dict(item) for item in value.resources],
    }


def _dashboard_from_dict(value: Any) -> DashboardSnapshot:
    if not isinstance(value, dict):
        raise TypeError("dashboard snapshot must be an object")
    return DashboardSnapshot(
        system_label=str(value["system_label"]),
        scanned_at=datetime.fromisoformat(str(value["scanned_at"])),
        resources=tuple(
            resource_summary_from_dict(item) for item in value["resources"]
        ),
    )


def node_snapshot_to_dict(value: NodeSnapshot) -> dict[str, Any]:
    return {
        "node_id": value.node_id.value,
        "display_name": value.display_name,
        "hostname": value.hostname,
        "platform": value.platform,
        "status": value.status.value,
        "capabilities": sorted(item.value for item in value.capabilities),
        "scanned_at": value.scanned_at.isoformat(),
        "dashboard": _dashboard_to_dict(value.dashboard),
    }


def node_snapshot_from_dict(value: Any) -> NodeSnapshot:
    if not isinstance(value, dict):
        raise TypeError("node snapshot must be an object")
    return NodeSnapshot(
        node_id=NodeId(str(value["node_id"])),
        display_name=str(value["display_name"]),
        hostname=str(value["hostname"]),
        platform=value.get("platform"),
        status=NodeStatus(str(value["status"])),
        capabilities=frozenset(NodeCapability(item) for item in value["capabilities"]),
        scanned_at=datetime.fromisoformat(str(value["scanned_at"])),
        dashboard=_dashboard_from_dict(value["dashboard"]),
    )


def process_action_result_to_dict(value: ProcessActionResult) -> dict[str, Any]:
    return {
        "requested": value.requested,
        "stopped": list(value.stopped),
        "force_required": list(value.force_required),
        "errors": list(value.errors),
    }


def process_action_result_from_dict(value: Any) -> ProcessActionResult:
    if not isinstance(value, dict):
        raise TypeError("process action result must be an object")
    return ProcessActionResult(
        requested=int(value["requested"]),
        stopped=tuple(int(item) for item in value.get("stopped", [])),
        force_required=tuple(int(item) for item in value.get("force_required", [])),
        errors=tuple(str(item) for item in value.get("errors", [])),
    )


def process_candidate_to_dict(value: ProcessCandidate) -> dict[str, Any]:
    return {
        "pid": value.pid,
        "name": value.name,
        "memory_bytes": value.memory_bytes,
        "memory_percent": value.memory_percent,
        "cpu_percent": value.cpu_percent,
        "activity": value.activity,
        "username": value.username,
        "action_allowed": value.action_allowed,
        "create_time": value.create_time,
    }


def process_candidate_from_dict(value: Any) -> ProcessCandidate:
    if not isinstance(value, dict):
        raise TypeError("process candidate must be an object")
    return ProcessCandidate(
        pid=int(value["pid"]),
        name=str(value["name"]),
        memory_bytes=int(value["memory_bytes"]),
        memory_percent=float(value["memory_percent"]),
        cpu_percent=float(value["cpu_percent"]),
        activity=str(value["activity"]),
        username=str(value["username"]),
        action_allowed=bool(value["action_allowed"]),
        create_time=value.get("create_time"),
    )


def file_candidate_to_dict(value: FileCandidate) -> dict[str, Any]:
    return {
        "path": str(value.path),
        "size_bytes": value.size_bytes,
        "modified_at": value.modified_at.isoformat(),
        "reason": value.reason,
    }


def file_candidate_from_dict(value: Any) -> FileCandidate:
    if not isinstance(value, dict):
        raise TypeError("file candidate must be an object")
    return FileCandidate(
        path=Path(str(value["path"])),
        size_bytes=int(value["size_bytes"]),
        modified_at=datetime.fromisoformat(str(value["modified_at"])),
        reason=str(value["reason"]),
    )
