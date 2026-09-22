"""Remote client and server adapters for typed surface operations."""

from typing import Any

from .surfaces import SurfaceRegistry
from .wire_protocol import (
    RemoteAuthorizationError,
    RemoteProtocolError,
    RemoteUnavailableError,
)


class SurfaceProviderMixin:
    """Client methods for target-owned surface operations."""

    _request: Any

    def _surface_request(self, operation: str, params: dict[str, Any]) -> Any:
        payload = self._request(operation, params)
        if "result" not in payload:
            raise RemoteProtocolError("surface response has no result")
        return payload["result"]

    def read_surface(
        self, surface_id: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        return self._surface_request(
            "surface_read", {"surface_id": surface_id, "params": dict(params or {})}
        )

    def review_surface(
        self, surface_id: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        return self._surface_request(
            "surface_review", {"surface_id": surface_id, "params": dict(params or {})}
        )

    def invoke_surface_action(
        self, surface_id: str, action: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        return self._surface_request(
            "surface_action",
            {"surface_id": surface_id, "action": action, "params": dict(params or {})},
        )


def dispatch_surface_request(
    registry: SurfaceRegistry,
    request: Any,
    cluster_grant: Any,
) -> Any:
    if request.caller_node_id is None:
        raise RemoteAuthorizationError("caller identity required for surface requests")
    if registry is None:
        raise RemoteUnavailableError("surface sharing is unavailable")
    return registry.dispatch(
        request.caller_node_id,
        request.params["surface_id"],
        access={
            "surface_read": "read",
            "surface_review": "review",
            "surface_action": "action",
        }[request.op],
        action=request.params.get("action"),
        params=request.params.get("params", {}),
        cluster_sources=(request.caller_node_id,)
        if cluster_grant is not None
        else (),
    )
