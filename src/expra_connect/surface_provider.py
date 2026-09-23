"""Remote client and server adapters for typed surface operations."""

from typing import Any

from .cluster.models import CapabilityGrant
from .surface_protocol import (
    SURFACE_ACCESS_BY_OPERATION,
    SURFACE_ACTION,
    SURFACE_OPERATIONS,
    SURFACE_READ,
    SURFACE_REVIEW,
    validate_surface_operation_params,
)
from .surfaces import SurfaceRegistry
from .wire_protocol import (
    RemoteAuthorizationError,
    RemoteProtocolError,
    RemoteRequest,
    RemoteUnavailableError,
)


class SurfaceProviderMixin:
    """Client methods for target-owned surface operations."""

    _request: Any

    def _surface_request(
        self,
        operation: str,
        params: dict[str, Any],
        cancel_event: Any | None = None,
    ) -> Any:
        validate_surface_operation_params(operation, params, RemoteProtocolError)
        payload = self._request(operation, params, cancel_event)
        if not isinstance(payload, dict) or "result" not in payload:
            raise RemoteProtocolError("surface response has no result")
        return payload["result"]

    @staticmethod
    def _normalize_surface_params(
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if params is None:
            return {}
        if not isinstance(params, dict):
            raise RemoteProtocolError("surface params must be an object")
        return dict(params)

    def read_surface(
        self,
        surface_id: str,
        *,
        params: dict[str, Any] | None = None,
        cancel_event: Any | None = None,
    ) -> Any:
        return self._surface_request(
            SURFACE_READ,
            {
                "surface_id": surface_id,
                "params": self._normalize_surface_params(params),
            },
            cancel_event,
        )

    def review_surface(
        self,
        surface_id: str,
        *,
        params: dict[str, Any] | None = None,
        cancel_event: Any | None = None,
    ) -> Any:
        return self._surface_request(
            SURFACE_REVIEW,
            {
                "surface_id": surface_id,
                "params": self._normalize_surface_params(params),
            },
            cancel_event,
        )

    def invoke_surface_action(
        self,
        surface_id: str,
        action: str,
        *,
        params: dict[str, Any] | None = None,
        cancel_event: Any | None = None,
    ) -> Any:
        """Invoke an action; cancellation cannot undo an already-sent request."""
        return self._surface_request(
            SURFACE_ACTION,
            {
                "surface_id": surface_id,
                "action": action,
                "params": self._normalize_surface_params(params),
            },
            cancel_event,
        )


def dispatch_surface_request(
    registry: SurfaceRegistry | None,
    request: RemoteRequest,
    cluster_grant: CapabilityGrant | None,
) -> Any:
    if request.caller_node_id is None:
        raise RemoteAuthorizationError("caller identity required for surface requests")
    if registry is None:
        raise RemoteUnavailableError("surface sharing is unavailable")
    if request.op not in SURFACE_OPERATIONS:
        raise RemoteProtocolError(f"unknown surface operation: {request.op}")
    validate_surface_operation_params(request.op, request.params, RemoteProtocolError)
    if cluster_grant is not None and (
        cluster_grant.subject != request.caller_node_id
        or cluster_grant.target != request.node_id
    ):
        raise RemoteAuthorizationError("cluster authorization context is invalid")
    return registry.dispatch(
        request.caller_node_id,
        request.params["surface_id"],
        access=SURFACE_ACCESS_BY_OPERATION[request.op],
        action=request.params.get("action"),
        params=request.params.get("params", {}),
        cluster_sources=(request.caller_node_id,) if cluster_grant is not None else (),
    )
