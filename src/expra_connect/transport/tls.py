"""Public TLS surface backed by the extracted cross-platform implementation."""

from .tls_material import (
    TLSMaterial,
    certificate_fingerprint,
    client_context,
    ensure_tls_material,
    server_context,
)

__all__ = [
    "TLSMaterial",
    "certificate_fingerprint",
    "client_context",
    "ensure_tls_material",
    "server_context",
]
