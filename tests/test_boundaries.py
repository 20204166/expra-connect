import unittest


class BoundaryTests(unittest.TestCase):
    def test_shared_permissions_have_canonical_data_owner(self) -> None:
        import expra_connect
        from expra_connect.data.permissions import (
            READ_CAPABILITIES,
            READ_PERMISSIONS,
            NodeCapability,
            NodePermission,
        )

        self.assertIs(expra_connect.NodeCapability, NodeCapability)
        self.assertIs(expra_connect.NodePermission, NodePermission)
        self.assertEqual(
            [item.value for item in NodeCapability],
            ["read_state", "remote_management"],
        )
        self.assertEqual(
            [item.value for item in NodePermission],
            [
                "read_state",
                "remote_management",
                "process_termination",
                "process_force_termination",
                "cleanup",
            ],
        )
        self.assertEqual(READ_CAPABILITIES, frozenset({NodeCapability.READ_STATE}))
        self.assertEqual(READ_PERMISSIONS, frozenset({NodePermission.READ_STATE}))

    def test_process_action_kind_is_owned_by_remote_models(self) -> None:
        from expra_connect.remote.models import ProcessActionKind

        self.assertEqual(
            [item.value for item in ProcessActionKind],
            ["request", "force", "request_quit", "force_quit"],
        )

    def test_public_package_does_not_import_application_modules(self) -> None:
        import expra_connect

        names = set(expra_connect.__dict__)
        self.assertFalse(any("tk" in name.lower() for name in names))
