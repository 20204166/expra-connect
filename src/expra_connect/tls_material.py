"""Per-installation TLS material for the authenticated peer socket."""

from __future__ import annotations

import datetime
import hashlib
import logging
import os
import ssl
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TLSMaterial:
    certificate: Path
    private_key: Path
    fingerprint: str
    generation: int | None = None


def certificate_fingerprint(certificate: bytes) -> str:
    """Return the stable display/pinning fingerprint for DER certificate bytes."""

    digest = hashlib.sha256(certificate).hexdigest()
    return ":".join(digest[index : index + 4] for index in range(0, 64, 4))


def _chmod_best_effort(path: Path, mode: int) -> None:
    # Windows ACL semantics differ from POSIX; chmod(2) bits are best-effort.
    # The private key is stored in the app's private config directory; no
    # automatic icacls hardening is applied on Windows (platform limitation).
    try:
        os.chmod(path, mode)
    except OSError as error:
        LOGGER.debug("chmod %o on %s not applied: %s", mode, path, error)


def _generate_self_signed(
    directory: Path, node_id: str, certificate: Path, private_key: Path
) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    with tempfile.TemporaryDirectory(dir=directory, prefix=".peer-tls-") as tmp:
        temporary_key = Path(tmp) / "peer-tls.key"
        temporary_certificate = Path(tmp) / "peer-tls.crt"
        temporary_key.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        temporary_certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        _chmod_best_effort(temporary_key, 0o600)
        _chmod_best_effort(temporary_certificate, 0o644)
        os.replace(temporary_key, private_key)
        os.replace(temporary_certificate, certificate)


def ensure_tls_material(directory: Path, node_id: str) -> TLSMaterial:
    """Load or create the legacy generation-zero transport material."""
    return ensure_tls_material_generation(directory, node_id)


def ensure_tls_material_generation(
    directory: Path, node_id: str, generation: int | None = None
) -> TLSMaterial:
    """Load or create replaceable TLS material for one transport generation."""
    if generation is not None and (
        isinstance(generation, bool) or not isinstance(generation, int) or generation < 1
    ):
        raise ValueError("TLS generation must be a positive integer")
    directory.mkdir(parents=True, exist_ok=True)
    suffix = "" if generation is None else f"-{generation}"
    certificate = directory / f"peer-tls{suffix}.crt"
    private_key = directory / f"peer-tls{suffix}.key"
    if not certificate.exists() or not private_key.exists():
        _generate_self_signed(directory, node_id, certificate, private_key)
    _chmod_best_effort(private_key, 0o600)
    _chmod_best_effort(certificate, 0o644)
    der = ssl.PEM_cert_to_DER_cert(certificate.read_text(encoding="ascii"))
    return TLSMaterial(
        certificate, private_key, certificate_fingerprint(der), generation
    )


def server_context(material: TLSMaterial) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(material.certificate, material.private_key)
    return context


def client_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context
