"""Low-level logical session registry: fencing, expiry, bounds, concurrency."""

import threading
import unittest
from collections.abc import Callable
from typing import Any

from expra_connect.identity import NodeId
from expra_connect.session import (
    DEFAULT_MAX_GENERATIONS_PER_SESSION,
    DEFAULT_MAX_SESSIONS,
    DEFAULT_SESSION_TTL_SECONDS,
    LogicalSessionRegistry,
)
from expra_connect.wire_protocol import RemoteAuthError, RemoteRequest


def _request(
    *,
    session_id: str | None = None,
    resume: bool = False,
    caller: str | None = "caller",
    node: str = "target",
) -> RemoteRequest:
    return RemoteRequest(
        node_id=NodeId(node),
        caller_node_id=NodeId(caller) if caller is not None else None,
        op="ping",
        params={},
        request_id="request",
        nonce="nonce",
        timestamp=0.0,
        session_id=session_id,
        resume=resume,
    )


def _constant_clock(value: float) -> Callable[[], float]:
    def clock() -> float:
        return value

    return clock


def _fixed_id() -> str:
    return "session-1"


def _make_registry(**kwargs: Any) -> LogicalSessionRegistry:
    return LogicalSessionRegistry(**kwargs)


def _registry(
    *,
    now: list[float],
    ttl: float = 100.0,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
    max_generations: int = DEFAULT_MAX_GENERATIONS_PER_SESSION,
    ids: list[str] | None = None,
) -> LogicalSessionRegistry:
    factory: Callable[[], str] | None = None
    if ids is not None:
        queue = list(ids)

        def next_id() -> str:
            return queue.pop(0)

        factory = next_id

    return LogicalSessionRegistry(
        clock=lambda: now[0],
        ttl_seconds=ttl,
        max_sessions=max_sessions,
        max_generations_per_session=max_generations,
        session_id_factory=factory,
    )


class ConstructorValidationTests(unittest.TestCase):
    def test_invalid_ttl_is_rejected(self) -> None:
        bad_types: tuple[object, ...] = (True, "100")
        for bad_type in bad_types:
            with self.subTest(ttl=bad_type), self.assertRaises(TypeError):
                _make_registry(ttl_seconds=bad_type)
        for bad_value in (float("nan"), float("inf"), float("-inf"), 0.0, -1.0):
            with self.subTest(ttl=bad_value), self.assertRaises(ValueError):
                _make_registry(ttl_seconds=bad_value)

    def test_invalid_resource_limits_are_rejected(self) -> None:
        with self.assertRaises(TypeError):
            _make_registry(max_sessions=True)
        with self.assertRaises(TypeError):
            _make_registry(max_generations_per_session=True)
        for value in (0, -1):
            with self.subTest(max_sessions=value), self.assertRaises(ValueError):
                _make_registry(max_sessions=value)
            with self.subTest(max_generations=value), self.assertRaises(ValueError):
                _make_registry(max_generations_per_session=value)

    def test_defaults_are_sane(self) -> None:
        self.assertGreater(DEFAULT_SESSION_TTL_SECONDS, 0)
        self.assertGreaterEqual(DEFAULT_MAX_SESSIONS, 1)
        self.assertGreaterEqual(DEFAULT_MAX_GENERATIONS_PER_SESSION, 1)


class NewSessionTests(unittest.TestCase):
    def test_new_session_is_issued_and_reused(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ids=["session-1"])
        session_id = registry.authenticate(_request(), connection_generation=None)
        self.assertEqual(session_id, "session-1")
        self.assertEqual(
            registry.authenticate(
                _request(session_id=session_id), connection_generation=None
            ),
            session_id,
        )

    def test_no_generation_is_none_and_not_a_string_sentinel(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ids=["session-1"])
        session_id = registry.authenticate(_request(), connection_generation=None)
        # A literal "memory" generation is a distinct opaque token, not absence.
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(
                _request(session_id=session_id), connection_generation="memory"
            )

    def test_empty_generation_is_rejected_not_coerced(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ids=["session-1"])
        session_id = registry.authenticate(_request(), connection_generation=None)
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(
                _request(session_id=session_id), connection_generation=""
            )

    def test_invalid_session_ids_are_rejected(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ids=["session-1"])
        registry.authenticate(_request(), connection_generation=None)
        for bad in ("", " ", "x" * 129):
            with self.subTest(session_id=bad), self.assertRaises(RemoteAuthError):
                registry.authenticate(
                    _request(session_id=bad), connection_generation=None
                )

    def test_forced_session_id_collision_does_not_overwrite(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ids=["dup", "dup", "fresh"])
        first = registry.authenticate(
            _request(caller="caller"), connection_generation=None
        )
        second = registry.authenticate(
            _request(caller="caller"), connection_generation=None
        )
        self.assertEqual(first, "dup")
        self.assertEqual(second, "fresh")
        # The original session must still be owned by its creator and usable.
        self.assertEqual(
            registry.authenticate(
                _request(session_id="dup", caller="caller"),
                connection_generation=None,
            ),
            "dup",
        )


class ExpiryTests(unittest.TestCase):
    def test_unknown_and_expired_sessions_fail_identically(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ttl=100.0, ids=["session-1"])
        session_id = registry.authenticate(_request(), connection_generation=None)
        now[0] = 100.0
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(
                _request(session_id=session_id), connection_generation=None
            )
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(
                _request(session_id="unknown"), connection_generation=None
            )

    def test_absolute_ttl_does_not_slide_on_use(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ttl=100.0, ids=["session-1"])
        session_id = registry.authenticate(_request(), connection_generation=None)
        now[0] = 50.0
        registry.authenticate(
            _request(session_id=session_id), connection_generation=None
        )
        now[0] = 100.0
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(
                _request(session_id=session_id), connection_generation=None
            )

    def test_absolute_ttl_does_not_slide_on_resume(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ttl=100.0, ids=["session-1"])
        session_id = registry.authenticate(_request(), connection_generation="a")
        now[0] = 50.0
        registry.authenticate(
            _request(session_id=session_id, resume=True),
            connection_generation="b",
        )
        now[0] = 100.0
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(
                _request(session_id=session_id, resume=True),
                connection_generation="c",
            )

    def test_none_generation_still_checks_expiry(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ttl=100.0, ids=["session-1"])
        session_id = registry.authenticate(_request(), connection_generation=None)
        now[0] = 101.0
        with self.assertRaises(RemoteAuthError):
            registry.assert_current(session_id, None)

    def test_invalid_clock_reading_fails_closed(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(clock=value):
                registry = LogicalSessionRegistry(
                    clock=_constant_clock(value),
                    ttl_seconds=100.0,
                    session_id_factory=_fixed_id,
                )
                with self.assertRaises(RemoteAuthError):
                    registry.authenticate(_request(), connection_generation=None)


class OwnerBindingTests(unittest.TestCase):
    def test_owner_mismatch_is_rejected(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ids=["session-1"])
        session_id = registry.authenticate(
            _request(caller="caller"), connection_generation="a"
        )
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(
                _request(session_id=session_id, caller="attacker"),
                connection_generation="a",
            )

    def test_owner_mismatch_resume_is_side_effect_free(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ids=["session-1"])
        session_id = registry.authenticate(
            _request(caller="caller"), connection_generation="a"
        )
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(
                _request(session_id=session_id, caller="attacker", resume=True),
                connection_generation="b",
            )
        # Original session remains owned by its creator on its original generation.
        registry.authenticate(
            _request(session_id=session_id, caller="caller"),
            connection_generation="a",
        )


class GenerationFencingTests(unittest.TestCase):
    def test_same_generation_resume_is_idempotent(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ids=["session-1"])
        session_id = registry.authenticate(_request(), connection_generation="a")
        registry.authenticate(
            _request(session_id=session_id, resume=True), connection_generation="a"
        )
        self.assertEqual(registry._sessions[session_id].retired, set())

    def test_changed_generation_without_resume_is_rejected(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ids=["session-1"])
        session_id = registry.authenticate(_request(), connection_generation="a")
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(
                _request(session_id=session_id), connection_generation="b"
            )

    def test_resume_migrates_and_permanently_retires_old_generation(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ids=["session-1"])
        session_id = registry.authenticate(_request(), connection_generation="a")
        registry.authenticate(
            _request(session_id=session_id, resume=True), connection_generation="b"
        )
        table = registry._sessions[session_id]
        self.assertEqual(table.active_generation, "b")
        self.assertEqual(table.retired, {"a"})
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(
                _request(session_id=session_id, resume=True), connection_generation="a"
            )
        with self.assertRaises(RemoteAuthError):
            registry.assert_current(session_id, "a")

    def test_aba_resurrection_is_denied(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ids=["session-1"])
        session_id = registry.authenticate(_request(), connection_generation="a")
        registry.authenticate(
            _request(session_id=session_id, resume=True), connection_generation="b"
        )
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(
                _request(session_id=session_id, resume=True), connection_generation="a"
            )

    def test_retired_generation_request_is_side_effect_free(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ids=["session-1"])
        session_id = registry.authenticate(_request(), connection_generation="a")
        registry.authenticate(
            _request(session_id=session_id, resume=True), connection_generation="b"
        )
        before = registry._sessions[session_id]
        snapshot = (before.active_generation, set(before.retired), before.expires_at)
        now[0] = 1.0
        for resume in (False, True):
            with self.assertRaises(RemoteAuthError):
                registry.authenticate(
                    _request(session_id=session_id, resume=resume),
                    connection_generation="a",
                )
        after = registry._sessions[session_id]
        self.assertEqual(
            (after.active_generation, set(after.retired), after.expires_at), snapshot
        )

    def test_retired_generation_growth_is_bounded_fail_closed(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ids=["session-1"], max_generations=2)
        session_id = registry.authenticate(_request(), connection_generation="a")
        registry.authenticate(
            _request(session_id=session_id, resume=True), connection_generation="b"
        )
        registry.authenticate(
            _request(session_id=session_id, resume=True), connection_generation="c"
        )
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(
                _request(session_id=session_id, resume=True), connection_generation="d"
            )
        # The session is invalidated; no forgotten generation becomes reusable.
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(
                _request(session_id=session_id, resume=True), connection_generation="b"
            )

    def test_concurrent_resume_keeps_one_active_and_all_previous_retired(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ids=["session-1"])
        session_id = registry.authenticate(_request(), connection_generation="a")
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def resume(generation: str) -> None:
            barrier.wait()
            try:
                registry.authenticate(
                    _request(session_id=session_id, resume=True),
                    connection_generation=generation,
                )
            except BaseException as error:  # noqa: BLE001 - recorded for assertion
                errors.append(error)

        threads = [
            threading.Thread(target=resume, args=("b",)),
            threading.Thread(target=resume, args=("c",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        table = registry._sessions[session_id]
        self.assertIn(table.active_generation, {"b", "c"})
        self.assertEqual(table.retired, {"a", "b", "c"} - {table.active_generation})
        for generation in ("a", "b", "c"):
            if generation == table.active_generation:
                registry.assert_current(session_id, generation)
            else:
                with self.assertRaises(RemoteAuthError):
                    registry.assert_current(session_id, generation)


class BoundsTests(unittest.TestCase):
    def test_active_sessions_are_bounded(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ttl=100.0, max_sessions=2, ids=["s1", "s2"])
        registry.authenticate(_request(), connection_generation=None)
        registry.authenticate(_request(), connection_generation=None)
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(_request(), connection_generation=None)

    def test_expired_sessions_are_pruned_before_capacity_failure(self) -> None:
        now = [0.0]
        registry = _registry(now=now, ttl=100.0, max_sessions=1, ids=["s1", "s2"])
        first = registry.authenticate(_request(), connection_generation=None)
        now[0] = 200.0
        second = registry.authenticate(_request(), connection_generation=None)
        self.assertNotEqual(first, second)
        with self.assertRaises(RemoteAuthError):
            registry.authenticate(
                _request(session_id=first), connection_generation=None
            )


if __name__ == "__main__":
    unittest.main()
