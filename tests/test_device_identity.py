import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from expra_connect.device_identity import (
    DeviceHardwareHint,
    DeviceIdentity,
    DeviceIdentityError,
    DeviceIdentityView,
    normalize_mac_addresses,
)
from expra_connect.identity import NodeId, NodeIdentity
from expra_connect.runtime import ConnectConfig, ConnectRuntime


class _HardwareProvider:
    def __init__(self, macs: tuple[str, ...] = (), machine_id: str | None = None):
        self.macs = macs
        self.machine = machine_id

    def mac_addresses(self) -> tuple[str, ...]:
        return self.macs

    def machine_id(self) -> str | None:
        return self.machine


class DeviceIdentityTests(unittest.TestCase):
    def test_first_run_creates_ed25519_identity_and_public_view(self) -> None:
        identity = DeviceIdentity.create(
            NodeId("peer-a"),
            hardware_provider=_HardwareProvider(("AA:BB:CC:DD:EE:01",), "machine-a"),
        )

        self.assertEqual(identity.node_id, NodeId("peer-a"))
        self.assertTrue(identity.fingerprint.startswith("ed25519:"))
        self.assertEqual(len(identity.public_key), 32)
        self.assertIsInstance(identity.public_view(), DeviceIdentityView)
        self.assertNotIn("private", repr(identity).lower())

    def test_restart_loads_exactly_the_same_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device_identity.json"
            first = DeviceIdentity.create(NodeId("peer-a"))
            first.save(path)

            second = DeviceIdentity.load(path, NodeId("peer-a"))

            self.assertEqual(second, first)
            self.assertEqual(second.public_key, first.public_key)
            self.assertEqual(second.fingerprint, first.fingerprint)

    def test_device_identity_binds_to_existing_node_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device_identity.json"
            DeviceIdentity.create(NodeId("peer-a")).save(path)

            with self.assertRaises(DeviceIdentityError):
                DeviceIdentity.load(path, NodeId("peer-b"))

    def test_legacy_profile_creates_device_identity_without_changing_node_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            node_identity = NodeIdentity.create(NodeId("legacy-node"))
            node_identity.save(profile / "identity.json")
            runtime = ConnectRuntime(ConnectConfig(profile_dir=profile))

            runtime._load_identity()

            self.assertIsNotNone(runtime.identity)
            self.assertEqual(runtime.identity.node_id, NodeId("legacy-node"))
            self.assertTrue((profile / "device_identity.json").exists())
            self.assertEqual(
                DeviceIdentity.load(
                    profile / "device_identity.json", NodeId("legacy-node")
                ).node_id,
                NodeId("legacy-node"),
            )

    def test_existing_trust_cluster_and_transport_files_are_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            NodeIdentity.create(NodeId("legacy-node")).save(profile / "identity.json")
            originals = {
                name: '{"schema_version": 1, "preserved": true}'
                for name in ("trust.json", "cluster.json", "transport.json")
            }
            for name, value in originals.items():
                (profile / name).write_text(value, encoding="utf-8")

            runtime = ConnectRuntime(ConnectConfig(profile_dir=profile))
            runtime._load_identity()

            for name, value in originals.items():
                self.assertEqual((profile / name).read_text(encoding="utf-8"), value)

    def test_corrupt_private_key_fails_closed_without_regeneration(self) -> None:
        self._assert_invalid_document(private_key=base64.b64encode(b"bad").decode())

    def test_corrupt_public_key_fails_closed(self) -> None:
        self._assert_invalid_document(public_key=base64.b64encode(b"bad").decode())

    def test_public_private_key_mismatch_fails_closed(self) -> None:
        first = DeviceIdentity.create(NodeId("peer-a"))
        second = DeviceIdentity.create(NodeId("peer-a"))
        document = json.loads(first.to_json())
        document["private_key"] = base64.b64encode(
            second._private_key_bytes  # noqa: SLF001 - corruption fixture
        ).decode("ascii")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device_identity.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(DeviceIdentityError):
                DeviceIdentity.load(path, NodeId("peer-a"))

    def test_fingerprint_mismatch_fails_closed(self) -> None:
        self._assert_invalid_document(fingerprint="ed25519:" + "0" * 64)

    def test_malformed_and_unsupported_schema_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device_identity.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(DeviceIdentityError):
                DeviceIdentity.load(path, NodeId("peer-a"))
            path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
            with self.assertRaises(DeviceIdentityError):
                DeviceIdentity.load(path, NodeId("peer-a"))

    def test_missing_private_key_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device_identity.json"
            document = json.loads(DeviceIdentity.create(NodeId("peer-a")).to_json())
            del document["private_key"]
            path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaises(DeviceIdentityError):
                DeviceIdentity.load(path, NodeId("peer-a"))

    def test_nonfinite_creation_time_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device_identity.json"
            document = json.loads(DeviceIdentity.create(NodeId("peer-a")).to_json())
            document["created_at"] = float("nan")
            path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaises(DeviceIdentityError):
                DeviceIdentity.load(path, NodeId("peer-a"))

    def test_hardware_hint_with_digest_but_no_sources_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device_identity.json"
            document = json.loads(DeviceIdentity.create(NodeId("peer-a")).to_json())
            document["hardware_hint_digest"] = "a" * 64
            document["hardware_hint_sources"] = []
            path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaises(DeviceIdentityError):
                DeviceIdentity.load(path, NodeId("peer-a"))

    def test_hardware_provider_failure_does_not_block_creation(self) -> None:
        class FailingProvider:
            def mac_addresses(self) -> tuple[str, ...]:
                raise RuntimeError("hardware unavailable")

            def machine_id(self) -> str | None:
                raise RuntimeError("hardware unavailable")

        identity = DeviceIdentity.create(
            NodeId("peer-a"), hardware_provider=FailingProvider()
        )

        self.assertIsNone(identity.hardware_hint)

    def test_wrong_key_encoding_fails_closed(self) -> None:
        self._assert_invalid_document(private_key="not-base64")

    def test_runtime_reports_existing_node_id_mismatch_without_starting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            NodeIdentity.create(NodeId("node-a")).save(profile / "identity.json")
            DeviceIdentity.create(NodeId("node-b")).save(
                profile / "device_identity.json"
            )

            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=profile,
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )

            status = runtime.start()

            self.assertEqual(status.state.value, "persistence_failed")
            self.assertFalse(runtime.started)

    def test_existing_node_id_is_preserved_when_device_identity_is_added(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            NodeIdentity.create(NodeId("original-node")).save(profile / "identity.json")
            runtime = ConnectRuntime(ConnectConfig(profile_dir=profile))

            runtime._load_identity()

            assert runtime.identity is not None
            assert runtime._device_identity is not None
            self.assertEqual(runtime.identity.node_id, NodeId("original-node"))
            self.assertEqual(runtime._device_identity.node_id, NodeId("original-node"))

    def test_node_id_change_after_device_creation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            NodeIdentity.create(NodeId("original-node")).save(profile / "identity.json")
            runtime = ConnectRuntime(ConnectConfig(profile_dir=profile))
            runtime._load_identity()

            identity_document = json.loads(
                (profile / "identity.json").read_text(encoding="utf-8")
            )
            identity_document["node_id"] = "changed-node"
            (profile / "identity.json").write_text(
                json.dumps(identity_document), encoding="utf-8"
            )
            restarted = ConnectRuntime(ConnectConfig(profile_dir=profile))

            status = restarted.start()

            self.assertEqual(status.state.value, "persistence_failed")
            self.assertFalse(restarted.started)

    def test_hardware_hint_changes_do_not_rotate_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device_identity.json"
            first = DeviceIdentity.create(
                NodeId("peer-a"),
                hardware_provider=_HardwareProvider(("aa:bb:cc:dd:ee:01",), "machine-a"),
            )
            first.save(path)
            restored = DeviceIdentity.load(
                path,
                NodeId("peer-a"),
                hardware_provider=_HardwareProvider(("aa:bb:cc:dd:ee:02",), "machine-b"),
            )

            self.assertEqual(restored.fingerprint, first.fingerprint)
            self.assertTrue(restored.hardware_hint_changed)

    def test_ip_hostname_and_vpn_changes_do_not_rotate_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            first_runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=profile,
                    hostname="host-a",
                    advertised_addresses=("10.0.0.10",),
                    device_hardware_provider=_HardwareProvider(
                        ("aa:bb:cc:dd:ee:01",), "machine-a"
                    ),
                )
            )
            first_runtime._load_identity()
            first = first_runtime.device_identity

            second_runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=profile,
                    hostname="host-b",
                    advertised_addresses=("10.0.0.11", "10.8.0.11"),
                    device_hardware_provider=_HardwareProvider(
                        ("aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"), "machine-a"
                    ),
                )
            )
            second_runtime._load_identity()
            restored = second_runtime.device_identity

            assert first is not None
            assert restored is not None
            self.assertEqual(restored.fingerprint, first.fingerprint)
            self.assertTrue(restored.hardware_hint_changed)

    def test_missing_hardware_hints_still_allow_creation(self) -> None:
        identity = DeviceIdentity.create(NodeId("peer-a"), hardware_provider=_HardwareProvider())
        self.assertIsNone(identity.hardware_hint)

    def test_multiple_and_duplicate_macs_normalize_deterministically(self) -> None:
        self.assertEqual(
            normalize_mac_addresses(
                (
                    "AA:BB:CC:DD:EE:02",
                    "aa-bb-cc-dd-ee-01",
                    "AA:BB:CC:DD:EE:02",
                    "00:00:00:00:00:00",
                    "ff:ff:ff:ff:ff:ff",
                    "01:00:00:00:00:01",
                )
            ),
            ("aabbccddee01", "aabbccddee02"),
        )

    def test_raw_hardware_values_are_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device_identity.json"
            DeviceIdentity.create(
                NodeId("peer-a"),
                hardware_provider=_HardwareProvider(
                    ("AA:BB:CC:DD:EE:01",), "machine-id-secret"
                ),
            ).save(path)
            document = path.read_text(encoding="utf-8")
            self.assertNotIn("AA:BB:CC:DD:EE:01", document)
            self.assertNotIn("aabbccddee01", document)
            self.assertNotIn("machine-id-secret", document)

    def test_private_key_is_not_in_view_or_runtime_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=profile,
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            runtime.start()
            view = runtime.device_identity
            diagnostics = json.dumps(runtime.diagnostics(), default=str)

            self.assertIsInstance(view, DeviceIdentityView)
            self.assertNotIn("private_key", diagnostics)
            self.assertNotIn("_private_key_bytes", repr(view))
            self.assertNotIn(
                base64.b64encode(runtime._device_identity._private_key_bytes).decode(),
                diagnostics,
            )
            runtime.shutdown()

    def test_private_file_permissions_are_restrictive_where_supported(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX permission bits are not authoritative on Windows")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device_identity.json"
            DeviceIdentity.create(NodeId("peer-a")).save(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_sign_verify_and_mutation_rejection(self) -> None:
        identity = DeviceIdentity.create(NodeId("peer-a"))
        message = b"device identity test"
        signature = identity.sign(message)

        self.assertTrue(identity.verify(message, signature))
        self.assertFalse(identity.verify(b"mutated", signature))
        self.assertFalse(DeviceIdentity.create(NodeId("peer-a")).verify(message, signature))

    def test_persistence_failure_does_not_leave_identity_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device_identity.json"
            with patch(
                "expra_connect.device_identity._atomic_write",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaises(OSError):
                    DeviceIdentity.create(NodeId("peer-a")).save(path)
            self.assertFalse(path.exists())

    def _assert_invalid_document(self, **updates: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device_identity.json"
            document = json.loads(DeviceIdentity.create(NodeId("peer-a")).to_json())
            document.update(updates)
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(DeviceIdentityError):
                DeviceIdentity.load(path, NodeId("peer-a"))
