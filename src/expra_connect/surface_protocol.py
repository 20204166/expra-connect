"""Wire-level metadata and validation for typed surface operations."""

import math
from typing import Any

from .models import NodeCapability

SURFACE_OPERATIONS = frozenset(
    {"surface_read", "surface_review", "surface_action"}
)
SURFACE_REQUIRED_CAPABILITY = {
    operation: NodeCapability.READ_STATE for operation in SURFACE_OPERATIONS
}


def validate_surface_operation_params(
    op: str, params: dict[str, Any], error_type: type[Exception]
) -> None:
    required = {"surface_id"} | ({"action"} if op == "surface_action" else set())
    allowed = required | {"params"}
    if set(params) - allowed or not required <= set(params):
        raise error_type(f"{op} has unexpected or missing parameters")
    for field in ("surface_id", "action"):
        value = params.get(field)
        if field in params and (
            not isinstance(value, str) or not value.strip() or len(value) > 128
        ):
            raise error_type(f"{op} {field} is invalid")
    section_params = params.get("params", {})
    if not isinstance(section_params, dict) or not _json_safe(section_params):
        raise error_type(f"{op} params must be JSON-safe")


def _json_safe(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _json_safe(item) for key, item in value.items()
        )
    return False
