import unittest

from expra_connect.models import NodeCapability, NodePermission
from expra_connect.wire_protocol import (
    OP_REQUIRED_CAPABILITY,
    OP_REQUIRED_PERMISSION,
    OPERATION_SAFETY,
    RemoteProtocolError,
    validate_operation_params,
)


class SurfaceOperationProtocolTests(unittest.TestCase):
    def test_surface_operations_have_read_metadata_without_changing_old_operations(
        self,
    ) -> None:
        for operation in ("surface_read", "surface_review", "surface_action"):
            with self.subTest(operation=operation):
                self.assertIs(
                    OP_REQUIRED_CAPABILITY[operation], NodeCapability.READ_STATE
                )
                self.assertIs(
                    OP_REQUIRED_PERMISSION[operation], NodePermission.READ_STATE
                )
                self.assertEqual(OPERATION_SAFETY[operation], "read")
        self.assertIs(OP_REQUIRED_CAPABILITY["ping"], NodeCapability.READ_STATE)
        self.assertEqual(OPERATION_SAFETY["process_force_quit"], "unsafe")

    def test_surface_parameters_require_exact_fields_and_json_safe_data(self) -> None:
        validate_operation_params("surface_read", {"surface_id": "dashboard/main"})
        validate_operation_params(
            "surface_review", {"surface_id": "settings", "params": {"tab": 1}}
        )
        validate_operation_params(
            "surface_action",
            {"surface_id": "settings", "action": "save", "params": {}},
        )
        invalid = (
            ("surface_read", {}),
            ("surface_review", {"surface_id": ""}),
            ("surface_action", {"surface_id": "settings"}),
            ("surface_action", {"surface_id": "settings", "action": ""}),
            (
                "surface_read",
                {"surface_id": "dashboard", "params": {"bad": object()}},
            ),
            ("surface_review", {"surface_id": "dashboard", "extra": True}),
        )
        for operation, params in invalid:
            with (
                self.subTest(operation=operation, params=params),
                self.assertRaises(RemoteProtocolError),
            ):
                validate_operation_params(operation, params)

    def test_existing_operation_validation_remains_compatible(self) -> None:
        validate_operation_params("ping", {})
        with self.assertRaises(RemoteProtocolError):
            validate_operation_params("not_an_operation", {})

    def test_capability_request_reuses_bounded_opaque_capability_validation(
        self,
    ) -> None:
        validate_operation_params(
            "capability_request", {"capability": "app/foo.v1", "params": {}}
        )
        validate_operation_params(
            "capability_request", {"capability": "x" * 128, "params": {}}
        )
        for capability in ("", " ", "x" * 129, 1):
            with (
                self.subTest(capability=capability),
                self.assertRaises(RemoteProtocolError),
            ):
                validate_operation_params(
                    "capability_request", {"capability": capability, "params": {}}
                )
