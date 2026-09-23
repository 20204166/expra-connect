"""Wire-level metadata and validation for typed surface operations."""

import json
import math
from typing import Any

from .models import NodeCapability

SURFACE_READ = "surface_read"
SURFACE_REVIEW = "surface_review"
SURFACE_ACTION = "surface_action"
SURFACE_OPERATIONS = frozenset({SURFACE_READ, SURFACE_REVIEW, SURFACE_ACTION})
MAX_SURFACE_ID_LENGTH = 128
MAX_SURFACE_PARAM_DEPTH = 32
SURFACE_REQUIRED_CAPABILITY = {
    SURFACE_READ: NodeCapability.READ_STATE,
    SURFACE_REVIEW: NodeCapability.READ_STATE,
    SURFACE_ACTION: NodeCapability.REMOTE_MANAGEMENT,
}
SURFACE_ACCESS_BY_OPERATION = {
    SURFACE_READ: "read",
    SURFACE_REVIEW: "review",
    SURFACE_ACTION: "action",
}
SURFACE_OPERATION_SAFETY = {
    SURFACE_READ: "read",
    SURFACE_REVIEW: "read",
    SURFACE_ACTION: "unsafe",
}


def validate_surface_operation_params(
    op: str, params: dict[str, Any], error_type: type[Exception]
) -> None:
    if not isinstance(op, str) or op not in SURFACE_OPERATIONS:
        raise error_type(f"unknown surface operation: {op}")
    if not isinstance(params, dict):
        raise error_type("surface operation parameters must be an object")
    required = {"surface_id"} | ({"action"} if op == SURFACE_ACTION else set())
    allowed = required | {"params"}
    if set(params) - allowed or not required <= set(params):
        raise error_type(f"{op} has unexpected or missing parameters")
    for field in ("surface_id", "action"):
        value = params.get(field)
        if field in params and (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > MAX_SURFACE_ID_LENGTH
        ):
            raise error_type(f"{op} {field} is invalid")
    section_params = params.get("params", {})
    if not isinstance(section_params, dict) or not _json_safe(section_params):
        raise error_type(f"{op} params must be JSON-safe")
    try:
        json.dumps(section_params, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise error_type(f"{op} params must be JSON-safe") from error


def _json_safe(value: Any) -> bool:
    stack: list[tuple[Any, int, bool]] = [(value, 0, False)]
    active_containers: set[int] = set()
    while stack:
        current, depth, exiting = stack.pop()
        if exiting:
            active_containers.remove(id(current))
            continue
        if depth > MAX_SURFACE_PARAM_DEPTH:
            return False
        if current is None or isinstance(current, (str, bool, int)):
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                return False
            continue
        if not isinstance(current, (list, dict)):
            return False
        identity = id(current)
        if identity in active_containers:
            return False
        active_containers.add(identity)
        stack.append((current, depth, True))
        if isinstance(current, dict):
            for key, item in reversed(tuple(current.items())):
                if not isinstance(key, str):
                    return False
                stack.append((item, depth + 1, False))
        else:
            for item in reversed(current):
                stack.append((item, depth + 1, False))
    return True
