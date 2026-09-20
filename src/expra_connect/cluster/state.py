"""Strict JSON codec for role and capability state."""

from __future__ import annotations

import math
from typing import Any

from ..identity import NodeId
from ..models import NodePermission
from .models import CapabilityGrant, ClusterRole, CoordinatorEpoch, RoleAssignment
from .roles import RoleState


def role_state_to_dict(state: RoleState) -> dict[str, Any]:
    return {
        "assignments": [
            {
                "node_id": a.node_id.value if a.node_id else None,
                "roles": sorted(r.value for r in a.roles),
                "paused": a.paused,
                "revoked": a.revoked,
                "has_active_job": a.has_active_job,
            }
            for a in state.assignments
        ],
        "epoch": None
        if state.epoch is None
        else {
            "epoch": state.epoch.epoch,
            "coordinator_id": state.epoch.coordinator_id.value,
            "fencing_token": state.epoch.fencing_token,
            "issued_at": state.epoch.issued_at,
            "lease_expires_at": state.epoch.lease_expires_at,
        },
        "promotion_epochs": sorted(state.promotion_epochs),
        "capability_grants": [
            {
                "subject": g.subject.value,
                "target": g.target.value,
                "permissions": sorted(p.value for p in g.permissions),
                "issued_at": g.issued_at,
                "expires_at": g.expires_at,
            }
            for g in state.capability_grants
        ],
    }


def _finite_number(value: Any, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{field} is malformed")
    return float(value)


def role_state_from_dict(value: object) -> RoleState:
    if not isinstance(value, dict) or not isinstance(
        value.get("assignments", []), list
    ):
        raise TypeError("role state is malformed")
    assignments: list[RoleAssignment] = []
    for raw in value["assignments"]:
        if not isinstance(raw, dict):
            raise TypeError("role assignment is malformed")
        roles = raw.get("roles")
        if roles is None and isinstance(raw.get("role"), str):
            roles = [raw["role"]]
        if (
            not isinstance(raw.get("node_id"), str)
            or not isinstance(roles, list)
            or any(not isinstance(item, str) for item in roles)
        ):
            raise ValueError("role assignment is malformed")
        for field in ("paused", "revoked"):
            if field in raw and not isinstance(raw[field], bool):
                raise ValueError("role assignment boolean is malformed")
        if "has_active_job" in raw and not isinstance(raw["has_active_job"], bool):
            raise ValueError("role assignment boolean is malformed")
        assignments.append(
            RoleAssignment(
                frozenset(ClusterRole(item) for item in roles),
                NodeId(raw["node_id"]),
                paused=raw.get("paused", False),
                revoked=raw.get("revoked", False),
                has_active_job=False,
            )
        )
    raw_epoch = value.get("epoch")
    epoch = None
    if raw_epoch is not None:
        if (
            not isinstance(raw_epoch, dict)
            or type(raw_epoch.get("epoch")) is not int
            or raw_epoch["epoch"] < 0
        ):
            raise ValueError("epoch is malformed")
        if (
            not isinstance(raw_epoch.get("coordinator_id"), str)
            or not isinstance(raw_epoch.get("fencing_token"), str)
            or not raw_epoch["fencing_token"]
        ):
            raise ValueError("epoch is malformed")
        epoch = CoordinatorEpoch(
            raw_epoch["epoch"],
            NodeId(raw_epoch["coordinator_id"]),
            raw_epoch["fencing_token"],
            _finite_number(raw_epoch.get("issued_at"), "epoch issued_at"),
            _finite_number(raw_epoch.get("lease_expires_at"), "epoch lease_expires_at"),
        )
    raw_promotions = value.get("promotion_epochs", [])
    if not isinstance(raw_promotions, list) or any(
        type(item) is not int or item < 0 for item in raw_promotions
    ):
        raise ValueError("promotion epochs are malformed")
    raw_grants = value.get("capability_grants", [])
    if not isinstance(raw_grants, list):
        raise TypeError("capability grants are malformed")
    grants: list[CapabilityGrant] = []
    for raw in raw_grants:
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("subject"), str)
            or not isinstance(raw.get("target"), str)
            or not isinstance(raw.get("permissions"), list)
            or any(not isinstance(item, str) for item in raw["permissions"])
        ):
            raise ValueError("capability grant is malformed")
        grants.append(
            CapabilityGrant(
                NodeId(raw["subject"]),
                NodeId(raw["target"]),
                frozenset(NodePermission(item) for item in raw["permissions"]),
                _finite_number(raw.get("issued_at"), "grant issued_at"),
                _finite_number(raw.get("expires_at"), "grant expires_at"),
            )
        )
    return RoleState(
        tuple(assignments), epoch, frozenset(raw_promotions), tuple(grants)
    )
