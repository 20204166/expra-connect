"""Target and initiator peer flows for the development harness."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Event
from typing import Any, cast

from expra_connect import ConnectConfig, ConnectRuntime, NodeId, __version__
from expra_connect.wire_protocol import RemoteAuthorizationError

from .events import CAPABILITY, discovery_event, write_event
from .surfaces import (
    SURFACE_SYNC_CAPABILITY,
    SURFACE_SYNC_TIMEOUT,
    _retry_surface_success,
    approve_surface_pairing,
    record_surface_result,
    register_harness_surfaces,
    register_surface_sync,
)

__all__ = [
    "CAPABILITY",
    "SURFACE_SYNC_CAPABILITY",
    "_TrackedPairingCallback",
    "_approve_capability_pairing",
    "_candidate_values",
    "_diagnostics_values",
    "_explicit_approval_decision",
    "_retry_surface_success",
    "_runtime",
    "_surface_success_or_denial",
    "approve_surface_pairing",
    "cleanup_runtime",
    "connect_bidirectionally",
    "exercise_remote_surfaces",
    "record_surface_result",
    "register_harness_surfaces",
    "register_surface_sync",
    "run_initiator",
    "run_target",
    "wait_for_matching_peer",
    "wait_for_peer",
]


class _TrackedPairingCallback:
    def __init__(
        self, callback: Callable[[Any], bool], capability_peers: set[NodeId]
    ) -> None:
        self._callback = callback
        self.capability_peers = capability_peers

    def __call__(self, request: Any) -> bool:
        return self._callback(request)


def _status_state(status: Any) -> Any:
    state = getattr(status, "state", None)
    return getattr(state, "value", state)


def _reallow_capability_after_rotation(
    runtime: ConnectRuntime, peer_ids: set[NodeId]
) -> None:
    """Re-apply only the harness's explicit grants after transport restart."""
    for peer_id in peer_ids:
        runtime.sharing.allow(peer_id, CAPABILITY)


def _retry_shared_capability(operation: Any, timeout: float = 5.0) -> Any:
    """Wait briefly for a target harness to re-apply its explicit grant."""
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        try:
            return operation()
        except RemoteAuthorizationError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.1, remaining))


def _approve_capability_pairing(
    runtime: ConnectRuntime,
    report: Path,
    request: Any,
    capability_peers: set[NodeId],
) -> bool:
    """Approve the shared capability and remember the caller for re-allowance."""
    permissions = sorted(
        getattr(permission, "value", permission)
        for permission in getattr(request, "permissions", ())
    )
    write_event(
        report,
        "pairing_request",
        caller_node_id=getattr(getattr(request, "caller_node_id", None), "value", None),
        permissions=permissions,
    )
    runtime.sharing.allow(request.caller_node_id, CAPABILITY)
    capability_peers.add(request.caller_node_id)
    return True


def _explicit_approval_decision(
    runtime: ConnectRuntime,
    report: Path,
    request: Any,
    capability_peers: set[NodeId],
    *,
    approval_file: Path,
    approval_wait: float,
) -> bool:
    """Wait for an operator to create the approval file before allowing.

    This is the harness's explicit host-acceptance path: trust is only extended
    after an out-of-band trigger, and a missing trigger fails closed.
    """
    caller = request.caller_node_id
    permissions = sorted(
        getattr(permission, "value", permission)
        for permission in getattr(request, "permissions", ())
    )
    write_event(
        report,
        "pairing_pending",
        caller_node_id=getattr(caller, "value", None),
        permissions=permissions,
    )
    deadline = time.monotonic() + max(approval_wait, 0.0)
    while not approval_file.exists():
        if time.monotonic() >= deadline:
            write_event(
                report,
                "pairing_denied",
                caller_node_id=getattr(caller, "value", None),
            )
            return False
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    try:
        approval_file.unlink()
    except OSError:
        pass
    write_event(
        report,
        "pairing_approved",
        caller_node_id=getattr(caller, "value", None),
    )
    runtime.sharing.allow(caller, CAPABILITY)
    capability_peers.add(caller)
    return True


@contextmanager
def cleanup_runtime(runtime: ConnectRuntime) -> Iterator[ConnectRuntime]:
    """Provide the common shutdown boundary for later runtime stages."""
    try:
        yield runtime
    finally:
        runtime.shutdown()


def _candidate_values(candidate: Any) -> dict[str, Any]:
    endpoints = getattr(candidate, "endpoint_candidates", ()) or ()
    endpoint = (
        endpoints[0] if isinstance(endpoints, (list, tuple)) and endpoints else None
    )
    addresses = getattr(candidate, "addresses", ()) or ()
    if not isinstance(addresses, (list, tuple)):
        addresses = ()
    address = getattr(endpoint, "address", None) or next(iter(addresses), None)
    port = getattr(endpoint, "port", None) or getattr(candidate, "port", None)
    source = getattr(endpoint, "source", None)
    if source is None:
        source = "discovery"
    return {
        "address": address,
        "port": port,
        "source": getattr(source, "value", source),
    }


def _write_started(
    report: Path, role: str, runtime: ConnectRuntime, status: Any
) -> None:
    identity = runtime.identity
    if identity is None:
        raise RuntimeError("runtime identity is unavailable")
    state = getattr(status, "state", None)
    write_event(
        report,
        "started",
        role=role,
        version=__version__,
        state=getattr(state, "value", state),
        node_id=identity.node_id.value,
        discovery_started=getattr(status, "discovery_started", None),
        discovery_disabled=getattr(status, "discovery_disabled", None),
        discovery_reason=getattr(status, "discovery_reason", None),
        bound_host=getattr(status, "bound_host", None),
        bound_port=getattr(status, "bound_port", None),
        tls_fingerprint=getattr(status, "tls_fingerprint", None),
    )


def wait_for_peer(
    runtime: ConnectRuntime,
    peer_id: str | None,
    timeout: float,
    *,
    generation_not: int | None = None,
    fingerprint_not: str | None = None,
) -> Any | None:
    """Wait for a matching public discovery candidate without fixed delays."""
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        candidate = next(
            (
                peer
                for peer in runtime.peers
                if peer_id is None or peer.stable_id == peer_id
                if generation_not is None
                or getattr(peer, "transport_generation", None) != generation_not
                if fingerprint_not is None
                or getattr(peer, "transport_fingerprint", None) != fingerprint_not
            ),
            None,
        )
        if candidate is not None:
            return candidate
        if time.monotonic() >= deadline:
            return None
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def wait_for_matching_peer(
    runtime: ConnectRuntime, peer_id: str | None, timeout: float
) -> Any | None:
    """Wait for a discovered candidate matching the requested peer, if given."""
    return wait_for_peer(runtime, peer_id, timeout)


def connect_bidirectionally(
    runtime: ConnectRuntime,
    candidate: Any,
    *,
    on_paired: Any | None = None,
) -> Any:
    """Pair and connect to a discovered peer using the public runtime APIs."""
    peer_id = NodeId(candidate.stable_id)
    trusted = runtime.pairing.trusted.get(peer_id) if runtime.pairing else None
    if trusted is None:
        runtime.pair_peer(peer_id)
        if on_paired is not None:
            on_paired()
    runtime.sharing.allow(peer_id, SURFACE_SYNC_CAPABILITY)
    provider = runtime.connect_peer(peer_id)
    runtime.sharing.allow(peer_id, SURFACE_SYNC_CAPABILITY)
    return provider


def exercise_remote_surfaces(
    runtime: ConnectRuntime,
    provider: Any,
    peer_id: NodeId,
    report: Path,
    *,
    ready_events: dict[str, Event] | None = None,
) -> None:
    """Exercise each remote surface through every explicit authorization stage."""
    surface_ids = ("desktop", "desktop/settings", "device/status")

    def denied(access: str, operation: Any, *, retry_success: bool = False) -> None:
        deadline = time.monotonic() + SURFACE_SYNC_TIMEOUT if retry_success else None
        while True:
            try:
                operation()
            except RemoteAuthorizationError:
                write_event(
                    report,
                    "surface_denied",
                    surface_id=surface_id,
                    access=access,
                    outcome="denied",
                    error_type=RemoteAuthorizationError.__name__,
                )
                return
            except Exception as exc:
                write_event(report, "error", error_type=type(exc).__name__)
                raise
            if deadline is not None and time.monotonic() < deadline:
                time.sleep(0.1)
                continue
            error = AssertionError(f"surface {access} unexpectedly succeeded")
            write_event(report, "error", error_type=type(error).__name__)
            raise error

    def succeeded(access: str, operation: Any) -> None:
        try:
            _retry_surface_success(operation)
        except Exception as exc:
            write_event(report, "error", error_type=type(exc).__name__)
            raise
        else:
            record_surface_result(report, surface_id, access, "success")

    def synchronize(phase: str) -> None:
        if ready_events is None:
            return
        sync_key = f"{surface_id}:{phase}"
        ready_events.setdefault(sync_key, Event()).set()

        def request_sync() -> Any:
            ready = provider.request_shared(
                SURFACE_SYNC_CAPABILITY,
                {
                    "phase": phase,
                    "surface_id": surface_id,
                    "sync_key": sync_key,
                },
            )
            if not isinstance(ready, dict) or ready.get("ready") is not True:
                raise RemoteAuthorizationError("surface synchronization is not ready")
            return ready

        _retry_surface_success(request_sync)

    for surface_id in surface_ids:

        def _read(sid: str = surface_id) -> Any:
            return provider.read_surface(sid)

        def _review(sid: str = surface_id) -> Any:
            return provider.review_surface(sid)

        def _action(sid: str = surface_id) -> Any:
            return provider.invoke_surface_action(sid, "save")

        denied("read", _read)
        denied("review", _review)
        denied("action", _action)

        if ready_events is not None:
            synchronize("initial_denials")

        runtime.grant_surface_access(peer_id, surface_id, access="read")
        succeeded("read", _read)
        synchronize("read_success")
        denied("review", _review)
        synchronize("review_denied")

        runtime.grant_surface_access(peer_id, surface_id, access="review")
        succeeded("review", _review)
        synchronize("review_success")
        denied("action", _action)
        synchronize("action_denied")

        runtime.grant_surface_access(peer_id, surface_id, access="action")
        succeeded("action", _action)
        synchronize("action_success")

        runtime.stop_surface_share(peer_id, surface_id)
        synchronize("stopped_denied")
        denied("read", _read, retry_success=True)

        runtime.grant_surface_access(peer_id, surface_id, access="read")
        runtime.revoke_surface_access(peer_id, surface_id, access="read")
        synchronize("revoked_denied")
        denied("read", _read, retry_success=True)


def _surface_success_or_denial(
    report: Path,
    surface_id: str,
    access: str,
    operation: Any,
    expected: str,
) -> None:
    try:
        operation()
    except RemoteAuthorizationError:
        if expected == "denied":
            write_event(
                report,
                "surface_denied",
                surface_id=surface_id,
                access=access,
                outcome="denied",
                error_type=RemoteAuthorizationError.__name__,
            )
            return
        write_event(
            report,
            "error",
            error_type=RemoteAuthorizationError.__name__,
        )
        raise
    except Exception as exc:
        write_event(report, "error", error_type=type(exc).__name__)
        raise
    if expected == "success":
        record_surface_result(report, surface_id, access, "success")
        return
    error = AssertionError(f"surface {access} unexpectedly succeeded")
    write_event(report, "error", error_type=type(error).__name__)
    raise error


def _exercise_reconnected_surface(
    runtime: ConnectRuntime, provider: Any, peer_id: NodeId, report: Path
) -> None:
    """Re-grant only read access, then prove the other levels remain denied."""
    surface_id = "desktop"
    runtime.grant_surface_access(peer_id, surface_id, access="read")
    _surface_success_or_denial(
        report, surface_id, "read", lambda: provider.read_surface(surface_id), "success"
    )
    _surface_success_or_denial(
        report,
        surface_id,
        "review",
        lambda: provider.review_surface(surface_id),
        "denied",
    )
    _surface_success_or_denial(
        report,
        surface_id,
        "action",
        lambda: provider.invoke_surface_action(surface_id, "save"),
        "denied",
    )


def _verify_bidirectional_revocation(
    provider: Any, peer_id: NodeId, report: Path
) -> None:
    provider.revoke_self()
    write_event(report, "self_revoked", peer_id=peer_id.value)
    _surface_success_or_denial(
        report,
        "desktop",
        "read",
        lambda: provider.read_surface("desktop"),
        "denied",
    )


def _restart_bidirectional_session(
    args: Any,
    runtime: ConnectRuntime,
    peer_id: NodeId,
    runtime_factory: Any,
) -> tuple[ConnectRuntime, Any]:
    """Restore trust but require a fresh explicit session surface grant."""
    runtime.shutdown()
    runtime = runtime_factory()
    register_harness_surfaces(runtime)
    status = runtime.start()
    if _status_state(status) != "started":
        raise RuntimeError("runtime did not restart")
    restored = runtime.pairing and runtime.pairing.trusted.get(peer_id)
    if restored is None:
        raise RuntimeError("trusted peer was not restored")
    write_event(args.report, "restored_trust", peer_id=peer_id.value)
    provider = runtime.connect_peer(peer_id)
    _surface_success_or_denial(
        args.report,
        "desktop",
        "read",
        lambda: provider.read_surface("desktop"),
        "denied",
    )
    runtime.grant_surface_access(peer_id, "desktop", access="read")
    _surface_success_or_denial(
        args.report,
        "desktop",
        "read",
        lambda: provider.read_surface("desktop"),
        "success",
    )
    return runtime, provider


def _diagnostics_values(diagnostics: Any) -> dict[str, Any]:
    if not isinstance(diagnostics, dict):
        return {}
    return {
        "generation": diagnostics.get("generation"),
        "routes_count": len(diagnostics.get("routes", ()))
        if isinstance(diagnostics.get("routes"), (list, tuple))
        else None,
        "connections_count": len(diagnostics.get("connections", ()))
        if isinstance(diagnostics.get("connections"), (list, tuple))
        else None,
    }


def run_target(args: Any, runtime: ConnectRuntime) -> int:
    """Start a target, expose the test capability, and remain available."""
    bidirectional = getattr(args, "bidirectional_surfaces", False)

    configured_callback = getattr(runtime.config, "on_pairing_request", None)
    configured_peers = getattr(configured_callback, "capability_peers", None)
    capability_peers = configured_peers if isinstance(configured_peers, set) else set()

    def reallow_capability_after_rotation() -> None:
        for peer_id in capability_peers:
            runtime.sharing.allow(peer_id, CAPABILITY)

    surface_ready = {
        surface_id: Event()
        for surface_id in ("desktop", "desktop/settings", "device/status")
    }

    def approve_pairing(request: Any) -> bool:
        return _approve_capability_pairing(
            runtime, args.report, request, capability_peers
        )

    runtime.sharing.register(
        CAPABILITY,
        lambda peer_id, params: {
            "ok": True,
            "peer_id": peer_id.value,
            "params": params,
        },
    )
    try:
        if bidirectional:
            register_harness_surfaces(runtime)
            register_surface_sync(runtime, surface_ready)
        if not getattr(args, "explicit_approval", False):
            cast(Any, runtime.config).on_pairing_request = (
                approve_surface_pairing if bidirectional else approve_pairing
            )
    except FrozenInstanceError:
        # The real public config is frozen; its callback is installed at build time.
        pass
    try:
        status = runtime.start()
        _write_started(args.report, "target", runtime, status)
        if _status_state(status) != "started":
            raise RuntimeError("runtime did not start")
        write_event(args.report, "target_ready")
        candidate: Any | None = None
        if bidirectional:
            candidate = wait_for_matching_peer(
                runtime, getattr(args, "peer_id", None), args.wait
            )
            if candidate is None:
                write_event(
                    args.report, "discovery_timeout", peers_count=len(runtime.peers)
                )
                return 2
            runtime.sharing.allow(NodeId(candidate.stable_id), SURFACE_SYNC_CAPABILITY)
            provider = connect_bidirectionally(
                runtime,
                candidate,
                on_paired=lambda: write_event(
                    args.report, "reverse_paired", outcome="success"
                ),
            )
            write_event(args.report, "reverse_connected", outcome="success")
            exercise_remote_surfaces(
                runtime,
                provider,
                NodeId(candidate.stable_id),
                args.report,
                ready_events=surface_ready,
            )
        rotate_after = getattr(args, "rotate_after", None)
        rotation_deadline = (
            time.monotonic() + max(rotate_after, 0.0)
            if rotate_after is not None
            else None
        )

        def rotate_once() -> None:
            runtime.rotate_transport()
            if bidirectional and candidate is not None:
                runtime.grant_surface_access(
                    NodeId(candidate.stable_id), "desktop", access="read"
                )
            else:
                reallow_capability_after_rotation()
            generations = runtime.transport_generations
            write_event(
                args.report,
                "rotated",
                generation=(
                    generations.current_generation if generations is not None else None
                ),
            )

        rotated = False
        deadline = time.monotonic() + max(args.wait, 0.0)
        while time.monotonic() < deadline:
            if (
                rotation_deadline is not None
                and not rotated
                and time.monotonic() >= rotation_deadline
            ):
                rotate_once()
                rotated = True
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        if rotation_deadline is not None and not rotated:
            rotate_once()
        return 0
    except Exception as error:  # noqa: BLE001 - report only the exception type
        write_event(args.report, "error", error_type=type(error).__name__)
        return 1
    finally:
        runtime.shutdown()


def _reconnect_after_rotation(
    runtime: ConnectRuntime,
    peer_id: NodeId,
    candidate: Any,
    rotation_wait: float,
    report: Path,
) -> Any:
    """Wait for the rotated peer, prove the generation changed, and reconnect."""
    write_event(
        report,
        "waiting_for_rotated_peer",
        peer_id=peer_id.value,
        timeout=rotation_wait,
    )
    previous_generation = getattr(candidate, "transport_generation", None)
    previous_fingerprint = getattr(candidate, "transport_fingerprint", None)
    rotated_candidate = wait_for_peer(
        runtime,
        peer_id.value,
        rotation_wait,
        generation_not=previous_generation,
        fingerprint_not=previous_fingerprint,
    )
    if rotated_candidate is None:
        raise TimeoutError("rotated peer was not rediscovered")
    if getattr(rotated_candidate, "transport_generation", None) == previous_generation:
        raise RuntimeError("transport generation did not change")
    provider = runtime.reconnect_peer(peer_id)
    write_event(report, "reconnected_after_rotation", peer_id=peer_id.value)
    return provider


def run_initiator(
    args: Any,
    runtime: ConnectRuntime,
    runtime_factory: Any | None = None,
) -> int:
    """Run the ordered initiator discovery, trust, connection, and share stages."""
    bidirectional = getattr(args, "bidirectional_surfaces", False)
    surface_ready = {
        surface_id: Event()
        for surface_id in ("desktop", "desktop/settings", "device/status")
    }
    try:
        if bidirectional:
            register_harness_surfaces(runtime)
            register_surface_sync(runtime, surface_ready)
        status = runtime.start()
        _write_started(args.report, "initiator", runtime, status)
        if _status_state(status) != "started":
            raise RuntimeError("runtime did not start")
        if bidirectional:
            candidate = wait_for_matching_peer(runtime, args.peer_id, args.wait)
        else:
            candidate = wait_for_peer(runtime, args.peer_id, args.wait)
        if candidate is None:
            write_event(
                args.report, "discovery_timeout", peers_count=len(runtime.peers)
            )
            return 2
        route = _candidate_values(candidate)
        write_event(args.report, "discovered", **route)
        peer_id = NodeId(candidate.stable_id)
        if bidirectional:
            provider = connect_bidirectionally(
                runtime,
                candidate,
                on_paired=lambda: write_event(
                    args.report, "reverse_paired", outcome="success"
                ),
            )
            write_event(args.report, "reverse_connected", outcome="success")
            exercise_remote_surfaces(
                runtime,
                provider,
                peer_id,
                args.report,
                ready_events=surface_ready,
            )
            if getattr(args, "reconnect_after_rotation", False):
                provider = _reconnect_after_rotation(
                    runtime, peer_id, candidate, args.rotation_wait, args.report
                )
                _exercise_reconnected_surface(runtime, provider, peer_id, args.report)
                write_event(
                    args.report,
                    "shared_after_rotation",
                    capability=CAPABILITY,
                    outcome="success",
                )
            if getattr(args, "revoke_self", False):
                _verify_bidirectional_revocation(provider, peer_id, args.report)
            if getattr(args, "restart_check", False):
                if runtime_factory is None:
                    raise RuntimeError("restart runtime factory is unavailable")
                runtime, provider = _restart_bidirectional_session(
                    args, runtime, peer_id, runtime_factory
                )
            write_event(
                args.report, "diagnostics", **_diagnostics_values(runtime.diagnostics())
            )
            return 0
        existing_peer_id = getattr(args, "existing_peer_id", None)
        if existing_peer_id is not None and existing_peer_id != peer_id.value:
            raise ValueError("discovered peer does not match --existing-peer-id")
        trusted = runtime.pairing.trusted.get(peer_id) if runtime.pairing else None
        if trusted is None:
            if existing_peer_id is not None:
                raise PermissionError("existing peer is not present in persisted trust")
            trusted = runtime.pair_peer(peer_id)
            write_event(
                args.report,
                "paired",
                peer_id=trusted.peer_id.value,
                permissions=sorted(trusted.permissions),
            )
        else:
            write_event(args.report, "restored_trust", peer_id=peer_id.value)
        provider = runtime.connect_peer(peer_id)
        write_event(
            args.report,
            "connected",
            peer_id=peer_id.value,
            tls_verified=True,
            generation=getattr(candidate, "transport_generation", None),
        )
        result = provider.request_shared(CAPABILITY, {"source": "peer_harness"})
        write_event(
            args.report,
            "shared_capability_result",
            capability=CAPABILITY,
            outcome="success"
            if isinstance(result, dict) and result.get("ok") is True
            else "failure",
        )
        if getattr(args, "reconnect_after_rotation", False):
            provider = _reconnect_after_rotation(
                runtime, peer_id, candidate, args.rotation_wait, args.report
            )
            result = _retry_shared_capability(
                lambda: provider.request_shared(
                    CAPABILITY,
                    {"source": "peer_harness", "after_rotation": True},
                )
            )
            write_event(
                args.report,
                "shared_after_rotation",
                capability=CAPABILITY,
                outcome=(
                    "success"
                    if isinstance(result, dict) and result.get("ok") is True
                    else "failure"
                ),
            )
        if getattr(args, "revoke_self", False):
            provider.revoke_self()
            write_event(args.report, "self_revoked", peer_id=peer_id.value)
            try:
                provider.request_shared(CAPABILITY, {"source": "after_revoke"})
            except Exception as error:  # noqa: BLE001 - report only the exception type
                write_event(
                    args.report,
                    "post_revoke_denied",
                    error_type=type(error).__name__,
                )
            else:
                raise AssertionError("revoked caller still accessed capability")
        if getattr(args, "restart_check", False):
            if runtime_factory is None:
                raise RuntimeError("restart runtime factory is unavailable")
            runtime.shutdown()
            runtime = runtime_factory()
            status = runtime.start()
            if _status_state(status) != "started":
                raise RuntimeError("runtime did not restart")
            restored = runtime.pairing and runtime.pairing.trusted.get(peer_id)
            if restored is None:
                raise RuntimeError("trusted peer was not restored")
            write_event(args.report, "restored_trust", peer_id=peer_id.value)
        write_event(
            args.report, "diagnostics", **_diagnostics_values(runtime.diagnostics())
        )
        return 0
    except Exception as error:  # noqa: BLE001 - report only the exception type
        write_event(args.report, "error", error_type=type(error).__name__)
        return 1
    finally:
        runtime.shutdown()


def _runtime(args: Any) -> ConnectRuntime:
    route_started: dict[tuple[str, str, int], float] = {}
    runtime_holder: list[ConnectRuntime] = []
    capability_peers: set[NodeId] = set()
    explicit_approval = getattr(args, "explicit_approval", False)

    def route_attempt(
        phase: str, endpoint: Any, outcome: str, _error: str | None
    ) -> None:
        key = (phase, endpoint.address, endpoint.port)
        now = time.monotonic()
        duration_ms = None
        if outcome == "started":
            route_started[key] = now
        elif key in route_started:
            duration_ms = round((now - route_started.pop(key)) * 1000, 1)
        write_event(
            args.report,
            "route_attempt",
            phase=phase,
            address=endpoint.address,
            port=endpoint.port,
            source=getattr(endpoint.source, "value", endpoint.source),
            outcome=outcome,
            duration_ms=duration_ms,
        )

    def approve_pairing(request: Any) -> bool:
        return _approve_capability_pairing(
            runtime_holder[0], args.report, request, capability_peers
        )

    def on_pairing_request(request: Any) -> bool:
        if getattr(args, "bidirectional_surfaces", False):
            return approve_surface_pairing(runtime_holder[0], args.report, request)
        if explicit_approval:
            approval_file = getattr(args, "approval_file", None)
            if approval_file is None:
                approval_file = Path(f"{args.report}.approve")
            return _explicit_approval_decision(
                runtime_holder[0],
                args.report,
                request,
                capability_peers,
                approval_file=approval_file,
                approval_wait=getattr(args, "approval_wait", 30.0),
            )
        return approve_pairing(request)

    pairing_callback = _TrackedPairingCallback(on_pairing_request, capability_peers)

    runtime = ConnectRuntime(
        ConnectConfig(
            profile_dir=args.profile,
            advertised_addresses=(
                tuple(args.advertise_address) if args.advertise_address else None
            ),
            on_route_attempt=route_attempt,
            on_discovery=lambda kind, payload: discovery_event(
                args.report, kind, payload
            ),
            on_pairing_request=pairing_callback
            if args.role == "target" or getattr(args, "bidirectional_surfaces", False)
            else None,
        )
    )
    runtime_holder.append(runtime)
    return runtime


def _runtime_factory(args: Any) -> Any:
    return lambda: _runtime(args)
