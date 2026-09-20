"""Headless node registry keeping trust, membership, and connection separate."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

from .connection_state import ConnectionState
from .identity import NodeId
from .models import NodeCapability
from .role_engine import ClusterRole


class TrustState(str, Enum):
    DISCOVERED = "discovered"
    TRUSTED = "trusted"
    AUTHORIZED = "authorized"
    REVOKED = "revoked"


class MembershipState(str, Enum):
    NOT_JOINED = "not_joined"
    WORKER = "worker"
    COORDINATOR = "coordinator"
    SUBCOORDINATOR = "subcoordinator"


@dataclass(frozen=True, slots=True)
class NodeRecord:
    node_id: NodeId
    trust: TrustState = TrustState.DISCOVERED
    membership: MembershipState = MembershipState.NOT_JOINED
    connection: ConnectionState = field(default_factory=ConnectionState.unknown)
    capabilities: frozenset[NodeCapability] = frozenset()
    coordinator_id: NodeId | None = None


class NodeRegistry:
    def __init__(self, local_node_id: NodeId, *, cluster_enabled: bool = True) -> None:
        self.local_node_id = local_node_id
        self._records: dict[NodeId, NodeRecord] = {
            local_node_id: NodeRecord(
                local_node_id,
                trust=TrustState.AUTHORIZED,
                membership=(
                    MembershipState.COORDINATOR
                    if cluster_enabled
                    else MembershipState.NOT_JOINED
                ),
            )
        }

    def record(self, node_id: NodeId) -> NodeRecord | None:
        return self._records.get(node_id)

    @property
    def records(self) -> tuple[NodeRecord, ...]:
        """Return a stable snapshot for diagnostics and host adapters."""

        return tuple(self._records.values())

    def observe(
        self, node_id: NodeId, capabilities: frozenset[NodeCapability]
    ) -> NodeRecord:
        if node_id == self.local_node_id:
            return self._records[node_id]
        current = self._records.get(node_id, NodeRecord(node_id))
        updated = replace(current, capabilities=capabilities)
        self._records[node_id] = updated
        return updated

    def promote(
        self, node_id: NodeId, *, permissions: frozenset[NodeCapability]
    ) -> NodeRecord:
        current = self._require(node_id)
        if current.trust is TrustState.REVOKED:
            raise PermissionError("revoked node requires a fresh pairing")
        updated = replace(
            current,
            trust=TrustState.AUTHORIZED,
            capabilities=permissions,
        )
        self._records[node_id] = updated
        return updated

    def revoke(self, node_id: NodeId) -> NodeRecord:
        current = self._require(node_id)
        updated = replace(current, trust=TrustState.REVOKED)
        self._records[node_id] = updated
        return updated

    def set_connection(self, node_id: NodeId, state: ConnectionState) -> NodeRecord:
        current = self._require(node_id)
        updated = replace(current, connection=state)
        self._records[node_id] = updated
        return updated

    def join(
        self,
        node_id: NodeId,
        *,
        role: ClusterRole,
        coordinator_id: NodeId,
    ) -> NodeRecord:
        current = self._require(node_id)
        if current.trust not in {TrustState.TRUSTED, TrustState.AUTHORIZED}:
            raise PermissionError("untrusted node cannot join the cluster")
        membership = MembershipState(role.value)
        updated = replace(
            current,
            membership=membership,
            coordinator_id=coordinator_id,
        )
        self._records[node_id] = updated
        return updated

    def hydrate_membership(
        self,
        node_id: NodeId,
        *,
        role: ClusterRole,
        coordinator_id: NodeId,
    ) -> NodeRecord:
        """Project persisted canonical membership without changing trust."""
        current = self._require(node_id)
        updated = replace(
            current,
            membership=MembershipState(role.value),
            coordinator_id=coordinator_id,
        )
        self._records[node_id] = updated
        return updated

    def _require(self, node_id: NodeId) -> NodeRecord:
        record = self._records.get(node_id)
        if record is None:
            raise KeyError(node_id)
        return record
