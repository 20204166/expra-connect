import math
import unittest

from expra_connect.models import NodeCapability, NodePermission
from expra_connect.surface_protocol import (
    MAX_SURFACE_ID_LENGTH,
    MAX_SURFACE_PARAM_DEPTH,
    SURFACE_ACCESS_BY_OPERATION,
    SURFACE_ACTION,
    SURFACE_OPERATION_SAFETY,
    SURFACE_OPERATIONS,
    SURFACE_READ,
    SURFACE_REQUIRED_CAPABILITY,
    SURFACE_REVIEW,
    validate_surface_operation_params,
)
from expra_connect.wire_protocol import (
    OP_REQUIRED_CAPABILITY,
    OP_REQUIRED_PERMISSION,
    OPERATION_SAFETY,
    REMOTE_PROTOCOL_VERSION,
    IdempotencyCache,
    RemoteAuthError,
    RemoteProtocolError,
    ReplayCache,
    _signature,
    sign_request,
    sign_response,
    validate_operation_params,
    verify_request,
    verify_response,
)

TEST_SECRET = "a" * 64
TEST_TIMESTAMP = 10.0


class SurfaceOperationProtocolTests(unittest.TestCase):
    def test_surface_operations_have_complete_canonical_metadata(self) -> None:
        expected = {SURFACE_READ, SURFACE_REVIEW, SURFACE_ACTION}
        self.assertEqual(SURFACE_OPERATIONS, frozenset(expected))
        self.assertEqual(set(SURFACE_REQUIRED_CAPABILITY), expected)
        self.assertEqual(set(SURFACE_ACCESS_BY_OPERATION), expected)
        self.assertEqual(set(SURFACE_OPERATION_SAFETY), expected)
        self.assertIs(
            SURFACE_REQUIRED_CAPABILITY[SURFACE_READ], NodeCapability.READ_STATE
        )
        self.assertIs(
            SURFACE_REQUIRED_CAPABILITY[SURFACE_REVIEW], NodeCapability.READ_STATE
        )
        self.assertIs(
            SURFACE_REQUIRED_CAPABILITY[SURFACE_ACTION],
            NodeCapability.REMOTE_MANAGEMENT,
        )
        self.assertEqual(SURFACE_ACCESS_BY_OPERATION[SURFACE_READ], "read")
        self.assertEqual(SURFACE_ACCESS_BY_OPERATION[SURFACE_REVIEW], "review")
        self.assertEqual(SURFACE_ACCESS_BY_OPERATION[SURFACE_ACTION], "action")
        self.assertEqual(SURFACE_OPERATION_SAFETY[SURFACE_READ], "read")
        self.assertEqual(SURFACE_OPERATION_SAFETY[SURFACE_REVIEW], "read")
        self.assertEqual(SURFACE_OPERATION_SAFETY[SURFACE_ACTION], "unsafe")
        for operation in expected:
            with self.subTest(operation=operation):
                self.assertIs(
                    OP_REQUIRED_CAPABILITY[operation],
                    SURFACE_REQUIRED_CAPABILITY[operation],
                )
                self.assertIs(
                    OP_REQUIRED_PERMISSION[operation],
                    NodePermission(SURFACE_REQUIRED_CAPABILITY[operation].value),
                )
                self.assertEqual(
                    OPERATION_SAFETY[operation], SURFACE_OPERATION_SAFETY[operation]
                )
        self.assertIs(OP_REQUIRED_CAPABILITY["ping"], NodeCapability.READ_STATE)
        self.assertEqual(OPERATION_SAFETY["process_force_quit"], "unsafe")

    def test_surface_parameters_require_exact_fields_and_json_safe_data(self) -> None:
        validate_operation_params(SURFACE_READ, {"surface_id": "dashboard/main"})
        validate_operation_params(
            SURFACE_REVIEW, {"surface_id": "settings", "params": {"tab": 1}}
        )
        validate_operation_params(
            SURFACE_ACTION,
            {"surface_id": "settings", "action": "save", "params": {}},
        )
        invalid: tuple[tuple[str, dict[str, object]], ...] = (
            (SURFACE_READ, {}),
            (SURFACE_REVIEW, {"surface_id": ""}),
            (SURFACE_ACTION, {"surface_id": "settings"}),
            (SURFACE_ACTION, {"surface_id": "settings", "action": ""}),
            (SURFACE_READ, {"surface_id": "dashboard", "action": "save"}),
            (SURFACE_REVIEW, {"surface_id": "dashboard", "action": "save"}),
            (
                SURFACE_ACTION,
                {
                    "surface_id": "settings",
                    "action": "save",
                    "caller_node_id": "caller",
                },
            ),
            (
                SURFACE_READ,
                {"surface_id": "dashboard", "params": {"bad": object()}},
            ),
            (
                SURFACE_READ,
                {"surface_id": "dashboard", "params": {"huge": 10**5000}},
            ),
            (SURFACE_REVIEW, {"surface_id": "dashboard", "extra": True}),
        )
        for operation, params in invalid:
            with (
                self.subTest(operation=operation, params=params),
                self.assertRaises(RemoteProtocolError),
            ):
                validate_operation_params(operation, params)

    def test_each_surface_operation_rejects_invalid_identifier_shapes(self) -> None:
        cases = (
            (SURFACE_READ, {"surface_id": None}),
            (SURFACE_REVIEW, {"surface_id": 1}),
            (SURFACE_ACTION, {"surface_id": "surface", "action": None}),
            (SURFACE_ACTION, {"surface_id": "surface", "action": 1}),
        )
        for operation, params in cases:
            with (
                self.subTest(operation=operation, params=params),
                self.assertRaises(RemoteProtocolError),
            ):
                validate_operation_params(operation, params)
        for operation in (SURFACE_READ, SURFACE_REVIEW, SURFACE_ACTION):
            with (
                self.subTest(operation=operation),
                self.assertRaises(RemoteProtocolError),
            ):
                validate_operation_params(
                    operation,
                    {
                        "surface_id": "surface",
                        **({"action": "save"} if operation == SURFACE_ACTION else {}),
                        "params": {},
                        "unexpected": True,
                    },
                )

    def test_surface_validator_rejects_unknown_operations_with_controlled_error(
        self,
    ) -> None:
        with self.assertRaises(RemoteProtocolError):
            validate_surface_operation_params(
                "surface_delete", {"surface_id": "settings"}, RemoteProtocolError
            )

    def test_surface_ids_use_one_canonical_bound(self) -> None:
        self.assertEqual(MAX_SURFACE_ID_LENGTH, 128)
        for operation, params in (
            (SURFACE_READ, {"surface_id": "x" * MAX_SURFACE_ID_LENGTH}),
            (
                SURFACE_ACTION,
                {
                    "surface_id": "x" * MAX_SURFACE_ID_LENGTH,
                    "action": "y" * MAX_SURFACE_ID_LENGTH,
                },
            ),
        ):
            with self.subTest(operation=operation):
                validate_operation_params(operation, params)
        with self.assertRaises(RemoteProtocolError):
            validate_operation_params(
                SURFACE_ACTION,
                {
                    "surface_id": "x" * (MAX_SURFACE_ID_LENGTH + 1),
                    "action": "save",
                },
            )

    def test_surface_params_reject_cycles_and_depth_beyond_boundary(self) -> None:
        cyclic_dict: dict[str, object] = {}
        cyclic_dict["self"] = cyclic_dict
        cyclic_list: list[object] = []
        cyclic_list.append(cyclic_list)
        for value in (cyclic_dict, cyclic_list):
            with (
                self.subTest(value_type=type(value).__name__),
                self.assertRaises(RemoteProtocolError),
            ):
                validate_operation_params(
                    SURFACE_READ,
                    {"surface_id": "surface", "params": {"value": value}},
                )

        nested: object = "leaf"
        for _ in range(MAX_SURFACE_PARAM_DEPTH - 1):
            nested = [nested]
        validate_operation_params(
            SURFACE_READ, {"surface_id": "surface", "params": {"value": nested}}
        )
        nested = "leaf"
        for _ in range(MAX_SURFACE_PARAM_DEPTH):
            nested = [nested]
        with self.assertRaises(RemoteProtocolError):
            validate_operation_params(
                SURFACE_READ,
                {"surface_id": "surface", "params": {"value": nested}},
            )

    def test_existing_operation_validation_remains_compatible(self) -> None:
        validate_operation_params("ping", {})
        with self.assertRaises(RemoteProtocolError):
            validate_operation_params("not_an_operation", {})

    def test_invalid_authenticated_node_ids_are_typed_wire_errors(self) -> None:
        request = sign_request(
            node_id="local",
            op="ping",
            params={},
            request_id="request",
            nonce="nonce",
            timestamp=TEST_TIMESTAMP,
            secret=TEST_SECRET,
        )
        bounded_replay_cache = ReplayCache(clock=lambda: TEST_TIMESTAMP, max_entries=1)
        for cache_name, replay_cache in (
            ("default", ReplayCache(clock=lambda: TEST_TIMESTAMP)),
            ("bounded", bounded_replay_cache),
        ):
            with (
                self.subTest(cache=cache_name),
                self.assertRaises(RemoteAuthError),
            ):
                verify_request(
                    request,
                    secret=TEST_SECRET,
                    clock=lambda: TEST_TIMESTAMP,
                    freshness_seconds=60.0,
                    replay_cache=replay_cache,
                )
        valid_request = sign_request(
            node_id="peer",
            op="ping",
            params={},
            request_id="valid-request",
            nonce="valid-nonce",
            timestamp=TEST_TIMESTAMP,
            secret=TEST_SECRET,
        )
        verify_request(
            valid_request,
            secret=TEST_SECRET,
            clock=lambda: TEST_TIMESTAMP,
            freshness_seconds=60.0,
            replay_cache=bounded_replay_cache,
        )
        response = sign_response(
            node_id="local",
            request_id="request",
            status="ok",
            timestamp=TEST_TIMESTAMP,
            secret=TEST_SECRET,
            payload={},
        )
        with self.assertRaises(RemoteAuthError):
            verify_response(
                response,
                secret=TEST_SECRET,
                clock=lambda: TEST_TIMESTAMP,
                freshness_seconds=60.0,
            )

    def test_resume_without_session_id_is_rejected(self) -> None:
        fields = {
            "v": REMOTE_PROTOCOL_VERSION,
            "node_id": "peer",
            "op": "ping",
            "params": {},
            "request_id": "resume-request",
            "nonce": "resume-nonce",
            "ts": TEST_TIMESTAMP,
            "resume": True,
        }
        envelope = dict(fields)
        envelope["sig"] = _signature(TEST_SECRET, fields)
        with self.assertRaises(RemoteAuthError):
            verify_request(
                envelope,
                secret=TEST_SECRET,
                clock=lambda: TEST_TIMESTAMP,
                freshness_seconds=60.0,
                replay_cache=ReplayCache(clock=lambda: TEST_TIMESTAMP),
            )

    def test_signed_wire_values_reject_non_finite_json_numbers(self) -> None:
        with self.assertRaises(ValueError):
            sign_request(
                node_id="peer",
                op="ping",
                params={"value": math.nan},
                request_id="request",
                nonce="nonce",
                timestamp=TEST_TIMESTAMP,
                secret=TEST_SECRET,
            )
        request = sign_request(
            node_id="peer",
            op="ping",
            params={},
            request_id="request",
            nonce="nonce",
            timestamp=TEST_TIMESTAMP,
            secret=TEST_SECRET,
        )
        request["params"] = {"value": math.nan}
        with self.assertRaises(RemoteProtocolError):
            verify_request(
                request,
                secret=TEST_SECRET,
                clock=lambda: TEST_TIMESTAMP,
                freshness_seconds=60.0,
                replay_cache=ReplayCache(clock=lambda: TEST_TIMESTAMP),
            )

        with self.assertRaises(ValueError):
            sign_response(
                node_id="peer",
                request_id="request",
                status="ok",
                timestamp=TEST_TIMESTAMP,
                secret=TEST_SECRET,
                payload={"value": math.nan},
            )
        response = sign_response(
            node_id="peer",
            request_id="request",
            status="ok",
            timestamp=TEST_TIMESTAMP,
            secret=TEST_SECRET,
            payload={},
        )
        response["payload"] = {"value": math.nan}
        with self.assertRaises(RemoteProtocolError):
            verify_response(
                response,
                secret=TEST_SECRET,
                clock=lambda: TEST_TIMESTAMP,
                freshness_seconds=60.0,
            )

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


class WireValidationEdgeCaseTests(unittest.TestCase):
    def test_operation_validation_rejects_non_object_parameters(self) -> None:
        with self.assertRaises(RemoteProtocolError):
            validate_operation_params("ping", None)  # type: ignore[arg-type]

    def test_snapshot_validation_rejects_non_finite_payload_values(self) -> None:
        with self.assertRaises(RemoteProtocolError):
            validate_operation_params(
                "worker_snapshot",
                {
                    "cluster_id": "cluster",
                    "epoch": 1,
                    "fencing_token": "fence",
                    "payload": {"value": math.nan},
                },
            )

    def test_revoke_member_uses_role_operation_validation(self) -> None:
        validate_operation_params(
            "revoke_member",
            {
                "cluster_id": "cluster",
                "epoch": 1,
                "fencing_token": "fence",
                "target_node_id": "worker",
            },
        )

    def test_corrupt_idempotency_state_is_ignored(self) -> None:
        class CorruptStateStore:
            def __init__(self, raw: object) -> None:
                self._raw = raw

            def load(self) -> object:
                return self._raw

        for case, raw in enumerate(
            (
                {"entries": None},
                {
                    "entries": [
                        {
                            "key": ["peer", "request"],
                            "fingerprint": "fingerprint",
                            "result": {},
                            "remaining": 10**5000,
                        }
                    ]
                },
            )
        ):
            with self.subTest(case=case):
                self.assertEqual(
                    len(IdempotencyCache(state_store=CorruptStateStore(raw))), 0
                )

    def test_idempotency_save_failure_does_not_release_cached_operation(self) -> None:
        class FailingStateStore:
            def load(self) -> dict[str, list[object]]:
                return {"entries": []}

            def save(self, _state: object) -> None:
                raise OSError("persistence unavailable")

        calls = 0

        def operation() -> dict[str, bool]:
            nonlocal calls
            calls += 1
            return {"ok": True}

        cache = IdempotencyCache(state_store=FailingStateStore())
        with self.assertRaises(OSError):
            cache.run(("peer", "request"), "fingerprint", operation)
        with self.assertRaises(OSError):
            cache.run(("peer", "request"), "fingerprint", operation)
        self.assertEqual(calls, 1)
