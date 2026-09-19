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
