import ssl
import tempfile
import unittest
from pathlib import Path

from expra_connect.server import RemoteSocketServer
from expra_connect.socket_transport import TLSRemoteTransport
from expra_connect.tls import (
    certificate_fingerprint,
    ensure_tls_material,
    server_context,
)
from expra_connect.tls_material import ensure_tls_material_generation
from expra_connect.wire_protocol import RemoteAuthError


class TLSMaterialTests(unittest.TestCase):
    def test_generation_material_uses_distinct_durable_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = ensure_tls_material_generation(Path(directory), "peer-a", 1)
            second = ensure_tls_material_generation(Path(directory), "peer-a", 2)
            self.assertEqual(first.generation, 1)
            self.assertEqual(second.generation, 2)
            self.assertNotEqual(first.fingerprint, second.fingerprint)
            self.assertTrue(first.certificate.name.endswith("-1.crt"))

    def test_material_is_reused_and_fingerprint_is_encoding_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = ensure_tls_material(Path(directory), "peer-a")
            second = ensure_tls_material(Path(directory), "peer-a")
            self.assertEqual(first.fingerprint, second.fingerprint)
            self.assertEqual(first.private_key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                certificate_fingerprint(
                    ssl.PEM_cert_to_DER_cert(
                        first.certificate.read_text(encoding="ascii")
                    )
                ),
                first.fingerprint,
            )

    def test_pinned_tls_transport_round_trip_and_wrong_pin_rejection(self) -> None:
        class Service:
            def handle(self, payload: str) -> str:
                return payload

        with tempfile.TemporaryDirectory() as directory:
            material = ensure_tls_material(Path(directory), "peer-tls")
            server = RemoteSocketServer(Service(), ssl_context=server_context(material))
            server.start()
            try:
                transport = TLSRemoteTransport(
                    "127.0.0.1",
                    server.bound_port or 0,
                    expected_fingerprint=material.fingerprint,
                )
                self.assertEqual(transport.request('{"ok":true}'), '{"ok":true}')
                wrong = TLSRemoteTransport(
                    "127.0.0.1",
                    server.bound_port or 0,
                    expected_fingerprint="00" * 32,
                )
                with self.assertRaises(RemoteAuthError):
                    wrong.request('{"ok":true}')
            finally:
                server.stop()

    def test_partial_material_is_regenerated_and_private_key_stays_private(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = ensure_tls_material(root, "peer-partial")
            first.private_key.unlink()
            second = ensure_tls_material(root, "peer-partial")
            self.assertTrue(second.private_key.exists())
            self.assertEqual(second.private_key.stat().st_mode & 0o777, 0o600)
            self.assertNotEqual(first.fingerprint, second.fingerprint)
