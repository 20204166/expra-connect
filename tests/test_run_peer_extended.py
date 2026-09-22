import json
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from expra_connect import NodeId
from run_peer_extended import (
    CAPABILITY,
    _candidate_values,
    _diagnostics_values,
    _parser,
    _runtime,
    approve_surface_pairing,
    cleanup_runtime,
    connect_bidirectionally,
    format_terminal_event,
    main,
    record_surface_result,
    register_harness_surfaces,
    run_initiator,
    run_target,
    wait_for_matching_peer,
    write_event,
)


class ExtendedPeerEventWriterTests(unittest.TestCase):
    def test_surface_events_allowlist_only_redacted_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            with redirect_stdout(StringIO()):
                write_event(
                    report,
                    "surface_request",
                    surface_id="desktop",
                    access="read",
                    outcome="success",
                    error_type="ValueError",
                    payload={"secret": "must not persist"},
                )

            record = json.loads(report.read_text(encoding="utf-8"))[0]
            self.assertEqual(
                record,
                {
                    "event": "surface_request",
                    "sequence": 1,
                    "surface_id": "desktop",
                    "access": "read",
                    "outcome": "success",
                    "error_type": "ValueError",
                },
            )
            self.assertNotIn("must not persist", report.read_text(encoding="utf-8"))

    def test_record_surface_result_writes_no_handler_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            with redirect_stdout(StringIO()):
                record_surface_result(report, "device/status", "review", "denied")

            self.assertEqual(
                json.loads(report.read_text(encoding="utf-8"))[0],
                {
                    "event": "surface_result",
                    "sequence": 1,
                    "surface_id": "device/status",
                    "access": "review",
                    "outcome": "denied",
                },
            )

    def test_sequence_numbers_are_ordered_per_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            output = StringIO()
            with redirect_stdout(output):
                write_event(report, "started", role="target", state="started")
                write_event(report, "target_ready")

            records = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual([record["sequence"] for record in records], [1, 2])
            self.assertEqual(output.getvalue().splitlines()[0], "[01] started role=target version=- state=started")
            self.assertEqual(output.getvalue().splitlines()[1], "[02] target_ready")

    def test_write_event_appends_existing_report_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            report.write_text(
                json.dumps([{"sequence": 7, "event": "started", "role": "target"}]),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                write_event(report, "stopping")

            records = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(len(records), 2)
            self.assertEqual(records[-1], {"event": "stopping", "sequence": 8})

    def test_report_and_terminal_output_allowlist_sensitive_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            output = StringIO()
            with redirect_stdout(output):
                write_event(
                    report,
                    "error",
                    error_type="TimeoutError",
                    error="private exception detail",
                    private_key="private key",
                    hmac_secret="hmac secret",
                    invitation="invitation material",
                    fencing_token="fencing token",
                    transport_proof="transport proof",
                    raw_payload={"secret": "payload"},
                )

            record = json.loads(report.read_text(encoding="utf-8"))[0]
            self.assertEqual(record, {"event": "error", "error_type": "TimeoutError", "sequence": 1})
            self.assertEqual(output.getvalue().strip(), "[01] error type=TimeoutError")
            for secret in (
                "private exception detail",
                "private key",
                "hmac secret",
                "invitation material",
                "fencing token",
                "transport proof",
                "payload",
            ):
                self.assertNotIn(secret, output.getvalue())
                self.assertNotIn(secret, report.read_text(encoding="utf-8"))

    def test_terminal_formatter_does_not_render_unapproved_event_values(self) -> None:
        line = format_terminal_event(
            3,
            "started",
            role="initiator",
            state="started",
            node_id="must-not-print",
            private_key="must-not-print",
        )
        self.assertEqual(line, "[03] started role=initiator version=- state=started")

    def test_started_terminal_event_renders_approved_version(self) -> None:
        self.assertEqual(
            format_terminal_event(
                1, "started", role="target", version="1.2.3", state="started"
            ),
            "[01] started role=target version=1.2.3 state=started",
        )


class ExtendedPeerCleanupTests(unittest.TestCase):
    def test_cleanup_scaffold_shuts_down_runtime_after_failure(self) -> None:
        runtime = Mock()
        runtime.config = SimpleNamespace()
        try:
            with self.assertRaisesRegex(RuntimeError, "operation failed"):
                with cleanup_runtime(runtime):
                    raise RuntimeError("operation failed")
        finally:
            runtime.shutdown.assert_called_once_with()


class ExtendedPeerArgumentTests(unittest.TestCase):
    def test_parser_accepts_bidirectional_surfaces_without_changing_default(self) -> None:
        common = ["--role", "target", "--profile", "/tmp/profile"]
        self.assertFalse(_parser().parse_args(common).bidirectional_surfaces)
        self.assertTrue(
            _parser().parse_args(common + ["--bidirectional-surfaces"]).bidirectional_surfaces
        )

    def test_main_accepts_task_one_arguments_without_starting_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            arguments = [
                "run_peer_extended.py",
                "--role",
                "initiator",
                "--profile",
                directory,
                "--report",
                str(report),
                "--peer-id",
                "peer-a",
                "--wait",
                "2",
                "--advertise-address",
                "192.168.55.107",
                "--rotate-after",
                "3",
                "--reconnect-after-rotation",
                "--rotation-wait",
                "4",
                "--restart-check",
                "--revoke-self",
            ]
            with unittest.mock.patch("sys.argv", arguments), unittest.mock.patch(
                "run_peer_extended.ConnectRuntime"
            ) as runtime_type:
                runtime_type.return_value.start.return_value = SimpleNamespace(
                    state="started"
                )
                runtime_type.return_value.peers = ()
                self.assertEqual(main(), 2)


class ExtendedPeerStageTests(unittest.TestCase):
    def _status(self) -> SimpleNamespace:
        return SimpleNamespace(
            state=SimpleNamespace(value="started"),
            discovery_started=True,
            discovery_disabled=False,
            discovery_reason=None,
            bound_host="0.0.0.0",
            bound_port=27321,
            tls_fingerprint="fingerprint",
        )

    def _identity(self, value: str) -> SimpleNamespace:
        return SimpleNamespace(node_id=SimpleNamespace(value=value))

    def _candidate(self, value: str = "target") -> SimpleNamespace:
        return SimpleNamespace(
            stable_id=value,
            addresses=("192.168.55.107",),
            port=27321,
            endpoint_candidates=(),
            transport_generation=1,
            transport_fingerprint="fingerprint",
        )

    def test_register_harness_surfaces_uses_deterministic_structured_handlers(self) -> None:
        runtime = Mock()
        register_harness_surfaces(runtime)

        self.assertEqual(
            [call.args[0] for call in runtime.register_surface.call_args_list],
            ["desktop", "desktop/settings", "device/status"],
        )
        for call in runtime.register_surface.call_args_list:
            handlers = call.kwargs
            first_read = handlers["read"](NodeId("peer"), {"ignored": "input"})
            second_read = handlers["read"](NodeId("peer"), {})
            self.assertEqual(first_read, second_read)
            self.assertIsInstance(handlers["review"](NodeId("peer"), {}), dict)
            self.assertEqual(
                handlers["actions"]["save"](NodeId("peer"), {}),
                handlers["actions"]["save"](NodeId("other"), {"payload": "ignored"}),
            )

    def test_target_registers_capability_and_approves_pairing_before_start(self) -> None:
        runtime = Mock()
        runtime.start.return_value = self._status()
        runtime.identity = self._identity("target")
        runtime.peers = ()
        runtime.sharing.allow = Mock()
        args = SimpleNamespace(
            role="target",
            profile=Path("/tmp/target"),
            report=Path("/tmp/target-report.json"),
            advertise_address=["192.168.55.107"],
            wait=0,
        )
        with unittest.mock.patch("run_peer_extended.time.sleep"):
            self.assertEqual(run_target(args, runtime), 0)
        runtime.sharing.register.assert_called_once()
        runtime.start.assert_called_once_with()
        runtime.shutdown.assert_called_once_with()

        callback = runtime.config.on_pairing_request
        request = SimpleNamespace(
            caller_node_id=SimpleNamespace(value="initiator"), permissions=()
        )
        self.assertTrue(callback(request))
        runtime.sharing.allow.assert_called_once_with(
            request.caller_node_id, CAPABILITY
        )

    def test_surface_pairing_approves_trust_without_granting_surface_access(self) -> None:
        runtime = Mock()
        request = SimpleNamespace(
            caller_node_id=SimpleNamespace(value="initiator"), permissions=()
        )

        with unittest.mock.patch("run_peer_extended.write_event") as event:
            self.assertTrue(
                approve_surface_pairing(runtime, Path("/tmp/report"), request)
            )

        runtime.sharing.allow.assert_not_called()
        runtime.grant_surface_access.assert_not_called()
        self.assertEqual(event.call_args.args[1], "pairing_request")
        self.assertNotIn("caller_node_id", event.call_args.kwargs)

    def test_bidirectional_runtime_installs_pairing_callback_for_both_roles(self) -> None:
        for role in ("target", "initiator"):
            args = SimpleNamespace(
                role=role,
                profile=Path(f"/tmp/{role}"),
                report=Path(f"/tmp/{role}-report.json"),
                advertise_address=None,
                bidirectional_surfaces=True,
            )
            runtime = _runtime(args)
            self.assertIsNotNone(runtime.config.on_pairing_request)

    def test_one_way_initiator_does_not_install_pairing_callback(self) -> None:
        args = SimpleNamespace(
            role="initiator",
            profile=Path("/tmp/initiator"),
            report=Path("/tmp/initiator-report.json"),
            advertise_address=None,
            bidirectional_surfaces=False,
        )
        runtime = _runtime(args)
        self.assertIsNone(runtime.config.on_pairing_request)

    def test_wait_for_matching_peer_filters_by_public_stable_id(self) -> None:
        runtime = Mock()
        runtime.peers = (self._candidate("other"), self._candidate("target"))
        self.assertEqual(
            wait_for_matching_peer(runtime, "target", 0).stable_id,
            "target",
        )

    def test_connect_bidirectionally_pairs_before_connecting(self) -> None:
        runtime = Mock()
        runtime.pairing.trusted.get.return_value = None
        runtime.pair_peer.return_value = SimpleNamespace(peer_id=SimpleNamespace(value="peer"))
        provider = Mock()
        runtime.connect_peer.return_value = provider

        result = connect_bidirectionally(runtime, self._candidate("peer"))

        self.assertIs(result, provider)
        runtime.pair_peer.assert_called_once_with(NodeId("peer"))
        runtime.connect_peer.assert_called_once_with(NodeId("peer"))

    def test_bidirectional_initiator_orders_reverse_pair_and_connect_events(self) -> None:
        runtime = Mock()
        runtime.start.return_value = self._status()
        runtime.identity = self._identity("initiator")
        runtime.peers = (self._candidate("target"),)
        runtime.pairing.trusted.get.return_value = None
        runtime.pair_peer.return_value = SimpleNamespace()
        runtime.connect_peer.return_value = Mock()
        runtime.diagnostics.return_value = {"routes": [], "connections": []}
        args = SimpleNamespace(
            peer_id="target",
            wait=0,
            report=Path("/tmp/report"),
            bidirectional_surfaces=True,
        )

        with unittest.mock.patch("run_peer_extended.write_event") as event:
            self.assertEqual(run_initiator(args, runtime), 0)

        names = [call.args[1] for call in event.call_args_list]
        self.assertLess(names.index("reverse_paired"), names.index("reverse_connected"))
        self.assertNotIn("target", str(event.call_args_list))
        runtime.grant_surface_access.assert_not_called()

    def test_bidirectional_pair_failure_shuts_down_without_connecting(self) -> None:
        runtime = Mock()
        runtime.start.return_value = self._status()
        runtime.identity = self._identity("initiator")
        runtime.peers = (self._candidate("target"),)
        runtime.pairing.trusted.get.return_value = None
        runtime.pair_peer.side_effect = ValueError("private detail")
        args = SimpleNamespace(
            peer_id="target",
            wait=0,
            report=Path("/tmp/report"),
            bidirectional_surfaces=True,
        )

        with unittest.mock.patch("run_peer_extended.write_event") as event:
            self.assertEqual(run_initiator(args, runtime), 1)

        self.assertEqual(event.call_args.kwargs, {"error_type": "ValueError"})
        runtime.connect_peer.assert_not_called()
        runtime.shutdown.assert_called_once_with()

    def test_bidirectional_target_failure_shuts_down_runtime(self) -> None:
        runtime = Mock()
        runtime.start.return_value = self._status()
        runtime.identity = self._identity("target")
        runtime.peers = ()
        args = SimpleNamespace(
            role="target",
            report=Path("/tmp/target-report.json"),
            wait=0,
            bidirectional_surfaces=True,
            peer_id=None,
        )

        self.assertEqual(run_target(args, runtime), 2)
        runtime.shutdown.assert_called_once_with()

    def test_initiator_candidate_wait_timeout_gates_pair_and_connect(self) -> None:
        runtime = Mock()
        runtime.start.return_value = self._status()
        runtime.identity = self._identity("initiator")
        runtime.peers = ()
        args = SimpleNamespace(peer_id="target", wait=0, report=Path("/tmp/report"))
        self.assertEqual(run_initiator(args, runtime), 2)
        runtime.pair_peer.assert_not_called()
        runtime.connect_peer.assert_not_called()
        runtime.shutdown.assert_called_once_with()

    def test_initiator_pairs_connects_shares_and_collects_diagnostics(self) -> None:
        runtime = Mock()
        runtime.start.return_value = self._status()
        runtime.identity = self._identity("initiator")
        runtime.peers = (self._candidate(),)
        runtime.pairing.trusted.get.return_value = None
        runtime.pair_peer.return_value = SimpleNamespace(
            peer_id=SimpleNamespace(value="target"), permissions=frozenset({"read_state"})
        )
        provider = Mock()
        provider.request_shared.return_value = {"ok": True, "state": "ready"}
        runtime.connect_peer.return_value = provider
        runtime.diagnostics.return_value = {"status": {}, "routes": [], "connections": []}
        args = SimpleNamespace(peer_id="target", wait=0, report=Path("/tmp/report"))
        with unittest.mock.patch("run_peer_extended.write_event") as event:
            self.assertEqual(run_initiator(args, runtime), 0)
        self.assertEqual(
            [call.args[1] for call in event.call_args_list],
            ["started", "discovered", "paired", "connected", "shared_capability_result", "diagnostics"],
        )
        provider.request_shared.assert_called_once_with(CAPABILITY, {"source": "run_peer_extended.py"})
        runtime.shutdown.assert_called_once_with()

    def test_initiator_reports_type_only_errors_and_stops_after_pair_failure(self) -> None:
        runtime = Mock()
        runtime.start.return_value = self._status()
        runtime.identity = self._identity("initiator")
        runtime.peers = (self._candidate(),)
        runtime.pairing.trusted.get.return_value = None
        runtime.pair_peer.side_effect = ValueError("secret-bearing detail")
        args = SimpleNamespace(peer_id="target", wait=0, report=Path("/tmp/report"))
        with unittest.mock.patch("run_peer_extended.write_event") as event:
            self.assertEqual(run_initiator(args, runtime), 1)
        self.assertEqual(event.call_args.args[1], "error")
        self.assertEqual(event.call_args.kwargs, {"error_type": "ValueError"})
        runtime.connect_peer.assert_not_called()
        runtime.shutdown.assert_called_once_with()

    def test_target_rotates_once_and_reports_generation_only(self) -> None:
        runtime = Mock()
        runtime.start.return_value = self._status()
        runtime.identity = self._identity("target")
        runtime.transport_generations.current_generation = 2
        args = SimpleNamespace(
            role="target",
            report=Path("/tmp/target-report.json"),
            wait=0.02,
            rotate_after=0,
        )
        with unittest.mock.patch("run_peer_extended.time.sleep"), unittest.mock.patch(
            "run_peer_extended.write_event"
        ) as event:
            self.assertEqual(run_target(args, runtime), 0)
        self.assertEqual(
            [call.args[1] for call in event.call_args_list],
            ["started", "target_ready", "rotated"],
        )
        self.assertEqual(event.call_args_list[-1].kwargs, {"generation": 2})
        runtime.rotate_transport.assert_called_once_with()
        runtime.shutdown.assert_called_once_with()

    def test_initiator_reconnect_requires_changed_transport_generation(self) -> None:
        runtime = Mock()
        runtime.start.return_value = self._status()
        runtime.identity = self._identity("initiator")
        initial = self._candidate()
        rotated = self._candidate()
        rotated.transport_generation = 2
        runtime.peers = (initial,)
        runtime.pairing.trusted.get.return_value = Mock()
        provider = Mock()
        provider.request_shared.return_value = {"ok": True}
        runtime.connect_peer.return_value = provider
        runtime.reconnect_peer.return_value = provider
        args = SimpleNamespace(
            peer_id="target",
            wait=0,
            report=Path("/tmp/report"),
            reconnect_after_rotation=True,
            rotation_wait=1,
        )
        with unittest.mock.patch("run_peer_extended.write_event") as event, unittest.mock.patch(
            "run_peer_extended.wait_for_peer", side_effect=[initial, rotated]
        ):
            self.assertEqual(run_initiator(args, runtime), 0)
        self.assertIn("reconnected_after_rotation", [call.args[1] for call in event.call_args_list])
        runtime.reconnect_peer.assert_called_once_with(NodeId("target"))
        self.assertEqual(provider.request_shared.call_count, 2)

    def test_initiator_rotation_timeout_is_typed_error_and_gates_reconnect(self) -> None:
        runtime = Mock()
        runtime.start.return_value = self._status()
        runtime.identity = self._identity("initiator")
        runtime.peers = (self._candidate(),)
        runtime.pairing.trusted.get.return_value = Mock()
        provider = Mock()
        provider.request_shared.return_value = {"ok": True}
        runtime.connect_peer.return_value = provider
        args = SimpleNamespace(
            peer_id="target",
            wait=0,
            report=Path("/tmp/report"),
            reconnect_after_rotation=True,
            rotation_wait=1,
        )
        with unittest.mock.patch("run_peer_extended.write_event") as event, unittest.mock.patch(
            "run_peer_extended.wait_for_peer", side_effect=[runtime.peers[0], None]
        ):
            self.assertEqual(run_initiator(args, runtime), 1)
        self.assertEqual(event.call_args.args[1], "error")
        self.assertEqual(event.call_args.kwargs, {"error_type": "TimeoutError"})
        runtime.reconnect_peer.assert_not_called()

    def test_restart_check_uses_fresh_runtime_and_restores_trust(self) -> None:
        runtime = Mock()
        runtime.start.return_value = self._status()
        runtime.identity = self._identity("initiator")
        runtime.peers = (self._candidate(),)
        runtime.pairing.trusted.get.return_value = None
        runtime.pair_peer.return_value = SimpleNamespace(
            peer_id=SimpleNamespace(value="target"), permissions=frozenset()
        )
        provider = Mock()
        provider.request_shared.return_value = {"ok": True}
        runtime.connect_peer.return_value = provider
        restarted = Mock()
        restarted.start.return_value = self._status()
        restarted.pairing.trusted = {NodeId("target"): Mock()}
        args = SimpleNamespace(
            peer_id="target", wait=0, report=Path("/tmp/report"), restart_check=True
        )
        with unittest.mock.patch("run_peer_extended.write_event") as event:
            self.assertEqual(
                run_initiator(args, runtime, runtime_factory=Mock(return_value=restarted)), 0
            )
        self.assertIn("restored_trust", [call.args[1] for call in event.call_args_list])
        runtime.shutdown.assert_called_once_with()
        restarted.shutdown.assert_called_once_with()

    def test_revoke_self_requires_typed_denial(self) -> None:
        runtime = Mock()
        runtime.start.return_value = self._status()
        runtime.identity = self._identity("initiator")
        runtime.peers = (self._candidate(),)
        runtime.pairing.trusted.get.return_value = Mock()
        provider = Mock()
        provider.request_shared.side_effect = [{"ok": True}, PermissionError("secret")]
        runtime.connect_peer.return_value = provider
        args = SimpleNamespace(
            peer_id="target", wait=0, report=Path("/tmp/report"), revoke_self=True
        )
        with unittest.mock.patch("run_peer_extended.write_event") as event:
            self.assertEqual(run_initiator(args, runtime), 0)
        names = [call.args[1] for call in event.call_args_list]
        self.assertEqual(names[-3:-1], ["self_revoked", "post_revoke_denied"])
        self.assertEqual(event.call_args_list[-2].kwargs, {"error_type": "PermissionError"})
        provider.revoke_self.assert_called_once_with()

    def test_optional_candidate_and_diagnostics_state_is_malformed_safe(self) -> None:
        self.assertEqual(
            _candidate_values(SimpleNamespace(addresses="not-a-sequence")),
            {"address": None, "port": None, "source": "discovery"},
        )
        self.assertEqual(_diagnostics_values({"routes": "bad", "connections": None}), {
            "generation": None,
            "routes_count": None,
            "connections_count": None,
        })


if __name__ == "__main__":
    unittest.main()
