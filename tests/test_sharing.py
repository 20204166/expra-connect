import unittest

from expra_connect.identity import NodeId
from expra_connect.sharing import CapabilityShare


class SharingTests(unittest.TestCase):
    def test_target_owned_capability_requires_explicit_permission(self) -> None:
        share = CapabilityShare()
        share.register("demo.read_state", lambda _peer, _params: {"ok": True})
        with self.assertRaises(PermissionError):
            share.request(NodeId("peer"), "demo.read_state")
        share.allow(NodeId("peer"), "demo.read_state")
        self.assertEqual(share.request(NodeId("peer"), "demo.read_state"), {"ok": True})

    def test_request_preserves_an_explicit_empty_parameter_mapping(self) -> None:
        received: list[dict[str, object]] = []

        def handler(_peer: NodeId, params: dict[str, object]) -> object:
            received.append(params)
            return None

        params: dict[str, object] = {}
        share = CapabilityShare()
        share.register("demo.read_state", handler)
        share.allow(NodeId("peer"), "demo.read_state")
        share.request(NodeId("peer"), "demo.read_state", params)
        self.assertIs(received[0], params)
