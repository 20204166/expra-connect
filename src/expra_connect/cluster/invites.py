"""One-time invite creation, hashing, and copyable encoding."""

from __future__ import annotations

import base64
import json
import math
import secrets

from .models import CoordinatorEpoch, InviteRecord
from .roles import hash_invite


class ClusterDataError(ValueError):
    pass


def invite_to_dict(invite: InviteRecord) -> dict[str, object]:
    """Return the persisted form, deliberately excluding the one-time token."""
    return {
        "token_hash": invite.token_hash,
        "target_node_id": invite.target_node_id,
        "expires_at": invite.expires_at,
        "cluster_id": invite.cluster_id,
        "coordinator_id": invite.coordinator_id,
        "epoch": invite.epoch,
        "fencing_token": invite.fencing_token,
    }


def create_invite(
    *,
    cluster_id: str,
    target_node_id: str,
    epoch: CoordinatorEpoch,
    now: float,
    ttl_seconds: float = 300.0,
) -> InviteRecord:
    if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
        raise ValueError("invite TTL must be positive")
    token = secrets.token_urlsafe(32)
    return InviteRecord(
        hash_invite(token),
        target_node_id,
        now + ttl_seconds,
        token,
        cluster_id,
        epoch.coordinator_id.value,
        epoch.epoch,
        epoch.fencing_token,
    )


def encode_invite_blob(invite: InviteRecord) -> str:
    payload = {
        "token": invite.token,
        "cluster_id": invite.cluster_id,
        "coordinator_id": invite.coordinator_id,
        "epoch": invite.epoch,
        "fencing_token": invite.fencing_token,
        "target_node_id": invite.target_node_id,
        "expires_at": invite.expires_at,
    }
    return base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True).encode()
    ).decode()


def decode_invite_blob(blob: str) -> InviteRecord:
    try:
        value = json.loads(base64.urlsafe_b64decode(blob.encode("ascii")))
        if not isinstance(value, dict):
            raise TypeError("invite payload is not an object")
        token, cluster, coordinator, fence, target = (
            value[k]
            for k in (
                "token",
                "cluster_id",
                "coordinator_id",
                "fencing_token",
                "target_node_id",
            )
        )
        epoch, expiry = value["epoch"], value["expires_at"]
        if (
            not all(
                isinstance(x, str) and x
                for x in (token, cluster, coordinator, fence, target)
            )
            or type(epoch) is not int
            or epoch < 0
            or not isinstance(expiry, (int, float))
            or isinstance(expiry, bool)
            or not math.isfinite(float(expiry))
        ):
            raise ValueError
        return InviteRecord(
            hash_invite(token),
            target,
            float(expiry),
            token,
            cluster,
            coordinator,
            epoch,
            fence,
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
    ) as error:
        raise ClusterDataError("invite is not a valid pairing invite") from error
