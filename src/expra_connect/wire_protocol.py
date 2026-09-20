"""Versioned HMAC envelope, replay protection, and operation validation."""

import hashlib
import hmac
import json
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .identity import NodeId
from .models import (
    READ_PERMISSIONS,
    NodeCapability,
    NodePermission,
    ProcessActionKind,
)

REMOTE_PROTOCOL_VERSION = "1"
PAIRING_MODE_TRANSACTIONAL = "transactional"
MAX_ENVELOPE_BYTES = 8 * 1024 * 1024
DEFAULT_FRESHNESS_SECONDS = 60.0
DEFAULT_REPLAY_TTL_SECONDS = 300.0
DEFAULT_REPLAY_MAX_ENTRIES = 4096
DEFAULT_MAX_ACTIVE_HANDLERS = 8
DEFAULT_IDEMPOTENCY_TTL_SECONDS = 300.0
DEFAULT_IDEMPOTENCY_MAX_ENTRIES = 4096
OP_REQUIRED_CAPABILITY: dict[str, NodeCapability] = {
    "hello": NodeCapability.READ_STATE,
    "ping": NodeCapability.READ_STATE,
    "echo": NodeCapability.READ_STATE,
    "demo.read_state": NodeCapability.READ_STATE,
    "dashboard_snapshot": NodeCapability.READ_STATE,
    "component_summary": NodeCapability.READ_STATE,
    "process_candidates": NodeCapability.READ_STATE,
    "storage_candidates": NodeCapability.READ_STATE,
    "process_request_quit": NodeCapability.REMOTE_MANAGEMENT,
    "process_force_quit": NodeCapability.REMOTE_MANAGEMENT,
    "revoke_self": NodeCapability.READ_STATE,
    "consume_invite": NodeCapability.REMOTE_MANAGEMENT,
    "assign_role": NodeCapability.REMOTE_MANAGEMENT,
    "renew_coordinator_lease": NodeCapability.REMOTE_MANAGEMENT,
    "revoke_member": NodeCapability.REMOTE_MANAGEMENT,
    "pause_worker": NodeCapability.REMOTE_MANAGEMENT,
    "resume_worker": NodeCapability.REMOTE_MANAGEMENT,
    "revoke_worker": NodeCapability.REMOTE_MANAGEMENT,
    "remove_job": NodeCapability.REMOTE_MANAGEMENT,
    "remove_connection": NodeCapability.REMOTE_MANAGEMENT,
    "grant_capabilities": NodeCapability.REMOTE_MANAGEMENT,
    "revoke_capabilities": NodeCapability.REMOTE_MANAGEMENT,
    "sync_capability_grant": NodeCapability.REMOTE_MANAGEMENT,
    "worker_snapshot": NodeCapability.REMOTE_MANAGEMENT,
    "standby_batch": NodeCapability.REMOTE_MANAGEMENT,
    "capability_request": NodeCapability.READ_STATE,
}
OP_REQUIRED_PERMISSION: dict[str, NodePermission] = {
    operation: NodePermission(capability.value)
    for operation, capability in OP_REQUIRED_CAPABILITY.items()
}
ROLE_OPERATIONS = frozenset(
    {
        "consume_invite",
        "assign_role",
        "renew_coordinator_lease",
        "revoke_member",
        "pause_worker",
        "resume_worker",
        "revoke_worker",
        "remove_job",
        "remove_connection",
        "grant_capabilities",
        "revoke_capabilities",
        "sync_capability_grant",
        "worker_snapshot",
        "standby_batch",
    }
)
OPERATION_SAFETY: dict[str, str] = {
    operation: "read" for operation in OP_REQUIRED_CAPABILITY
}
for _operation in ("process_request_quit", "revoke_self"):
    OPERATION_SAFETY[_operation] = "retry_safe"
for _operation in ("process_force_quit",):
    OPERATION_SAFETY[_operation] = "unsafe"
for _operation in ROLE_OPERATIONS:
    OPERATION_SAFETY[_operation] = "retry_safe"


class RemoteProtocolError(ValueError):
    """Raised for malformed or unsupported envelopes."""


class RemoteAuthError(RemoteProtocolError):
    """Raised when an envelope fails authentication or replay checks."""


class RemoteAuthorizationError(RemoteProtocolError):
    """Raised when a node is not authorised for the requested operation."""


class RemoteExecutionError(RuntimeError):
    """Raised when the authenticated peer reports an execution failure."""


class RemoteTransportError(RuntimeError):
    """Raised when the transport cannot complete an authenticated exchange."""


class IdempotencyCollisionError(RemoteAuthError):
    """Raised when one request ID is reused for different operation content."""


class RemoteUnavailableError(RemoteExecutionError):
    """Raised when the authenticated target cannot currently perform an action."""


def parse_hello_capabilities(payload: Any) -> frozenset[NodeCapability]:
    """Decode advertised capabilities without granting unknown values."""

    if not isinstance(payload, dict):
        raise RemoteProtocolError("hello payload must be an object")
    raw_capabilities = payload.get("capabilities", [])
    if not isinstance(raw_capabilities, list):
        raise RemoteProtocolError("hello capabilities must be a list")
    capabilities: set[NodeCapability] = set()
    for raw in raw_capabilities:
        if not isinstance(raw, str):
            raise RemoteProtocolError("hello capability must be a string")
        try:
            capabilities.add(NodeCapability(raw))
        except ValueError:
            # Unknown values are forward metadata, never permissions.
            continue
    return frozenset(capabilities)


def validate_hello_payload(
    payload: Any, *, expected_node_id: NodeId | None = None
) -> frozenset[NodeCapability]:
    """Validate the security-bearing portion of a hello response."""

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RemoteProtocolError("hello response is invalid")
    if (
        payload.get("protocol_version", REMOTE_PROTOCOL_VERSION)
        != REMOTE_PROTOCOL_VERSION
    ):
        raise RemoteProtocolError("unsupported remote protocol version")
    node_id = payload.get("node_id")
    if not isinstance(node_id, str) or not node_id:
        raise RemoteAuthError("hello node identity is invalid")
    if expected_node_id is not None and node_id != expected_node_id.value:
        raise RemoteAuthError("hello came from the wrong node")
    fingerprint = payload.get("identity_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise RemoteAuthError("hello identity fingerprint is missing")
    return parse_hello_capabilities(payload)


@dataclass(frozen=True, slots=True)
class PeerGrant:
    """Target-owned authorization grant for one authenticated caller."""

    caller_node_id: NodeId
    secret: str
    permissions: frozenset[NodePermission]
    expires_at: float | None = None


@dataclass(frozen=True, slots=True)
class PairingRequest:
    """Unauthenticated, TLS-protected request awaiting target approval."""

    caller_node_id: NodeId
    identity_fingerprint: str
    transport_fingerprint: str
    proposed_secret: str
    permissions: frozenset[NodePermission]
    root_public_key: str | None = None
    transport_generation: int | None = None
    transport_proof: str | None = None

    def __post_init__(self) -> None:
        if not self.caller_node_id.value or not self.identity_fingerprint:
            raise RemoteAuthError("pairing identity is missing")
        if not self.transport_fingerprint:
            raise RemoteAuthError("pairing transport fingerprint is missing")
        _validate_secret(self.proposed_secret)
        if not self.permissions <= frozenset(READ_PERMISSIONS):
            raise RemoteAuthorizationError("pairing is read-only")


@dataclass(frozen=True, slots=True)
class PairingControlRequest:
    """Exact binding presented when completing or cancelling a pairing."""

    operation: str
    transaction_id: str
    caller_node_id: NodeId
    identity_fingerprint: str
    transport_fingerprint: str
    secret: str
    permissions: frozenset[NodePermission]
    expires_at: float


@dataclass(frozen=True, slots=True)
class CapabilityElevationRequest:
    """Authenticated peer request for target-side permission elevation."""

    caller_node_id: NodeId
    identity_fingerprint: str
    transport_fingerprint: str
    current_secret: str
    proposed_secret: str
    permissions: frozenset[NodePermission]

    def __post_init__(self) -> None:
        if not self.caller_node_id.value or not self.identity_fingerprint:
            raise RemoteAuthError("elevation identity is missing")
        if not self.transport_fingerprint:
            raise RemoteAuthError("elevation transport fingerprint is missing")
        _validate_secret(self.current_secret)
        _validate_secret(self.proposed_secret)
        if not self.permissions:
            raise RemoteAuthorizationError("elevation requires a permission")


def _validate_secret(secret: str) -> None:
    if not isinstance(secret, str) or len(secret) != 64:
        raise ValueError("peer credentials must be 256-bit hex text")
    try:
        bytes.fromhex(secret)
    except ValueError as error:
        raise ValueError("peer credentials must be hexadecimal") from error


def validate_pairing_control_request(
    raw: Any, *, clock: Callable[[], float] = time.time
) -> PairingControlRequest:
    """Validate the complete binding for an additive pairing control."""

    if not isinstance(raw, dict) or raw.get("op") not in {"pair_confirm", "pair_abort"}:
        raise RemoteProtocolError("pairing control operation is invalid")
    expected_fields = {
        "op",
        "transaction_id",
        "caller_node_id",
        "identity_fingerprint",
        "transport_fingerprint",
        "secret",
        "permissions",
        "expires_at",
    }
    if set(raw) != expected_fields:
        raise RemoteProtocolError("pairing control fields are invalid")
    for field in (
        "transaction_id",
        "identity_fingerprint",
        "transport_fingerprint",
    ):
        if not isinstance(raw[field], str) or not raw[field]:
            raise RemoteProtocolError(f"pairing control {field} is invalid")
    if not isinstance(raw["caller_node_id"], str) or not raw["caller_node_id"]:
        raise RemoteProtocolError("pairing control caller identity is invalid")
    try:
        _validate_secret(raw["secret"])
    except (TypeError, ValueError) as error:
        raise RemoteProtocolError("pairing control secret is invalid") from error
    permissions = raw["permissions"]
    if not isinstance(permissions, list) or any(
        not isinstance(item, str) for item in permissions
    ):
        raise RemoteProtocolError("pairing control permissions are invalid")
    try:
        parsed_permissions = frozenset(NodePermission(item) for item in permissions)
    except ValueError as error:
        raise RemoteProtocolError("pairing control permissions are invalid") from error
    if not parsed_permissions <= frozenset(READ_PERMISSIONS):
        raise RemoteAuthorizationError("pairing control is read-only")
    expires_at = raw["expires_at"]
    if (
        not isinstance(expires_at, (int, float))
        or isinstance(expires_at, bool)
        or not math.isfinite(float(expires_at))
        or float(expires_at) <= clock()
    ):
        raise RemoteProtocolError("pairing control expiry is invalid")
    return PairingControlRequest(
        operation=raw["op"],
        transaction_id=raw["transaction_id"],
        caller_node_id=NodeId(raw["caller_node_id"]),
        identity_fingerprint=raw["identity_fingerprint"],
        transport_fingerprint=raw["transport_fingerprint"],
        secret=raw["secret"],
        permissions=parsed_permissions,
        expires_at=float(expires_at),
    )


@dataclass(frozen=True, slots=True)
class RemoteRequest:
    """One verified authenticated request from a peer node."""

    node_id: NodeId
    caller_node_id: NodeId | None
    op: str
    params: dict[str, Any]
    request_id: str
    nonce: str
    timestamp: float
    session_id: str | None = None
    resume: bool = False
    connection_generation: str | None = None


@dataclass(frozen=True, slots=True)
class RemoteResponse:
    """One verified authenticated response to a request."""

    node_id: NodeId
    request_id: str
    status: str
    payload: dict[str, Any] | None
    error: str | None
    timestamp: float
    session_id: str | None = None
    connection_generation: str | None = None


def _canonical(fields: dict[str, Any]) -> str:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"))


def _signature(secret: str, fields: dict[str, Any]) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        _canonical(fields).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def sign_request(
    *,
    node_id: str,
    op: str,
    params: dict[str, Any],
    request_id: str,
    nonce: str,
    timestamp: float,
    secret: str,
    caller_node_id: str | None = None,
    session_id: str | None = None,
    resume: bool = False,
    connection_generation: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "v": REMOTE_PROTOCOL_VERSION,
        "node_id": node_id,
        "op": op,
        "params": params,
        "request_id": request_id,
        "nonce": nonce,
        "ts": timestamp,
    }
    if caller_node_id is not None:
        fields["caller_node_id"] = caller_node_id
    if session_id is not None:
        fields["session_id"] = session_id
        fields["resume"] = resume
    if connection_generation is not None:
        fields["connection_generation"] = connection_generation
    envelope = dict(fields)
    envelope["sig"] = _signature(secret, fields)
    return envelope


def sign_response(
    *,
    node_id: str,
    request_id: str,
    status: str,
    secret: str,
    timestamp: float,
    payload: dict[str, Any] | None = None,
    error: str | None = None,
    session_id: str | None = None,
    connection_generation: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "v": REMOTE_PROTOCOL_VERSION,
        "node_id": node_id,
        "request_id": request_id,
        "status": status,
        "payload": payload,
        "error": error,
        "ts": timestamp,
    }
    if session_id is not None:
        fields["session_id"] = session_id
    if connection_generation is not None:
        fields["connection_generation"] = connection_generation
    envelope = dict(fields)
    envelope["sig"] = _signature(secret, fields)
    return envelope


class ReplayCache:
    """Bounded, expiry-pruned record of seen ``(node_id, request_id, nonce)``.

    One entry is kept per authenticated request and evicted after ``ttl`` or
    when the cache grows past ``max_entries``, so memory stays bounded while
    replayed requests are rejected for at least the freshness window.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = DEFAULT_REPLAY_TTL_SECONDS,
        max_entries: int = DEFAULT_REPLAY_MAX_ENTRIES,
    ) -> None:
        self._clock = clock
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._seen: dict[tuple[str, str, str], float] = {}
        self._request_ids: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def check_and_record(
        self,
        node_id: str,
        request_id: str,
        nonce: str,
        seen_at: float,
    ) -> bool:
        key = (node_id, request_id, nonce)
        with self._lock:
            self._prune(seen_at)
            if key in self._seen:
                return False
            if len(self._seen) >= self._max_entries:
                return False
            self._seen[key] = seen_at
            return True

    def _prune(self, now: float) -> None:
        expired = [
            key
            for key, recorded_at in self._seen.items()
            if now - recorded_at > self._ttl
        ]
        for replay_key in expired:
            del self._seen[replay_key]
        expired_ids = [
            request_key
            for request_key, recorded_at in self._request_ids.items()
            if now - recorded_at > self._ttl
        ]
        for request_key in expired_ids:
            del self._request_ids[request_key]

    def check_and_record_request_id(
        self, node_id: str, request_id: str, seen_at: float
    ) -> bool:
        """Reject destructive request-ID reuse even when its nonce is fresh."""

        with self._lock:
            self._prune(seen_at)
            key = (node_id, request_id)
            if key in self._request_ids:
                return False
            if len(self._request_ids) >= self._max_entries:
                return False
            self._request_ids[key] = seen_at
            return True

    def __len__(self) -> int:
        return len(self._seen)


def verify_request(
    envelope: Any,
    *,
    secret: str,
    clock: Callable[[], float],
    freshness_seconds: float,
    replay_cache: ReplayCache,
) -> RemoteRequest:
    if not isinstance(envelope, dict):
        raise RemoteProtocolError("request envelope must be an object")
    if envelope.get("v") != REMOTE_PROTOCOL_VERSION:
        raise RemoteProtocolError("unsupported remote protocol version")
    allowed_fields = {
        "v",
        "node_id",
        "caller_node_id",
        "op",
        "params",
        "request_id",
        "nonce",
        "ts",
        "sig",
        "session_id",
        "resume",
        "connection_generation",
    }
    if not set(envelope) <= allowed_fields or "sig" not in envelope:
        raise RemoteProtocolError("request envelope fields are invalid")
    signature = envelope.get("sig")
    if not isinstance(signature, str):
        raise RemoteAuthError("request is missing its signature")
    fields = {key: value for key, value in envelope.items() if key != "sig"}
    if not hmac.compare_digest(signature, _signature(secret, fields)):
        raise RemoteAuthError("request signature is invalid")
    node_id = envelope.get("node_id")
    caller_node_id = envelope.get("caller_node_id")
    op = envelope.get("op")
    params = envelope.get("params")
    request_id = envelope.get("request_id")
    nonce = envelope.get("nonce")
    session_id = envelope.get("session_id")
    resume = envelope.get("resume", False)
    connection_generation = envelope.get("connection_generation")
    timestamp = envelope.get("ts")
    if not isinstance(node_id, str) or not node_id:
        raise RemoteAuthError("request node_id is invalid")
    if caller_node_id is not None and (
        not isinstance(caller_node_id, str) or not caller_node_id
    ):
        raise RemoteAuthError("request caller_node_id is invalid")
    if not isinstance(op, str):
        raise RemoteProtocolError("request op must be a string")
    if not isinstance(params, dict):
        raise RemoteProtocolError("request params must be an object")
    if not isinstance(request_id, str) or not _valid_identifier(request_id):
        raise RemoteAuthError("request_id is invalid")
    if not isinstance(nonce, str) or not _valid_identifier(nonce):
        raise RemoteAuthError("request nonce is invalid")
    if session_id is not None and not _valid_identifier(session_id):
        raise RemoteAuthError("session_id is invalid")
    if not isinstance(resume, bool):
        raise RemoteAuthError("request resume flag is invalid")
    if connection_generation is not None and not _valid_identifier(
        connection_generation
    ):
        raise RemoteAuthError("connection generation is invalid")
    if (
        not isinstance(timestamp, (int, float))
        or isinstance(timestamp, bool)
        or not math.isfinite(timestamp)
    ):
        raise RemoteAuthError("request timestamp is invalid")
    now = clock()
    age = now - float(timestamp)
    if age > freshness_seconds or age < -freshness_seconds:
        raise RemoteAuthError("request timestamp is outside the freshness window")
    if not replay_cache.check_and_record(node_id, request_id, nonce, now):
        raise RemoteAuthError("request has been replayed")
    return RemoteRequest(
        node_id=NodeId(node_id),
        caller_node_id=(NodeId(caller_node_id) if caller_node_id else None),
        op=op,
        params=params,
        request_id=request_id,
        nonce=nonce,
        timestamp=float(timestamp),
        session_id=session_id,
        resume=resume,
        connection_generation=connection_generation,
    )


def verify_response(
    envelope: Any,
    *,
    secret: str,
    clock: Callable[[], float],
    freshness_seconds: float,
) -> RemoteResponse:
    if not isinstance(envelope, dict):
        raise RemoteProtocolError("response envelope must be an object")
    if envelope.get("v") != REMOTE_PROTOCOL_VERSION:
        raise RemoteProtocolError("unsupported remote protocol version")
    allowed_fields = {
        "v",
        "node_id",
        "request_id",
        "status",
        "payload",
        "error",
        "ts",
        "sig",
        "session_id",
        "connection_generation",
    }
    if not set(envelope) <= allowed_fields or "sig" not in envelope:
        raise RemoteProtocolError("response envelope fields are invalid")
    signature = envelope.get("sig")
    if not isinstance(signature, str):
        raise RemoteAuthError("response is missing its signature")
    fields = {key: value for key, value in envelope.items() if key != "sig"}
    if not hmac.compare_digest(signature, _signature(secret, fields)):
        raise RemoteAuthError("response signature is invalid")
    node_id = envelope.get("node_id")
    request_id = envelope.get("request_id")
    status = envelope.get("status")
    timestamp = envelope.get("ts")
    session_id = envelope.get("session_id")
    connection_generation = envelope.get("connection_generation")
    if (
        not isinstance(node_id, str)
        or not node_id
        or not isinstance(request_id, str)
        or not request_id
    ):
        raise RemoteAuthError("response identity fields are invalid")
    if status not in ("ok", "error"):
        raise RemoteProtocolError("response status is invalid")
    payload = envelope.get("payload")
    if payload is not None and not isinstance(payload, dict):
        raise RemoteProtocolError("response payload must be an object or null")
    error = envelope.get("error")
    if error is not None and not isinstance(error, str):
        raise RemoteProtocolError("response error must be a string or null")
    if session_id is not None and not _valid_identifier(session_id):
        raise RemoteAuthError("response session_id is invalid")
    if connection_generation is not None and not _valid_identifier(
        connection_generation
    ):
        raise RemoteAuthError("response connection generation is invalid")
    if (
        not isinstance(timestamp, (int, float))
        or isinstance(timestamp, bool)
        or not math.isfinite(timestamp)
    ):
        raise RemoteAuthError("response timestamp is invalid")
    age = clock() - float(timestamp)
    if age > freshness_seconds or age < -freshness_seconds:
        raise RemoteAuthError("response timestamp is outside the freshness window")
    return RemoteResponse(
        node_id=NodeId(node_id),
        request_id=request_id,
        status=status,
        payload=payload,
        error=error,
        timestamp=float(timestamp),
        session_id=session_id,
        connection_generation=connection_generation,
    )


def _valid_identifier(value: str) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and all(character.isalnum() or character in "._:-" for character in value)
    )


@dataclass(slots=True)
class _IdempotencyEntry:
    fingerprint: str
    expires_at: float
    event: threading.Event
    result: dict[str, Any] | None = None
    error: BaseException | None = None
    complete: bool = False


class IdempotencyCache:
    """Bounded result cache with one executor per request ID."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        max_entries: int = DEFAULT_IDEMPOTENCY_MAX_ENTRIES,
        state_store: Any | None = None,
    ) -> None:
        if ttl_seconds <= 0 or max_entries < 1:
            raise ValueError("invalid idempotency cache bounds")
        self._clock = clock
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._entries: dict[tuple[str, str], _IdempotencyEntry] = {}
        self._lock = threading.Lock()
        self._state_store = state_store
        if state_store is not None:
            self._load_state()

    def run(
        self,
        key: tuple[str, str],
        fingerprint: str,
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            self._prune(now)
            entry = self._entries.get(key)
            owner = entry is None
            if entry is None:
                if len(self._entries) >= self._max_entries:
                    raise RemoteAuthError("idempotency cache is full")
                entry = _IdempotencyEntry(
                    fingerprint=fingerprint,
                    expires_at=now + self._ttl,
                    event=threading.Event(),
                )
                self._entries[key] = entry
            elif entry.fingerprint != fingerprint:
                raise IdempotencyCollisionError("request ID was reused")
        if not owner:
            entry.event.wait()
            if entry.error is not None:
                raise entry.error
            if entry.result is None:
                raise RemoteExecutionError("idempotency result is unavailable")
            return dict(entry.result)
        try:
            result = operation()
        except BaseException as error:
            with self._lock:
                entry.error = error
                entry.complete = True
                entry.event.set()
                self._entries.pop(key, None)
            raise
        with self._lock:
            entry.result = dict(result)
            entry.complete = True
            entry.event.set()
            self._save_state()
        return result

    def _prune(self, now: float) -> None:
        for key, entry in list(self._entries.items()):
            if entry.complete and now >= entry.expires_at:
                del self._entries[key]

    def _load_state(self) -> None:
        state_store = self._state_store
        if state_store is None:
            return
        try:
            raw = state_store.load()
        except Exception:  # noqa: BLE001 - corrupt cache must fail closed.
            return
        for item in raw.get("entries", []):
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            fingerprint = item.get("fingerprint")
            result = item.get("result")
            remaining = item.get("remaining")
            if (
                not isinstance(key, list)
                or len(key) != 2
                or not all(isinstance(part, str) for part in key)
                or not isinstance(fingerprint, str)
                or not isinstance(result, dict)
                or not isinstance(remaining, (int, float))
                or remaining <= 0
            ):
                continue
            if len(self._entries) >= self._max_entries:
                break
            event = threading.Event()
            event.set()
            self._entries[(key[0], key[1])] = _IdempotencyEntry(
                fingerprint=fingerprint,
                expires_at=self._clock() + float(remaining),
                event=event,
                result=result,
                complete=True,
            )

    def _save_state(self) -> None:
        state_store = self._state_store
        if state_store is None:
            return
        now = self._clock()
        state_store.save(
            {
                "entries": [
                    {
                        "key": list(key),
                        "fingerprint": entry.fingerprint,
                        "result": entry.result,
                        "remaining": entry.expires_at - now,
                    }
                    for key, entry in self._entries.items()
                    if entry.complete
                    and entry.result is not None
                    and entry.expires_at > now
                ]
            }
        )

    def __len__(self) -> int:
        with self._lock:
            self._prune(self._clock())
            return len(self._entries)


def validate_operation_params(op: str, params: dict[str, Any]) -> None:
    if op not in OP_REQUIRED_CAPABILITY:
        raise RemoteProtocolError(f"unknown operation: {op}")
    if op in {
        "hello",
        "ping",
        "echo",
        "demo.read_state",
        "dashboard_snapshot",
        "process_candidates",
        "storage_candidates",
        "revoke_self",
    }:
        if params:
            raise RemoteProtocolError(f"{op} accepts no parameters")
        return
    if op == "capability_request":
        capability = params.get("capability")
        request_params = params.get("params", {})
        if not isinstance(capability, str) or not capability:
            raise RemoteProtocolError("capability_request requires a capability")
        if not isinstance(request_params, dict):
            raise RemoteProtocolError("capability_request params must be an object")
        if set(params) != {"capability", "params"}:
            raise RemoteProtocolError("capability_request has unexpected parameters")
        return
    if op == "component_summary":
        key = params.get("key")
        if not isinstance(key, str) or not key:
            raise RemoteProtocolError("component_summary requires a key")
        return
    if op in {"process_request_quit", "process_force_quit"}:
        processes = params.get("processes")
        if not isinstance(processes, list) or not processes:
            raise RemoteProtocolError(f"{op} requires process references")
        for item in processes:
            if not isinstance(item, dict):
                raise RemoteProtocolError("process reference must be an object")
            if (
                not isinstance(item.get("pid"), int)
                or isinstance(item.get("pid"), bool)
                or item["pid"] < 0
            ):
                raise RemoteProtocolError("process reference pid is invalid")
            create_time = item.get("create_time")
            if create_time is None or (
                not isinstance(create_time, (int, float))
                or isinstance(create_time, bool)
                or not math.isfinite(float(create_time))
                or float(create_time) < 0
            ):
                raise RemoteProtocolError("process reference create_time is required")
        expected_action = (
            ProcessActionKind.REQUEST_QUIT.value
            if op == "process_request_quit"
            else ProcessActionKind.FORCE_QUIT.value
        )
        if params.get("action") != expected_action:
            raise RemoteProtocolError("process action is not allowlisted")
        if set(params) != {"processes", "action"}:
            raise RemoteProtocolError(f"{op} has unexpected parameters")
        return
    if op in {
        "consume_invite",
        "assign_role",
        "renew_coordinator_lease",
        "worker_snapshot",
        "standby_batch",
        "pause_worker",
        "revoke_worker",
        "resume_worker",
        "remove_connection",
        "remove_job",
        "grant_capabilities",
        "revoke_capabilities",
        "sync_capability_grant",
    }:
        required = {"cluster_id", "epoch", "fencing_token"}
        if not required <= set(params):
            raise RemoteProtocolError(f"{op} requires cluster fencing fields")
        if not isinstance(params["cluster_id"], str) or not params["cluster_id"]:
            raise RemoteProtocolError("cluster_id is invalid")
        if (
            not isinstance(params["epoch"], int)
            or isinstance(params["epoch"], bool)
            or params["epoch"] < 0
        ):
            raise RemoteProtocolError("cluster epoch is invalid")
        if not isinstance(params["fencing_token"], str) or not params["fencing_token"]:
            raise RemoteProtocolError("fencing token is invalid")
        if op in {"renew_coordinator_lease", "consume_invite"}:
            if op == "consume_invite" and not isinstance(params.get("token"), str):
                raise RemoteProtocolError("invite token is invalid")
            return
        if op == "assign_role":
            if not isinstance(params.get("target_node_id"), str):
                raise RemoteProtocolError("role target is invalid")
            roles = params.get("roles")
            if (
                not isinstance(roles, list)
                or not roles
                or any(not isinstance(role, str) for role in roles)
            ):
                raise RemoteProtocolError("role list is invalid")
            return
        if op in {"pause_worker", "resume_worker", "revoke_worker", "revoke_member"}:
            if not isinstance(params.get("target_node_id"), str):
                raise RemoteProtocolError("role target is invalid")
            return
        if op in {"remove_connection", "remove_job"}:
            if (
                not isinstance(params.get("target_node_id"), str)
                or not params["target_node_id"]
            ):
                raise RemoteProtocolError("role target is invalid")
            return
        if op in {"grant_capabilities", "revoke_capabilities"}:
            expected_fields = {
                "cluster_id",
                "epoch",
                "fencing_token",
                "subject_node_id",
                "target_node_id",
                "permissions",
                "expires_at",
            }
            if op == "revoke_capabilities":
                expected_fields -= {"permissions", "expires_at"}
            if set(params) != expected_fields:
                raise RemoteProtocolError(f"{op} has unexpected parameters")
            for field in ("subject_node_id", "target_node_id"):
                if not isinstance(params.get(field), str) or not params[field]:
                    raise RemoteProtocolError(f"{op} target identity is invalid")
            if op == "grant_capabilities":
                permissions = params.get("permissions")
                known = {item.value for item in NodePermission}
                if (
                    not isinstance(permissions, list)
                    or not permissions
                    or any(
                        not isinstance(item, str) or item not in known
                        for item in permissions
                    )
                ):
                    raise RemoteProtocolError(
                        "capability grant permissions are invalid"
                    )
                if (
                    not isinstance(params.get("expires_at"), (int, float))
                    or isinstance(params["expires_at"], bool)
                    or not math.isfinite(float(params["expires_at"]))
                ):
                    raise RemoteProtocolError("capability grant expiry is invalid")
            return
        if op == "sync_capability_grant":
            expected_fields = {
                "cluster_id",
                "epoch",
                "fencing_token",
                "subject_node_id",
                "target_node_id",
                "permissions",
                "expires_at",
            }
            if set(params) != expected_fields:
                raise RemoteProtocolError(f"{op} has unexpected parameters")
            for field in ("subject_node_id", "target_node_id"):
                if not isinstance(params.get(field), str) or not params[field]:
                    raise RemoteProtocolError(f"{op} target identity is invalid")
            permissions = params.get("permissions")
            known = {item.value for item in NodePermission}
            if not isinstance(permissions, list) or any(
                not isinstance(item, str) or item not in known for item in permissions
            ):
                raise RemoteProtocolError(f"{op} permissions are invalid")
            if (
                not isinstance(params.get("expires_at"), (int, float))
                or isinstance(params["expires_at"], bool)
                or not math.isfinite(float(params["expires_at"]))
            ):
                raise RemoteProtocolError(f"{op} expiry is invalid")
            return
        payload = params.get("payload")
        if not isinstance(payload, dict):
            raise RemoteProtocolError("snapshot payload is invalid")
        if len(json.dumps(payload, separators=(",", ":"))) > MAX_ENVELOPE_BYTES // 2:
            raise RemoteProtocolError("snapshot payload is too large")
        return
    if params:
        raise RemoteProtocolError(f"{op} accepts no parameters")
