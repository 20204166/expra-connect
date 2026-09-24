"""Authenticated provider request mechanics kept outside the API facade."""

from __future__ import annotations

import json
import secrets
from typing import Any

from .socket_transport import _check_cancelled, request_with_retry
from .wire_protocol import (
    OPERATION_SAFETY,
    RemoteAuthError,
    RemoteAuthorizationError,
    RemoteExecutionError,
    RemoteProtocolError,
    RemoteUnavailableError,
    sign_request,
    verify_response,
)


class ProviderRequestMixin:
    _invalidated: bool
    _node_id: Any
    _caller_node_id: Any
    _secret: str
    _transport: Any
    _clock: Any
    _freshness_seconds: float
    _session_id: str | None

    def _check_cancel(self, cancel_event: Any | None) -> None:
        """Delegate to the shared transport cancellation guard."""
        _check_cancelled(cancel_event)

    def _request(
        self,
        op: str,
        params: dict[str, Any],
        cancel_event: Any | None = None,
    ) -> dict[str, Any]:
        if self._invalidated:
            raise RemoteAuthError("remote provider has been revoked")
        self._check_cancel(cancel_event)
        request_id = secrets.token_hex(16)
        envelope = sign_request(
            node_id=self._node_id.value,
            op=op,
            params=params,
            request_id=request_id,
            nonce=secrets.token_hex(16),
            timestamp=self._clock(),
            secret=self._secret,
            caller_node_id=(
                self._caller_node_id.value if self._caller_node_id is not None else None
            ),
            session_id=self._session_id,
            resume=self._session_id is not None,
        )
        attempts = 2 if OPERATION_SAFETY.get(op) != "unsafe" else 1
        response_text = request_with_retry(
            self._transport.request,
            json.dumps(envelope),
            cancel_event,
            attempts,
        )
        try:
            response_envelope = json.loads(response_text)
        except (TypeError, ValueError) as error:
            raise RemoteProtocolError("response is not valid JSON") from error
        response = verify_response(
            response_envelope,
            secret=self._secret,
            clock=self._clock,
            freshness_seconds=self._freshness_seconds,
        )
        if response.node_id != self._node_id:
            raise RemoteAuthError("response came from the wrong node")
        if response.request_id != request_id:
            raise RemoteAuthError("response request id does not match")
        if response.session_id is not None:
            self._session_id = response.session_id
        if response.status == "error":
            if response.error == "permission_denied":
                raise RemoteAuthorizationError("caller lacks permission")
            if response.error == "capability_unavailable":
                raise RemoteAuthorizationError("node capability is unavailable")
            if response.error == "target_offline":
                raise RemoteUnavailableError("target is offline")
            raise RemoteExecutionError(response.error or "remote operation failed")
        if response.payload is None:
            raise RemoteProtocolError("successful response has no payload")
        return response.payload
