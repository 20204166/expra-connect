"""Directional pairing transactions, grants, and relationship projection."""

from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from .identity import NodeId


@dataclass(frozen=True, slots=True)
class PendingPairing:
    transaction_id: str
    peer_id: NodeId
    secret: str
    expires_at: float
    identity_fingerprint: str | None = None
    transport_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class TrustedPeer:
    peer_id: NodeId
    secret: str
    permissions: frozenset[str]
    identity_fingerprint: str | None = None
    transport_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class PeerGrant:
    caller_id: NodeId
    secret: str
    permissions: frozenset[str]
    identity_fingerprint: str | None = None
    transport_fingerprint: str | None = None


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

    def begin(
        self,
        peer_id: NodeId,
        secret: str | None = None,
        *,
        identity_fingerprint: str | None = None,
        transport_fingerprint: str | None = None,
    ) -> PendingPairing:
        transaction = PendingPairing(
            uuid.uuid4().hex,
            peer_id,
            secret or secrets.token_hex(32),
            self._clock() + self._ttl,
            identity_fingerprint,
            transport_fingerprint,
        )
        self.pending[transaction.transaction_id] = transaction
        return transaction

    def approve(self, transaction_id: str, permissions: frozenset[str]) -> PeerGrant:
        transaction = self._active(transaction_id)
        grant = PeerGrant(
            transaction.peer_id,
            transaction.secret,
            permissions,
            transaction.identity_fingerprint,
            transaction.transport_fingerprint,
        )
        self.grants[transaction.peer_id] = grant
        return grant

    def receive_request(
        self, caller_id: NodeId, transaction: PendingPairing
    ) -> PendingPairing:
        if transaction.expires_at < self._clock():
            raise ValueError("pairing transaction is expired")
        pending = PendingPairing(
            transaction.transaction_id,
            caller_id,
            transaction.secret,
            transaction.expires_at,
            transaction.identity_fingerprint,
            transaction.transport_fingerprint,
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
        )
        self.trusted[transaction.peer_id] = trusted
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
        ):
            raise ValueError("pairing fingerprint binding mismatch")
        trusted = TrustedPeer(
            peer_id,
            transaction.secret,
            grant.permissions,
            grant.identity_fingerprint,
            grant.transport_fingerprint,
        )
        self.trusted[peer_id] = trusted
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
        self.pending = {
            key: item for key, item in self.pending.items() if item.peer_id != peer_id
        }

    def has_relationship(self, peer_id: NodeId) -> bool:
        return peer_id in self.trusted or any(
            g.caller_id == peer_id for g in self.grants.values()
        )

    def can_call(self, peer_id: NodeId, permission: str) -> bool:
        peer = self.trusted.get(peer_id)
        return peer is not None and permission in peer.permissions

    def _active(
        self, transaction_id: str, *, now: float | None = None
    ) -> PendingPairing:
        transaction = self.pending.get(transaction_id)
        current = self._clock() if now is None else now
        if transaction is None or transaction.expires_at < current:
            raise ValueError("pairing transaction is missing or expired")
        return transaction
