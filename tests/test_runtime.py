import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from expra_connect import __version__
from expra_connect.cluster import Cluster, ClusterRole
from expra_connect.discovery_full import DiscoveryAdvertisement
from expra_connect.identity import NodeId, NodeIdentity, node_identity_fingerprint
from expra_connect.models import DiscoveredNodeCandidate, NodeCapability, NodePermission
from expra_connect.pairing import PeerGrant, TrustedPeer
from expra_connect.remote_service import AuthenticatedNodeProvider, PairingTransaction
from expra_connect.role_engine import ClusterRole as RegistryClusterRole
from expra_connect.runtime import (
    ConnectConfig,
    ConnectRuntime,
    RuntimeState,
    RuntimeStatus,
)
from expra_connect.socket_transport import TLSRemoteTransport
from expra_connect.wire_protocol import (
    CapabilityElevationRequest,
    PairingControlRequest,
    PairingRequest,
    RemoteRequest,
)


class _UnavailableDiscoveryBackend:
    available = False

    def __init__(self, _listener: object) -> None:
        pass

    def start(self, _advertisement: DiscoveryAdvertisement) -> None:
        raise AssertionError("unavailable backend must not start")

    def stop(self) -> None:
        pass


class _RecordingDiscoveryBackend:
    available = True

    def __init__(self, _listener: object) -> None:
        self.advertisement: object | None = None

    def start(self, advertisement: DiscoveryAdvertisement) -> None:
        self.advertisement = advertisement

    def stop(self) -> None:
        pass


class RuntimeConfigurationTests(unittest.TestCase):
    def test_construction_has_no_network_effects_and_uses_mature_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = ConnectConfig(profile_dir=Path(directory))
            runtime = ConnectRuntime(config)

            self.assertTrue(config.discovery_enabled)
            self.assertEqual(config.bind_host, "0.0.0.0")
            self.assertEqual(config.preferred_port, 27321)
            self.assertFalse(runtime.started)
            self.assertFalse((Path(directory) / "identity.json").exists())
            self.assertEqual(config.app_version, __version__)

    def test_explicit_advertised_addresses_are_normalized(self) -> None:
        config = ConnectConfig(
            profile_dir=Path("/tmp/expra-connect-test"),
            advertised_addresses=("192.168.1.20", "10.0.0.20"),
        )
        self.assertEqual(config.advertised_addresses, ("192.168.1.20", "10.0.0.20"))

    def test_empty_advertised_address_policy_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ConnectConfig(
                profile_dir=Path("/tmp/expra-connect-test"),
                advertised_addresses=(),
            )


class RuntimeClusterOperationTests(unittest.TestCase):
    def _runtime_with_cluster(self) -> ConnectRuntime:
        runtime = ConnectRuntime(
            ConnectConfig(
                profile_dir=Path("/tmp/expra-connect-test"), cluster_enabled=True
            )
        )
        runtime._cluster = Cluster(NodeId("coord"), clock=lambda: 100.0)
        runtime._persistence_ready = True
        return runtime

    def _request(
        self, operation: str, caller: str, params: dict[str, Any]
    ) -> RemoteRequest:
        return RemoteRequest(
            node_id=NodeId("coord"),
            caller_node_id=NodeId(caller),
            op=operation,
            params=params,
            request_id="request",
            nonce="nonce",
            timestamp=0.0,
        )

    def test_role_handler_consumes_invite_and_returns_current_fence(self) -> None:
        runtime = self._runtime_with_cluster()
        cluster = runtime.cluster
        assert cluster is not None
        invite = cluster.create_invite(NodeId("worker"), now=100.0)

        with patch.object(runtime, "_save_persisted_state", return_value=True):
            result = runtime._handle_role_request(
                self._request(
                    "consume_invite",
                    "worker",
                    {
                        "token": invite.token,
                        "cluster_id": cluster.cluster_id,
                        "epoch": cluster.epoch.epoch,
                        "fencing_token": cluster.epoch.fencing_token,
                    },
                )
            )

        self.assertEqual(result["target_node_id"], "worker")
        self.assertTrue(cluster.is_member(NodeId("worker")))

    def test_role_handler_rejects_non_coordinator_membership_mutation(self) -> None:
        runtime = self._runtime_with_cluster()
        cluster = runtime.cluster
        assert cluster is not None
        cluster.assign(NodeId("worker"), ClusterRole.WORKER)
        params = {
            "target_node_id": "worker",
            "roles": [ClusterRole.WORKER.value],
            "cluster_id": cluster.cluster_id,
            "epoch": cluster.epoch.epoch,
            "fencing_token": cluster.epoch.fencing_token,
        }

        with self.assertRaises(PermissionError):
            runtime._handle_role_request(self._request("assign_role", "worker", params))

    def test_role_handler_renews_only_for_current_coordinator(self) -> None:
        runtime = self._runtime_with_cluster()
        cluster = runtime.cluster
        assert cluster is not None
        params = {
            "cluster_id": cluster.cluster_id,
            "epoch": cluster.epoch.epoch,
            "fencing_token": cluster.epoch.fencing_token,
        }

        result = runtime._handle_role_request(
            self._request("renew_coordinator_lease", "coord", params)
        )
        self.assertTrue(result["ok"])
        with self.assertRaises(PermissionError):
            runtime._handle_role_request(
                self._request("renew_coordinator_lease", "worker", params)
            )

    def test_role_handler_rolls_back_membership_when_cluster_save_fails(self) -> None:
        runtime = self._runtime_with_cluster()
        cluster = runtime.cluster
        assert cluster is not None
        params = {
            "target_node_id": "worker",
            "roles": [ClusterRole.WORKER.value],
            "cluster_id": cluster.cluster_id,
            "epoch": cluster.epoch.epoch,
            "fencing_token": cluster.epoch.fencing_token,
        }

        with (
            patch.object(runtime, "_save_persisted_state", return_value=False),
            self.assertRaises(RuntimeError),
        ):
            runtime._handle_role_request(self._request("assign_role", "coord", params))

        self.assertFalse(cluster.is_member(NodeId("worker")))

    def test_identity_fingerprint_matches_mature_namespaced_format(self) -> None:
        digest = hashlib.sha256(b"system-analyzer-node:peer").hexdigest()
        expected = ":".join(digest[index : index + 4] for index in range(0, 64, 4))

        self.assertEqual(node_identity_fingerprint("peer"), expected)

    def test_start_persists_identity_before_listener_and_can_disable_discovery(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )

            status = runtime.start()

            self.assertTrue(runtime.started)
            self.assertTrue(status.listener_started)
            self.assertFalse(status.discovery_started)
            self.assertTrue(status.discovery_disabled)
            self.assertGreater(status.bound_port or 0, 0)
            self.assertTrue((Path(directory) / "identity.json").exists())
            self.assertEqual(status.tls_fingerprint and len(status.tls_fingerprint), 79)
            registry = runtime.registry
            identity = runtime.identity
            assert registry is not None
            assert identity is not None
            record = registry.record(identity.node_id)
            assert record is not None
            self.assertEqual(record.membership.value, "not_joined")
            runtime.shutdown()

    def test_start_and_shutdown_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )

            first = runtime.start()
            second = runtime.start()
            runtime.shutdown()
            runtime.shutdown()

            self.assertIs(first, second)
            self.assertFalse(runtime.started)

    def test_restart_reuses_identity_tls_and_persisted_grants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = ConnectConfig(
                profile_dir=Path(directory),
                discovery_enabled=False,
                preferred_port=0,
            )
            first_runtime = ConnectRuntime(config)
            first_runtime.start()
            assert first_runtime.identity is not None
            assert first_runtime.pairing is not None
            peer_id = NodeId("trusted-peer")
            first_runtime.pairing.grants[peer_id] = PeerGrant(
                caller_id=peer_id,
                secret="a" * 64,
                permissions=frozenset({"read_state"}),
                transport_fingerprint="transport-a",
            )
            first_identity = first_runtime.identity.node_id
            first_tls = first_runtime.status.tls_fingerprint
            first_runtime.shutdown()

            second_runtime = ConnectRuntime(config)
            second_runtime.start()

            assert second_runtime.identity is not None
            assert second_runtime.pairing is not None
            self.assertEqual(second_runtime.identity.node_id, first_identity)
            self.assertEqual(second_runtime.status.tls_fingerprint, first_tls)
            self.assertIn(peer_id, second_runtime.pairing.grants)
            second_runtime.shutdown()

    def test_old_generation_discovery_callback_is_ignored_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events: list[tuple[str, object]] = []
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                    on_discovery=lambda kind, payload: events.append((kind, payload)),
                )
            )
            runtime.start()
            runtime.shutdown()
            runtime.start()

            runtime._on_discovery(1, "candidate", object())

            self.assertEqual(events, [])
            runtime.shutdown()

    def test_callback_in_progress_cannot_commit_after_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events: list[tuple[str, object]] = []
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                    on_discovery=lambda kind, payload: events.append((kind, payload)),
                )
            )
            runtime.start()
            candidate = DiscoveredNodeCandidate(
                stable_id="blocked-peer",
                hostname="peer.local",
                addresses=("192.168.1.9",),
                port=27321,
                service_name="blocked-peer._expra-peer._tcp.local.",
                app_version="1.0",
                protocol_version="1",
                platform=None,
                connectable=True,
                compatible=True,
                last_seen=1.0,
            )
            entered = threading.Event()
            release = threading.Event()

            def blocked(_candidate: DiscoveredNodeCandidate) -> bool:
                entered.set()
                release.wait(1.0)
                return True

            with patch.object(runtime, "_trusted_candidate_is_valid", blocked):
                callback_thread = threading.Thread(
                    target=lambda: runtime._on_discovery(1, "candidate", candidate)
                )
                callback_thread.start()
                self.assertTrue(entered.wait(1.0))
                shutdown_thread = threading.Thread(target=runtime.shutdown)
                shutdown_thread.start()
                time.sleep(0.05)
                release.set()
                callback_thread.join(2.0)
                shutdown_thread.join(2.0)

            self.assertEqual(runtime.peers, ())
            self.assertEqual(runtime.status.state.value, "stopped")

    def test_identity_persistence_failure_prevents_listener_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(profile_dir=Path(directory), preferred_port=0)
            )
            with patch(
                "expra_connect.runtime.NodeIdentity.save",
                side_effect=OSError("profile is read-only"),
            ):
                status = runtime.start()

            self.assertEqual(status.state.value, "persistence_failed")
            self.assertFalse(status.listener_started)
            self.assertFalse(runtime.started)

    def test_trusted_rediscovery_rejects_a_wrong_transport_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events: list[tuple[str, object]] = []
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                    on_discovery=lambda kind, payload: events.append((kind, payload)),
                )
            )
            runtime.start()
            assert runtime.pairing is not None
            peer_id = NodeId("trusted-peer")
            runtime.pairing.trusted[peer_id] = TrustedPeer(
                peer_id=peer_id,
                secret="a" * 64,
                permissions=frozenset({"read_state"}),
                transport_fingerprint="expected-fingerprint",
            )
            candidate = DiscoveredNodeCandidate(
                stable_id=peer_id.value,
                hostname="peer.local",
                addresses=("192.168.1.8",),
                port=27321,
                service_name="trusted-peer._expra-peer._tcp.local.",
                app_version="1.0",
                protocol_version="1",
                platform=None,
                connectable=True,
                compatible=True,
                last_seen=1.0,
                transport_fingerprint="wrong-fingerprint",
            )

            runtime._on_discovery(1, "candidate", candidate)

            self.assertEqual(events, [])
            self.assertEqual(runtime.peers, ())
            runtime.shutdown()

    def test_trusted_rediscovery_accepts_a_signed_new_transport_generation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            runtime.start()
            assert runtime.pairing is not None
            peer_id = NodeId("rotating-peer")
            peer_identity = NodeIdentity.create(peer_id)
            runtime.pairing.trusted[peer_id] = TrustedPeer(
                peer_id=peer_id,
                secret="a" * 64,
                permissions=frozenset({"read_state"}),
                transport_fingerprint="transport-one",
                root_public_key=peer_identity.root_public_key,
                transport_generation=1,
            )
            fingerprint = "transport-two"
            candidate = DiscoveredNodeCandidate(
                stable_id=peer_id.value,
                hostname="peer.local",
                addresses=("192.168.1.8",),
                port=27321,
                service_name="rotating-peer._expra-peer._tcp.local.",
                app_version="1.0",
                protocol_version="1",
                platform=None,
                connectable=True,
                compatible=True,
                last_seen=1.0,
                transport_fingerprint=fingerprint,
                root_public_key=peer_identity.root_public_key,
                transport_generation=2,
                transport_proof=peer_identity.sign_transport_proof(2, fingerprint),
            )

            runtime._on_discovery(1, "candidate", candidate)

            self.assertEqual(runtime.peers, (candidate,))
            runtime.shutdown()

    def test_discovery_loss_does_not_erase_trust_or_membership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            runtime.start()
            assert runtime.pairing is not None
            assert runtime.registry is not None
            assert runtime.identity is not None
            peer_id = NodeId("trusted-peer")
            runtime.pairing.trusted[peer_id] = TrustedPeer(
                peer_id=peer_id,
                secret="a" * 64,
                permissions=frozenset({"read_state"}),
            )
            runtime.registry.observe(peer_id, frozenset())
            runtime.registry.promote(peer_id, permissions=frozenset())
            runtime.registry.join(
                peer_id,
                role=RegistryClusterRole.WORKER,
                coordinator_id=runtime.identity.node_id,
            )
            runtime._peers[peer_id.value] = DiscoveredNodeCandidate(
                stable_id=peer_id.value,
                hostname="peer.local",
                addresses=("192.168.1.8",),
                port=27321,
                service_name="trusted-peer._expra-peer._tcp.local.",
                app_version="1.0",
                protocol_version="1",
                platform=None,
                connectable=True,
                compatible=True,
                last_seen=1.0,
            )

            runtime._on_discovery(1, "lost", peer_id.value)

            self.assertIn(peer_id, runtime.pairing.trusted)
            record = runtime.registry.record(peer_id)
            assert record is not None
            self.assertEqual(record.membership.value, "worker")
            runtime.shutdown()

    def test_discovery_backend_failure_keeps_listener_and_reports_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    preferred_port=0,
                    discovery_backend_factory=cast(
                        Any,
                        lambda listener: _UnavailableDiscoveryBackend(listener),
                    ),
                )
            )

            status = runtime.start()

            self.assertTrue(status.listener_started)
            self.assertFalse(status.discovery_started)
            self.assertFalse(status.discovery_disabled)
            self.assertEqual(
                status.discovery_reason, "python-zeroconf is not installed"
            )
            runtime.shutdown()

    def test_discovery_backend_constructor_failure_is_terminal_and_clean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:

            def factory(_listener: object) -> object:
                raise RuntimeError("backend constructor failed")

            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    preferred_port=0,
                    discovery_backend_factory=cast(Any, factory),
                )
            )

            status = runtime.start()

            self.assertEqual(status.state.value, "started")
            self.assertTrue(status.listener_started)
            self.assertFalse(status.discovery_started)
            self.assertIn("backend constructor failed", status.discovery_reason or "")
            runtime.shutdown()

    def test_pairing_and_revocation_delegate_to_pairing_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            runtime.start()
            peer_id = NodeId("pairing-peer")
            transaction = runtime.begin_pairing(peer_id)
            grant = runtime.approve_pairing(
                transaction.transaction_id, frozenset({"read_state"})
            )

            self.assertEqual(grant.caller_id, peer_id)
            assert runtime.pairing is not None
            self.assertIn(peer_id, runtime.pairing.grants)
            runtime.revoke_peer(peer_id)
            self.assertNotIn(peer_id, runtime.pairing.grants)
            runtime.shutdown()

    def test_pairing_changes_update_live_listener_grants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            runtime.start()
            peer_id = NodeId("live-peer")
            transaction = runtime.begin_pairing(peer_id)
            runtime.approve_pairing(
                transaction.transaction_id, frozenset({"read_state"})
            )
            assert runtime._server is not None
            service = runtime._server._service

            self.assertIn(peer_id, service._grants)
            runtime.revoke_peer(peer_id)
            self.assertNotIn(peer_id, service._grants)
            runtime.shutdown()

    def test_network_pairing_composes_handlers_and_default_read_capability(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                    on_pairing_request=lambda _request: True,
                )
            )
            runtime.start()
            assert runtime.identity is not None
            assert runtime._service is not None
            self.assertIn(NodeCapability.READ_STATE, runtime._service._capabilities)
            peer_id = NodeId("network-peer")
            request = PairingRequest(
                caller_node_id=peer_id,
                identity_fingerprint="peer-identity",
                transport_fingerprint="peer-transport",
                proposed_secret="b" * 64,
                permissions=frozenset({NodePermission.READ_STATE}),
            )

            response = runtime._handle_pairing_request(request)
            control = PairingControlRequest(
                operation="pair_confirm",
                transaction_id=response["transaction_id"],
                caller_node_id=peer_id,
                identity_fingerprint="peer-identity",
                transport_fingerprint="peer-transport",
                secret="b" * 64,
                permissions=frozenset({NodePermission.READ_STATE}),
                expires_at=response["expires_at"],
            )

            self.assertTrue(runtime._handle_pairing_control(control))
            self.assertIn(peer_id, runtime._service._grants)
            runtime.shutdown()

    def test_two_runtime_tls_pairing_and_authenticated_hello(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory) / "first",
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            second = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory) / "second",
                    discovery_enabled=False,
                    preferred_port=0,
                    on_pairing_request=lambda _request: True,
                )
            )
            first_status = first.start()
            second_status = second.start()
            assert first.identity is not None
            assert second.identity is not None
            assert second_status.bound_port is not None
            assert second_status.tls_fingerprint is not None
            transport = TLSRemoteTransport(
                "127.0.0.1",
                second_status.bound_port,
                expected_fingerprint=second_status.tls_fingerprint,
            )
            secret = "c" * 64
            response = AuthenticatedNodeProvider.request_pairing(
                transport=transport,
                caller_node_id=first.identity.node_id,
                identity_fingerprint=node_identity_fingerprint(first.identity.node_id),
                transport_fingerprint=first_status.tls_fingerprint or "",
                proposed_secret=secret,
                permissions=frozenset({NodePermission.READ_STATE}),
            )
            assert isinstance(response, dict)
            transaction = PairingTransaction(
                transaction_id=response["transaction_id"],
                caller_node_id=response["caller_node_id"],
                identity_fingerprint=response["identity_fingerprint"],
                transport_fingerprint=response["transport_fingerprint"],
                secret=response["secret"],
                permissions=frozenset({NodePermission.READ_STATE}),
                expires_at=response["expires_at"],
                transport=transport,
            )

            self.assertTrue(AuthenticatedNodeProvider.confirm_pairing(transaction))
            provider = AuthenticatedNodeProvider(
                node_id=second.identity.node_id,
                caller_node_id=first.identity.node_id,
                secret=secret,
                transport=transport,
            )
            self.assertEqual(provider.hello()["node_id"], second.identity.node_id.value)
            first.shutdown()
            second.shutdown()

    def test_runtime_pair_peer_and_connect_peer_compose_real_tls_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory) / "first",
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            second = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory) / "second",
                    discovery_enabled=False,
                    preferred_port=0,
                    on_pairing_request=lambda _request: True,
                )
            )
            first.start()
            second.start()
            assert first.identity is not None
            assert second.identity is not None
            assert second.status.bound_port is not None
            assert second.status.tls_fingerprint is not None
            first._peers[second.identity.node_id.value] = DiscoveredNodeCandidate(
                stable_id=second.identity.node_id.value,
                hostname="127.0.0.1",
                addresses=("127.0.0.1",),
                port=second.status.bound_port,
                service_name="_expra-peer._tcp.local.",
                app_version=__version__,
                protocol_version="1",
                platform="linux",
                connectable=True,
                compatible=True,
                last_seen=time.time(),
                identity_fingerprint=node_identity_fingerprint(second.identity.node_id),
                transport_fingerprint=second.status.tls_fingerprint,
            )

            trusted = first.pair_peer(second.identity.node_id)
            provider = first.connect_peer(second.identity.node_id)

            self.assertEqual(trusted.peer_id, second.identity.node_id)
            self.assertEqual(provider.hello()["node_id"], second.identity.node_id.value)
            assert second.pairing is not None
            self.assertFalse(second.pairing.pending)
            assert second._service is not None
            self.assertEqual(
                second._service._grants[first.identity.node_id].permissions,
                frozenset({NodePermission.READ_STATE}),
            )
            self.assertEqual(
                provider.revoke_self(),
                {"revoked": True, "node_id": first.identity.node_id.value},
            )
            self.assertNotIn(first.identity.node_id, second.pairing.grants)
            first.shutdown()
            second.shutdown()

    def test_pair_peer_aborts_target_grant_when_local_commit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory) / "first",
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            second = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory) / "second",
                    discovery_enabled=False,
                    preferred_port=0,
                    on_pairing_request=lambda _request: True,
                )
            )
            first.start()
            second.start()
            assert first.identity is not None
            assert second.identity is not None
            assert second.status.bound_port is not None
            assert second.status.tls_fingerprint is not None
            first._peers[second.identity.node_id.value] = DiscoveredNodeCandidate(
                stable_id=second.identity.node_id.value,
                hostname="127.0.0.1",
                addresses=("127.0.0.1",),
                port=second.status.bound_port,
                service_name="_expra-peer._tcp.local.",
                app_version=__version__,
                protocol_version="1",
                platform="linux",
                connectable=True,
                compatible=True,
                last_seen=time.time(),
                identity_fingerprint=node_identity_fingerprint(second.identity.node_id),
                transport_fingerprint=second.status.tls_fingerprint,
            )

            assert first._network_pairing is not None
            with (
                patch.object(first._network_pairing, "_persist", return_value=False),
                self.assertRaises(RuntimeError),
            ):
                first.pair_peer(second.identity.node_id)

            assert first.pairing is not None
            assert second.pairing is not None
            assert first.identity is not None
            self.assertNotIn(first.identity.node_id, second.pairing.grants)
            self.assertNotIn(second.identity.node_id, first.pairing.trusted)
            first.shutdown()
            second.shutdown()

    def test_pair_peer_cancellation_leaves_no_pending_trust(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory) / "first",
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            second = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory) / "second",
                    discovery_enabled=False,
                    preferred_port=0,
                    on_pairing_request=lambda _request: True,
                )
            )
            first.start()
            second.start()
            assert first.identity is not None
            assert second.identity is not None
            assert second.status.bound_port is not None
            assert second.status.tls_fingerprint is not None
            first._peers[second.identity.node_id.value] = DiscoveredNodeCandidate(
                stable_id=second.identity.node_id.value,
                hostname="127.0.0.1",
                addresses=("127.0.0.1",),
                port=second.status.bound_port,
                service_name="_expra-peer._tcp.local.",
                app_version=__version__,
                protocol_version="1",
                platform="linux",
                connectable=True,
                compatible=True,
                last_seen=time.time(),
                identity_fingerprint=node_identity_fingerprint(second.identity.node_id),
                transport_fingerprint=second.status.tls_fingerprint,
            )
            cancel_event = threading.Event()
            cancel_event.set()

            with self.assertRaises(RuntimeError):
                first.pair_peer(second.identity.node_id, cancel_event=cancel_event)

            assert first.pairing is not None
            assert second.pairing is not None
            self.assertFalse(first.pairing.pending)
            self.assertFalse(first.pairing.trusted)
            self.assertFalse(second.pairing.pending)
            self.assertFalse(second.pairing.grants)
            first.shutdown()
            second.shutdown()

    def test_elevation_uses_host_approval_and_refreshes_live_grant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                    on_elevation_request=lambda _request: True,
                )
            )
            runtime.start()
            assert runtime.identity is not None
            assert runtime.pairing is not None
            peer_id = NodeId("elevation-peer")
            grant = PeerGrant(
                caller_id=peer_id,
                secret="d" * 64,
                permissions=frozenset({NodePermission.READ_STATE.value}),
                identity_fingerprint="identity",
                transport_fingerprint="transport",
            )
            runtime.pairing.grants[peer_id] = grant
            request = CapabilityElevationRequest(
                caller_node_id=peer_id,
                identity_fingerprint="identity",
                transport_fingerprint="transport",
                current_secret=grant.secret,
                proposed_secret="e" * 64,
                permissions=frozenset({NodePermission.REMOTE_MANAGEMENT}),
            )

            self.assertTrue(runtime._handle_elevation_request(request))
            self.assertEqual(
                runtime.pairing.grants[peer_id].permissions,
                frozenset({NodePermission.REMOTE_MANAGEMENT.value}),
            )
            runtime.shutdown()

    def test_malformed_trust_state_is_structured_and_not_overwritten_on_shutdown(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            original = json.dumps({"grants": {"unexpected": "shape"}})
            (profile / "trust.json").write_text(original, encoding="utf-8")
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=profile,
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )

            status = runtime.start()
            runtime.shutdown()

            self.assertEqual(status.state.value, "persistence_failed")
            self.assertEqual((profile / "trust.json").read_text(), original)

    def test_invalid_persisted_permission_is_not_rewritten_after_start_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            NodeIdentity.create().save(profile / "identity.json")
            original = json.dumps(
                {
                    "grants": [
                        {
                            "caller_id": "peer",
                            "secret": "a" * 64,
                            "permissions": ["not-a-permission"],
                        }
                    ]
                }
            )
            (profile / "trust.json").write_text(original, encoding="utf-8")
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=profile,
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )

            status = runtime.start()
            runtime.shutdown()

            self.assertEqual(status.state.value, "persistence_failed")
            self.assertEqual((profile / "trust.json").read_text(), original)

    def test_invalid_approval_does_not_mutate_pairing_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            runtime.start()
            peer_id = NodeId("invalid-permission-peer")
            transaction = runtime.begin_pairing(peer_id)

            with self.assertRaises(ValueError):
                runtime.approve_pairing(
                    transaction.transaction_id, frozenset({"process_termination"})
                )

            assert runtime.pairing is not None
            self.assertNotIn(peer_id, runtime.pairing.grants)
            self.assertIn(transaction.transaction_id, runtime.pairing.pending)
            runtime.shutdown()

    def test_approval_persistence_failure_is_reported_and_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            runtime.start()
            peer_id = NodeId("disk-full-peer")
            transaction = runtime.begin_pairing(peer_id)

            with (
                patch(
                    "expra_connect.runtime.JsonStateStore.save",
                    side_effect=OSError("disk full"),
                ),
                self.assertRaises(RuntimeError),
            ):
                runtime.approve_pairing(
                    transaction.transaction_id, frozenset({"read_state"})
                )

            assert runtime.pairing is not None
            self.assertNotIn(peer_id, runtime.pairing.grants)
            runtime.shutdown()

    def test_concurrent_start_returns_one_listener(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            entered_save = threading.Event()
            original_save = NodeIdentity.save

            def slow_save(identity: NodeIdentity, path: Path) -> None:
                entered_save.set()
                time.sleep(0.1)
                original_save(identity, path)

            results: list[RuntimeStatus] = []

            with patch.object(NodeIdentity, "save", slow_save):
                threads = [
                    threading.Thread(target=lambda: results.append(runtime.start()))
                    for _ in range(2)
                ]
                for thread in threads:
                    thread.start()
                self.assertTrue(entered_save.wait(1.0))
                for thread in threads:
                    thread.join(2.0)

            self.assertEqual(len(results), 2)
            self.assertEqual(
                {item.bound_port for item in results}, {results[0].bound_port}
            )
            runtime.shutdown()

    def test_revoke_peer_revokes_registry_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            runtime.start()
            assert runtime.registry is not None
            peer_id = NodeId("authorized-peer")
            runtime.registry.observe(peer_id, frozenset({NodeCapability.READ_STATE}))
            runtime.registry.promote(
                peer_id, permissions=frozenset({NodeCapability.READ_STATE})
            )

            runtime.revoke_peer(peer_id)

            record = runtime.registry.record(peer_id)
            assert record is not None
            self.assertEqual(record.trust.value, "revoked")
            runtime.shutdown()

    def test_advertisement_uses_actual_listener_endpoint_and_tls_fingerprint(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backends: list[_RecordingDiscoveryBackend] = []

            def factory(listener: object) -> _RecordingDiscoveryBackend:
                backend = _RecordingDiscoveryBackend(listener)
                backends.append(backend)
                return backend

            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    preferred_port=0,
                    discovery_backend_factory=factory,
                )
            )

            status = runtime.start()
            advertisement = backends[0].advertisement
            assert isinstance(advertisement, DiscoveryAdvertisement)

            self.assertTrue(status.discovery_started)
            self.assertEqual(advertisement.port, status.bound_port)
            assert runtime.identity is not None
            self.assertEqual(
                advertisement.identity_fingerprint,
                node_identity_fingerprint(runtime.identity.node_id),
            )
            self.assertEqual(
                advertisement.transport_fingerprint, status.tls_fingerprint
            )
            runtime.shutdown()

    def test_diagnostics_include_operational_metadata_but_no_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            runtime.start()
            diagnostics = runtime.diagnostics()
            serialized = json.dumps(diagnostics, default=str)
            self.assertEqual(diagnostics["transport"]["current_generation"], 1)
            self.assertIn("root_fingerprint", diagnostics["identity"])
            self.assertNotIn(
                runtime.identity.secret if runtime.identity else "", serialized
            )
            runtime.shutdown()

    def test_transport_rotation_restarts_listener_with_new_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=Path(directory),
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            runtime.start()
            old_fingerprint = runtime.status.tls_fingerprint
            status = runtime.rotate_transport()
            self.assertEqual(status.state, RuntimeState.STARTED)
            assert runtime.transport_generations is not None
            self.assertEqual(runtime.transport_generations.current_generation, 2)
            self.assertNotEqual(status.tls_fingerprint, old_fingerprint)
            runtime.shutdown()


if __name__ == "__main__":
    unittest.main()
