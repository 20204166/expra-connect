"""Durable transport-generation continuity and rotation lifecycle."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from .identity import NodeId, NodeIdentity, verify_transport_proof
from .persistence import StateDataError, YamlStateStore


class RotationConflict(ValueError):
    """Raised when a second rotation is started before the first is resolved."""


class TransportStateError(ValueError):
    """Raised when transport continuity or durable state is invalid."""


@dataclass(frozen=True, slots=True)
class TransportGeneration:
    generation: int
    fingerprint: str
    proof: str


class TransportGenerationManager:
    """Own signed transport generations and their durable overlap state."""

    VERSION = 2

    def __init__(
        self,
        identity: NodeIdentity,
        *,
        store: YamlStateStore | None = None,
        persist: Callable[[dict[str, object]], bool | None] | None = None,
        clock: Callable[[], float] = time.time,
        grace_seconds: float = 300.0,
    ) -> None:
        self.identity = identity
        self._store = store
        self._persist_callback = persist
        self._clock = clock
        self._grace_seconds = grace_seconds
        self._current: TransportGeneration | None = None
        self._accepted: dict[int, tuple[TransportGeneration, float | None]] = {}
        self._next: TransportGeneration | None = None
        if store is not None and store.exists():
            self._load(store.load())

    @property
    def current(self) -> TransportGeneration:
        if self._current is None:
            raise TransportStateError("transport generation is not initialized")
        return self._current

    @property
    def next(self) -> TransportGeneration | None:
        return self._next

    @property
    def current_generation(self) -> int | None:
        return self._current.generation if self._current is not None else None

    def initialize(self, fingerprint: str) -> TransportGeneration:
        if self._current is not None:
            if self.current.fingerprint != fingerprint:
                raise TransportStateError("initial transport generation already exists")
            return self.current
        candidate = self._generation(1, fingerprint)
        self._commit(candidate, {}, None)
        return candidate

    def prepare(self, fingerprint: str) -> TransportGeneration:
        if self._current is None:
            return self.initialize(fingerprint)
        if self._next is not None:
            raise RotationConflict("a transport rotation is already pending")
        candidate = self._generation(self._highest_generation() + 1, fingerprint)
        self._commit(self._current, self._accepted, candidate)
        return candidate

    def activate(self, generation: int) -> TransportGeneration:
        if self._next is None or self._next.generation != generation:
            raise TransportStateError("generation is not pending activation")
        accepted = dict(self._accepted)
        accepted[self.current.generation] = (
            self.current,
            self._clock() + self._grace_seconds,
        )
        self._commit(self._next, accepted, None)
        return self.current

    def retire(self, generation: int) -> None:
        if self._current is not None and generation == self.current.generation:
            raise TransportStateError("cannot retire current transport generation")
        accepted = dict(self._accepted)
        accepted.pop(generation, None)
        self._commit(self.current, accepted, self._next)

    def rollback(self) -> None:
        if self._next is None:
            raise TransportStateError("no transport rotation is pending")
        self._commit(self.current, self._accepted, None)

    def rollback_active(self) -> TransportGeneration:
        """Restore the immediately previous generation after activation failure."""
        if not self._accepted:
            raise TransportStateError("no previous transport generation is retained")
        previous_generation = max(self._accepted)
        previous, _expiry = self._accepted[previous_generation]
        accepted = {
            generation: value
            for generation, value in self._accepted.items()
            if generation != previous_generation
        }
        self._commit(previous, accepted, None)
        return previous

    def accepted(self, fingerprint: str) -> bool:
        if self._current is not None and self.current.fingerprint == fingerprint:
            return True
        now = self._clock()
        return any(
            item.fingerprint == fingerprint and (expiry is None or expiry >= now)
            for item, expiry in self._accepted.values()
        )

    def accept_remote(
        self,
        node_id: NodeId,
        generation: int,
        fingerprint: str,
        proof: str,
        *,
        root_public_key: str | None = None,
    ) -> TransportGeneration:
        if (
            node_id != self.identity.node_id
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
        ):
            raise TransportStateError("transport identity binding mismatch")
        if not verify_transport_proof(
            node_id,
            root_public_key or self.identity.root_public_key,
            generation,
            fingerprint,
            proof,
        ):
            raise TransportStateError("invalid transport continuity proof")
        return TransportGeneration(generation, fingerprint, proof)

    def _generation(self, generation: int, fingerprint: str) -> TransportGeneration:
        if not isinstance(fingerprint, str) or not fingerprint:
            raise TransportStateError("transport fingerprint is required")
        return TransportGeneration(
            generation,
            fingerprint,
            self.identity.sign_transport_proof(generation, fingerprint),
        )

    def _highest_generation(self) -> int:
        generations = [g for g in self._accepted]
        if self._current is not None:
            generations.append(self.current.generation)
        if self._next is not None:
            generations.append(self._next.generation)
        return max(generations, default=0)

    def _commit(
        self,
        current: TransportGeneration,
        accepted: dict[int, tuple[TransportGeneration, float | None]],
        next_generation: TransportGeneration | None,
    ) -> None:
        old = self._snapshot()
        self._current, self._accepted, self._next = current, accepted, next_generation
        try:
            result = self._save()
            if result is False:
                raise OSError("transport state persistence was rejected")
        except Exception as error:
            self._restore(old)
            raise TransportStateError("transport state was not persisted") from error

    def _save(self) -> bool | None:
        document = self._to_dict()
        if self._persist_callback is not None:
            return self._persist_callback(document)
        if self._store is not None:
            self._store.save(document)
        return None

    def _to_dict(self) -> dict[str, object]:
        def encode(item: TransportGeneration) -> dict[str, object]:
            return {
                "generation": item.generation,
                "fingerprint": item.fingerprint,
                "proof": item.proof,
            }

        return {
            "version": self.VERSION,
            "current": encode(self.current),
            "accepted": [
                {**encode(item), "expires_at": expiry}
                for item, expiry in self._accepted.values()
            ],
            "next": encode(self._next) if self._next is not None else None,
        }

    def _load(self, document: dict[str, object]) -> None:
        try:
            version = document.get("version")
            if version == 1:
                fingerprint = document["fingerprint"]
                if not isinstance(fingerprint, str) or not fingerprint:
                    raise ValueError
                self._current = self._generation(1, fingerprint)
                return
            if version != self.VERSION:
                raise ValueError
            current = self._decode(document["current"])
            raw_accepted = document["accepted"]
            raw_next = document.get("next")
            if not isinstance(raw_accepted, list):
                raise TypeError
            accepted: dict[int, tuple[TransportGeneration, float | None]] = {}
            for raw in raw_accepted:
                if not isinstance(raw, dict):
                    raise TypeError
                item = self._decode(raw)
                if item.generation in accepted or item.generation >= current.generation:
                    raise ValueError
                expiry = raw.get("expires_at")
                if expiry is not None and (
                    not isinstance(expiry, (int, float))
                    or isinstance(expiry, bool)
                    or not math.isfinite(float(expiry))
                ):
                    raise ValueError
                accepted[item.generation] = (item, expiry)
            next_generation = None if raw_next is None else self._decode(raw_next)
            if next_generation is not None and (
                next_generation.generation <= current.generation
                or next_generation.generation in accepted
            ):
                raise ValueError
            self._current = current
            self._accepted = accepted
            self._next = next_generation
        except (KeyError, OverflowError, TypeError, ValueError) as error:
            raise StateDataError("transport state is malformed") from error

    def _decode(self, raw: object) -> TransportGeneration:
        if not isinstance(raw, dict):
            raise TypeError
        generation = raw["generation"]
        fingerprint = raw["fingerprint"]
        proof = raw["proof"]
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
            or not isinstance(fingerprint, str)
            or not fingerprint
            or not isinstance(proof, str)
            or not self.identity.verify_transport_proof(generation, fingerprint, proof)
        ):
            raise ValueError
        return TransportGeneration(generation, fingerprint, proof)

    def _snapshot(
        self,
    ) -> tuple[
        TransportGeneration | None,
        dict[int, tuple[TransportGeneration, float | None]],
        TransportGeneration | None,
    ]:
        return self._current, dict(self._accepted), self._next

    def _restore(
        self,
        snapshot: tuple[
            TransportGeneration | None,
            dict[int, tuple[TransportGeneration, float | None]],
            TransportGeneration | None,
        ],
    ) -> None:
        self._current, self._accepted, self._next = snapshot


__all__ = [
    "RotationConflict",
    "TransportGeneration",
    "TransportGenerationManager",
    "TransportStateError",
]
