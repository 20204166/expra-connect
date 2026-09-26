import tempfile
import unittest
from pathlib import Path

from expra_connect.device_identity import DeviceIdentity
from expra_connect.identity import NodeId, NodeIdentity
from expra_connect.observability import ObservabilityWatcher
from expra_connect.runtime import ConnectConfig, ConnectRuntime, RuntimeState


class ProfileObservabilityTests(unittest.TestCase):
    def test_legacy_profile_migrations_and_lock_lifecycle_are_observed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            identity = NodeIdentity.create(NodeId("observed-profile"))
            (profile / "identity.json").write_text(identity.to_json(), encoding="utf-8")
            device = DeviceIdentity.from_node_identity(identity)
            (profile / "device_identity.json").write_text(
                device.to_json(), encoding="utf-8"
            )
            watcher = ObservabilityWatcher()
            runtime = ConnectRuntime(
                ConnectConfig(
                    profile_dir=profile,
                    discovery_enabled=False,
                    preferred_port=0,
                ),
                observer=watcher,
            )

            self.assertEqual(runtime.start().state, RuntimeState.STARTED)
            runtime.shutdown()

            metrics = {metric.target: metric for metric in watcher.snapshot().metrics}
            for target in (
                "profile:lock_acquire",
                "profile:lock_release",
                "profile:identity_migration",
                "profile:device_identity_migration",
            ):
                self.assertIsNotNone(metrics.get(target), target)
                assert target in metrics
                self.assertEqual(metrics[target].successes, 1)
            for target in metrics:
                self.assertNotIn(str(profile), target)
                self.assertNotIn(identity.node_id.value, target)

    def test_profile_lock_contention_is_observed_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            owner = ConnectRuntime(
                ConnectConfig(
                    profile_dir=profile,
                    discovery_enabled=False,
                    preferred_port=0,
                )
            )
            watcher = ObservabilityWatcher()
            contender = ConnectRuntime(
                ConnectConfig(
                    profile_dir=profile,
                    discovery_enabled=False,
                    preferred_port=0,
                ),
                observer=watcher,
            )
            self.assertEqual(owner.start().state, RuntimeState.STARTED)

            try:
                self.assertEqual(
                    contender.start().state, RuntimeState.PERSISTENCE_FAILED
                )
                metrics = {
                    metric.target: metric for metric in watcher.snapshot().metrics
                }
                lock_metric = metrics.get("profile:lock_acquire")
                self.assertIsNotNone(lock_metric)
                assert lock_metric is not None
                self.assertEqual(lock_metric.failures, 1)
                self.assertNotIn(str(profile), str(watcher.snapshot()))
            finally:
                owner.shutdown()


if __name__ == "__main__":
    unittest.main()
