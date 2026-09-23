import tempfile
import time
import unittest
from pathlib import Path

from expra_connect.cluster.models import CapabilityGrant
from expra_connect.identity import NodeId
from expra_connect.models import READ_CAPABILITIES, NodePermission
from expra_connect.remote_models import NodeStatus
from expra_connect.remote_service import (
    AuthenticatedNodeProvider,
    MemoryRemoteTransport,
    RemoteAuthorizationError,
    RemoteExecutionError,
    RemoteService,
)
from expra_connect.runtime import ConnectConfig, ConnectRuntime
from expra_connect.surfaces import SurfaceAccess, SurfaceHandlerError
from expra_connect.wire_protocol import PeerGrant

SECRET = "a" * 64


class SurfaceIntegrationTests(unittest.TestCase):
    def _client(
        self,
        runtime: ConnectRuntime,
        caller: NodeId,
        *,
        cluster_grants: tuple[CapabilityGrant, ...] = (),
    ) -> AuthenticatedNodeProvider:
        identity = runtime.identity
        assert identity is not None
        service = RemoteService(
            node_id=identity.node_id,
            display_name="surface target",
            hostname="surface-target",
            platform="test",
            status=NodeStatus.ONLINE,
            capabilities=READ_CAPABILITIES,
            provider=object(),
            secret=identity.secret,
            grants={
                caller: PeerGrant(
                    caller, SECRET, frozenset({NodePermission.READ_STATE})
                )
            },
            cluster_capability_grants=cluster_grants,
            surface_registry=runtime._surface_registry,
        )
        return AuthenticatedNodeProvider(
            node_id=identity.node_id,
            caller_node_id=caller,
            secret=SECRET,
            transport=MemoryRemoteTransport(service),
        )

    def _runtime(self) -> ConnectRuntime:
        profile = tempfile.TemporaryDirectory()
        self.addCleanup(profile.cleanup)
        runtime = ConnectRuntime(
            ConnectConfig(
                profile_dir=Path(profile.name),
                discovery_enabled=False,
                preferred_port=0,
            )
        )
        self.assertEqual(runtime.start().state.value, "started")
        return runtime

    def test_pairing_alone_denies_until_each_surface_access_is_granted(self) -> None:
        runtime = self._runtime()
        self.addCleanup(runtime.shutdown)
        caller = NodeId("surface-caller")
        runtime.register_surface(
            "desktop/settings",
            read=lambda _peer, _params: {"theme": "dark"},
            review=lambda _peer, _params: {"pending": 1},
            actions={"save": lambda _peer, params: {"saved": params["theme"]}},
        )
        client = self._client(runtime, caller)

        with self.assertRaises(RemoteAuthorizationError):
            client.read_surface("desktop/settings")
        runtime.grant_surface_access(
            caller, "desktop/settings", access=SurfaceAccess.READ
        )
        self.assertEqual(client.read_surface("desktop/settings"), {"theme": "dark"})
        with self.assertRaises(RemoteAuthorizationError):
            client.review_surface("desktop/settings")
        with self.assertRaises(RemoteAuthorizationError):
            client.invoke_surface_action(
                "desktop/settings", "save", params={"theme": "light"}
            )

        runtime.grant_surface_access(caller, "desktop/settings", access="action")
        with self.assertRaises(RemoteAuthorizationError):
            client.invoke_surface_action(
                "desktop/settings", "save", params={"theme": "light"}
            )

    def test_nested_ids_expiry_stop_revoke_and_restart_cleanup(self) -> None:
        now = [10.0]
        runtime = self._runtime()
        self.addCleanup(runtime.shutdown)
        runtime._surface_registry._clock = lambda: now[0]
        caller = NodeId("cleanup-caller")
        runtime.register_surface(
            "workspace/panels/settings/advanced",
            read=lambda _peer, _params: {"ok": True},
        )
        client = self._client(runtime, caller)
        surface_id = "workspace/panels/settings/advanced"

        runtime.grant_surface_access(caller, surface_id, access="read", expires_at=11)
        self.assertEqual(client.read_surface(surface_id), {"ok": True})
        now[0] = 12
        with self.assertRaises(RemoteAuthorizationError):
            client.read_surface(surface_id)
        runtime.grant_surface_access(caller, surface_id, access="read")
        runtime.stop_surface_share(caller, surface_id)
        with self.assertRaises(RemoteAuthorizationError):
            client.read_surface(surface_id)
        runtime.grant_surface_access(caller, surface_id, access="read")
        runtime.revoke_surface_access(caller, surface_id)
        with self.assertRaises(RemoteAuthorizationError):
            client.read_surface(surface_id)
        runtime.grant_surface_access(caller, surface_id, access="read")
        runtime.shutdown()
        self.assertEqual(runtime.start().state.value, "started")
        restarted = self._client(runtime, caller)
        with self.assertRaises(RemoteAuthorizationError):
            restarted.read_surface(surface_id)

    def test_cluster_surface_grants_require_explicit_context_not_membership(
        self,
    ) -> None:
        runtime = self._runtime()
        self.addCleanup(runtime.shutdown)
        caller = NodeId("cluster-caller")
        target = runtime.identity
        assert target is not None
        runtime.register_surface(
            "cluster/nested/status", read=lambda _peer, _params: {"ok": True}
        )
        client = self._client(runtime, caller)
        with self.assertRaises(RemoteAuthorizationError):
            client.read_surface("cluster/nested/status")

        grant = CapabilityGrant(
            caller,
            target.node_id,
            frozenset({NodePermission.READ_STATE}),
            time.time() - 1.0,
            time.time() + 60.0,
        )
        runtime.grant_cluster_surface_access(
            caller, "cluster/nested/status", access="read"
        )
        with self.assertRaises(RemoteAuthorizationError):
            client.read_surface("cluster/nested/status")
        contextual = self._client(runtime, caller, cluster_grants=(grant,))
        self.assertEqual(contextual.read_surface("cluster/nested/status"), {"ok": True})

    def test_handler_errors_are_typed_and_redacted(self) -> None:
        runtime = self._runtime()
        self.addCleanup(runtime.shutdown)
        caller = NodeId("error-caller")

        def fail(_peer: NodeId, _params: dict[str, object]) -> object:
            raise SurfaceHandlerError("private handler detail")

        runtime.register_surface("desktop/failing", read=fail)
        runtime.grant_surface_access(caller, "desktop/failing", access="read")
        with self.assertRaises(RemoteExecutionError) as error:
            self._client(runtime, caller).read_surface("desktop/failing")
        self.assertEqual(str(error.exception), "execution_failed")
        self.assertNotIn("private", str(error.exception))


if __name__ == "__main__":
    unittest.main()
