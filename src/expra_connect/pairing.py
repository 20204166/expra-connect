"""Directional pairing transactions, grants, and relationship projection."""

from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from .identity import NodeId, verify_transport_proof

# ``ping`` is retained as the legacy read-only operation used by the original
# pairing API; network pairing uses ``read_state``.
_READ_ONLY_PERMISSIONS = frozenset({"ping", "read_state"})

DIRECTIONS = frozenset({"outbound", "inbound"})
INTENTS = frozenset({"pair", "repair"})


def grant_root_continuous(
    grant: PeerGrant,
    *,
    root_public_key: str | None,
    transport_generation: int | None,
    transport_fingerprint: str,
    transport_proof: str | None,
) -> bool:
    """Prove an inbound caller still holds the root the stored grant trusts."""

    if not grant.root_public_key:
        return False
    if root_public_key != grant.root_public_key:
        return False
    if transport_generation is None or not transport_proof:
        return False
    return verify_transport_proof(
        grant.caller_id,
        grant.root_public_key,
        transport_generation,
        transport_fingerprint,
        transport_proof,
    )


class RelationshipState(str, Enum):
    """Canonical directional view of the local relationship with one peer.

    The outbound and inbound directions are independent. ``OUTBOUND_TRUSTED``
    describes a local ``TrustedPeer`` (this node may call the peer) while
    ``INBOUND_GRANTED`` describes a local ``PeerGrant`` (the peer may call this
    node). An inbound grant never implies outbound trust and vice versa.
    """

    UNPAIRED = "unpaired"
    OUTBOUND_TRUSTED = "outbound_trusted"
    INBOUND_GRANTED = "inbound_granted"
    BIDIRECTIONAL = "bidirectional"
    PENDING_OUTBOUND = "pending_outbound"
    PENDING_INBOUND = "pending_inbound"
    REPAIR_REQUIRED = "repair_required"
    IDENTITY_CONFLICT = "identity_conflict"


class PairingConflict(ValueError):
    """Base class for fail-closed pairing relationship decisions."""

    def __init__(
        self,
        message: str,
        *,
        peer_id: NodeId | None = None,
        state: RelationshipState | None = None,
    ) -> None:
        super().__init__(message)
        self.peer_id = peer_id
        self.state = state


class PairingBusy(PairingConflict):
    """Raised when a competing transaction already exists for the peer."""


class RepairRequired(PairingConflict):
    """Raised when a broken outbound credential needs explicit repair."""


class RepairNotRequired(PairingConflict):
    """Raised when an explicit repair targets a healthy relationship."""


class IdentityConflict(PairingConflict):
    """Raised when a peer cannot prove continuity of its trusted root."""


@dataclass(frozen=True, slots=True)
class PendingPairing:
    transaction_id: str
    peer_id: NodeId
    secret: str
    expires_at: float
    identity_fingerprint: str | None = None
    transport_fingerprint: str | None = None
    root_public_key: str | None = None
    transport_generation: int | None = None
    transport_proof: str | None = None
    direction: str = "outbound"
    intent: str = "pair"

    def __post_init__(self) -> None:
        if self.direction not in DIRECTIONS:
            raise ValueError("pairing direction is invalid")
        if self.intent not in INTENTS:
            raise ValueError("pairing intent is invalid")


@dataclass(frozen=True, slots=True)
class TrustedPeer:
    peer_id: NodeId
    secret: str
    permissions: frozenset[str]
    identity_fingerprint: str | None = None
    transport_fingerprint: str | None = None
    root_public_key: str | None = None
    transport_generation: int | None = None
    transport_proof: str | None = None


@dataclass(frozen=True, slots=True)
class PeerGrant:
    caller_id: NodeId
    secret: str
    permissions: frozenset[str]
    identity_fingerprint: str | None = None
    transport_fingerprint: str | None = None
    root_public_key: str | None = None
    transport_generation: int | None = None
    transport_proof: str | None = None


class PairingManager:
    def __init__(
        self,
        local_id: NodeId,
        *,
        clock: Callable[[], float] = time.time,
        ttl: float = 300.0,
    ) -> None:
        self.local_id = local_id
        self._clock = clock
        self._ttl = ttl
        self.pending: dict[str, PendingPairing] = {}
        self.trusted: dict[NodeId, TrustedPeer] = {}
        self.grants: dict[NodeId, PeerGrant] = {}
        self._auth_broken: dict[NodeId, bool] = {}

    def begin(
        self,
        peer_id: NodeId,
        secret: str | None = None,
        *,
        identity_fingerprint: str | None = None,
        transport_fingerprint: str | None = None,
        root_public_key: str | None = None,
        transport_generation: int | None = None,
        transport_proof: str | None = None,
        direction: str = "outbound",
        intent: str = "pair",
    ) -> PendingPairing:
        if direction not in DIRECTIONS:
            raise ValueError("pairing direction is invalid")
        if intent not in INTENTS:
            raise ValueError("pairing intent is invalid")
        if secret is not None:
            replay = self.find_pending(peer_id, secret)
            if (
                replay is not None
                and replay.direction == direction
                and replay.intent == intent
            ):
                return replay
        if self.find_pending_for(peer_id, direction=direction) is not None:
            raise PairingBusy(
                "a pairing transaction is already active for this peer",
                peer_id=peer_id,
            )
        transaction = PendingPairing(
            uuid.uuid4().hex,
            peer_id,
            secret or secrets.token_hex(32),
            self._clock() + self._ttl,
            identity_fingerprint,
            transport_fingerprint,
            root_public_key,
            transport_generation,
            transport_proof,
            direction,
            intent,
        )
        self.pending[transaction.transaction_id] = transaction
        return transaction

    def approve(self, transaction_id: str, permissions: frozenset[str]) -> PeerGrant:
        transaction = self._active(transaction_id)
        if not permissions <= _READ_ONLY_PERMISSIONS:
            raise ValueError("pairing permissions are read-only")
        grant = PeerGrant(
            transaction.peer_id,
            transaction.secret,
            permissions,
            transaction.identity_fingerprint,
            transaction.transport_fingerprint,
            transaction.root_public_key,
            transaction.transport_generation,
            transaction.transport_proof,
        )
        self.grants[transaction.peer_id] = grant
        return grant

    def receive_request(
        self, caller_id: NodeId, transaction: PendingPairing
    ) -> PendingPairing:
        if transaction.expires_at <= self._clock():
            raise ValueError("pairing transaction is expired")
        pending = PendingPairing(
            transaction.transaction_id,
            caller_id,
            transaction.secret,
            transaction.expires_at,
            transaction.identity_fingerprint,
            transaction.transport_fingerprint,
            transaction.root_public_key,
            transaction.transport_generation,
            transaction.transport_proof,
            "inbound",
            transaction.intent,
        )
        self.pending[pending.transaction_id] = pending
        return pending

    def confirm(self, transaction_id: str, *, now: float | None = None) -> TrustedPeer:
        transaction = self._active(transaction_id, now=now)
        grant = self.grants.get(transaction.peer_id)
        if grant is None:
            raise ValueError("pairing was not approved")
        trusted = TrustedPeer(
            transaction.peer_id,
            transaction.secret,
            grant.permissions,
            grant.identity_fingerprint,
            grant.transport_fingerprint,
            grant.root_public_key,
            grant.transport_generation,
            grant.transport_proof,
        )
        self.trusted[transaction.peer_id] = trusted
        self._auth_broken.pop(transaction.peer_id, None)
        del self.pending[transaction_id]
        return trusted

    def accept_grant(
        self,
        transaction_id: str,
        peer_id: NodeId,
        grant: PeerGrant,
        *,
        now: float | None = None,
    ) -> TrustedPeer:
        transaction = self._active(transaction_id, now=now)
        if transaction.peer_id != peer_id or grant.caller_id != self.local_id:
            raise ValueError("pairing grant binding mismatch")
        if (
            grant.identity_fingerprint != transaction.identity_fingerprint
            or grant.transport_fingerprint != transaction.transport_fingerprint
            or grant.root_public_key != transaction.root_public_key
            or grant.transport_generation != transaction.transport_generation
            or grant.transport_proof != transaction.transport_proof
        ):
            raise ValueError("pairing fingerprint binding mismatch")
        trusted = TrustedPeer(
            peer_id,
            transaction.secret,
            grant.permissions,
            grant.identity_fingerprint,
            grant.transport_fingerprint,
            grant.root_public_key,
            grant.transport_generation,
            grant.transport_proof,
        )
        self.trusted[peer_id] = trusted
        self._auth_broken.pop(peer_id, None)
        del self.pending[transaction_id]
        return trusted

    def abort(self, transaction_id: str) -> None:
        self.pending.pop(transaction_id, None)

    def prune_expired(self, *, now: float | None = None) -> int:
        current = self._clock() if now is None else now
        expired = [
            key for key, item in self.pending.items() if item.expires_at <= current
        ]
        for key in expired:
            del self.pending[key]
        return len(expired)

    def revoke(self, peer_id: NodeId) -> None:
        self.trusted.pop(peer_id, None)
        self.grants.pop(peer_id, None)
        self._auth_broken.pop(peer_id, None)
        self.pending = {
            key: item for key, item in self.pending.items() if item.peer_id != peer_id
        }

    def revoke_trusted(self, peer_id: NodeId) -> None:
        """Remove only the outbound direction, preserving inbound grants."""
        self.trusted.pop(peer_id, None)
        self._auth_broken.pop(peer_id, None)
        self.pending = {
            key: item
            for key, item in self.pending.items()
            if not (item.peer_id == peer_id and item.direction == "outbound")
        }

    def revoke_grant(self, peer_id: NodeId) -> None:
        """Remove only the inbound direction, preserving outbound trust."""
        self.grants.pop(peer_id, None)
        self.pending = {
            key: item
            for key, item in self.pending.items()
            if not (item.peer_id == peer_id and item.direction == "inbound")
        }

    def find_pending(self, peer_id: NodeId, secret: str) -> PendingPairing | None:
        """Find an active exact transaction without changing transaction state."""
        current = self._clock()
        return next(
            (
                transaction
                for transaction in self.pending.values()
                if transaction.peer_id == peer_id
                and transaction.secret == secret
                and transaction.expires_at > current
            ),
            None,
        )

    def find_pending_for(
        self, peer_id: NodeId, *, direction: str | None = None
    ) -> PendingPairing | None:
        """Return the single active transaction for one direction, if any."""
        current = self._clock()
        return next(
            (
                transaction
                for transaction in self.pending.values()
                if transaction.peer_id == peer_id
                and transaction.expires_at > current
                and (direction is None or transaction.direction == direction)
            ),
            None,
        )

    def relationship(self, peer_id: NodeId) -> RelationshipState:
        """Derive the canonical directional relationship from owned state only."""
        outbound = peer_id in self.trusted
        inbound = peer_id in self.grants
        if outbound and self._auth_broken.get(peer_id):
            return RelationshipState.REPAIR_REQUIRED
        if outbound and inbound:
            return RelationshipState.BIDIRECTIONAL
        if outbound:
            return RelationshipState.OUTBOUND_TRUSTED
        if inbound:
            return RelationshipState.INBOUND_GRANTED
        pending = self.find_pending_for(peer_id)
        if pending is not None:
            return (
                RelationshipState.PENDING_OUTBOUND
                if pending.direction == "outbound"
                else RelationshipState.PENDING_INBOUND
            )
        return RelationshipState.UNPAIRED

    def classify_trusted_candidate(
        self,
        peer_id: NodeId,
        *,
        candidate_root_public_key: str | None,
        candidate_transport_fingerprint: str | None,
        candidate_transport_generation: int | None,
        candidate_transport_proof: str | None,
    ) -> RelationshipState:
        """Classify an already-trusted peer against fresh candidate material.

        The stored trusted root is authoritative; the candidate must prove
        possession of it. Identity is never trusted merely because the request
        claims the same ``NodeId`` or root string.
        """
        trusted = self.trusted.get(peer_id)
        if trusted is None:
            return self.relationship(peer_id)
        if not trusted.root_public_key:
            # Legacy trust without root material cannot prove continuity.
            return RelationshipState.IDENTITY_CONFLICT
        if candidate_root_public_key != trusted.root_public_key:
            return RelationshipState.IDENTITY_CONFLICT
        proven = bool(
            candidate_transport_generation is not None
            and candidate_transport_proof
            and candidate_transport_fingerprint
            and verify_transport_proof(
                peer_id,
                trusted.root_public_key,
                candidate_transport_generation,
                candidate_transport_fingerprint,
                candidate_transport_proof,
            )
        )
        if candidate_transport_fingerprint == trusted.transport_fingerprint:
            return (
                RelationshipState.REPAIR_REQUIRED
                if self._auth_broken.get(peer_id)
                else RelationshipState.OUTBOUND_TRUSTED
            )
        rotation = bool(
            proven
            and (
                trusted.transport_generation is None
                or candidate_transport_generation is not None
                and candidate_transport_generation > trusted.transport_generation
            )
        )
        if rotation:
            return (
                RelationshipState.REPAIR_REQUIRED
                if self._auth_broken.get(peer_id)
                else RelationshipState.OUTBOUND_TRUSTED
            )
        if proven:
            # Same identity proven, but the binding is stale or reused.
            return RelationshipState.REPAIR_REQUIRED
        return RelationshipState.IDENTITY_CONFLICT

    def mark_auth_failure(self, peer_id: NodeId) -> None:
        """Record that an established outbound credential failed authentication."""
        if peer_id in self.trusted:
            self._auth_broken[peer_id] = True

    def mark_auth_ok(self, peer_id: NodeId) -> None:
        """Record that the outbound credential authenticated successfully."""
        self._auth_broken.pop(peer_id, None)

    def is_auth_broken(self, peer_id: NodeId) -> bool:
        return bool(self._auth_broken.get(peer_id))

    def has_outbound_trust(self, peer_id: NodeId) -> bool:
        return peer_id in self.trusted

    def has_valid_relationship(self, peer_id: NodeId) -> bool:
        """Return whether either directional side has a relationship with peer."""
        return peer_id in self.trusted or any(
            g.caller_id == peer_id for g in self.grants.values()
        )

    def can_join_cluster(self, peer_id: NodeId) -> bool:
        """Return whether this peer has a directional relationship for membership."""
        return self.has_valid_relationship(peer_id)

    def has_relationship(self, peer_id: NodeId) -> bool:
        """Compatibility alias for the canonical relationship query."""
        return self.has_valid_relationship(peer_id)

    def can_call(self, peer_id: NodeId, permission: str) -> bool:
        peer = self.trusted.get(peer_id)
        return peer is not None and permission in peer.permissions

    def _active(
        self, transaction_id: str, *, now: float | None = None
    ) -> PendingPairing:
        transaction = self.pending.get(transaction_id)
        current = self._clock() if now is None else now
        if transaction is None or transaction.expires_at <= current:
            raise ValueError("pairing transaction is missing or expired")
        return transaction
