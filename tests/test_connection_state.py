import math
import unittest

from expra_connect.connection_state import (
    ConnectionState,
    ConnectionStatus,
    PeerFailure,
    RetryState,
    classify_peer_failure,
)


class ConnectionStateTests(unittest.TestCase):
    def test_connection_states_do_not_encode_trust(self) -> None:
        self.assertEqual(ConnectionState.online(now=10).status, ConnectionStatus.ONLINE)
        self.assertEqual(ConnectionState.offline("refused", now=11).reason, "refused")

    def test_authentication_failure_disables_automatic_retry(self) -> None:
        retry = RetryState()
        retry.record_failure(PeerFailure.AUTHENTICATION_FAILED, now=10.0)
        self.assertFalse(retry.automatic_retry)
        self.assertIsNone(retry.next_attempt_at)

    def test_transient_failures_use_bounded_exponential_backoff(self) -> None:
        retry = RetryState()
        retry.record_failure(PeerFailure.TIMEOUT, now=10.0, jitter=lambda _: 0.5)
        retry.record_failure(PeerFailure.TIMEOUT, now=10.0, jitter=lambda _: 0.5)
        self.assertEqual(retry.attempt, 2)
        self.assertEqual(retry.next_attempt_at, 12.5)
        retry.reset()
        self.assertEqual(retry.attempt, 0)

    def test_failure_classification_is_conservative(self) -> None:
        self.assertEqual(
            classify_peer_failure("certificate fingerprint changed"),
            PeerFailure.AUTHENTICATION_FAILED,
        )
        self.assertEqual(
            classify_peer_failure("connection refused"), PeerFailure.CONNECTION_REFUSED
        )
        self.assertEqual(classify_peer_failure("other"), PeerFailure.DISAPPEARED)

    def test_retry_rejects_invalid_backoff_configuration(self) -> None:
        retry = RetryState()
        with self.assertRaises(ValueError):
            retry.record_failure(PeerFailure.TIMEOUT, now=0.0, base_seconds=-1.0)
        with self.assertRaises(ValueError):
            retry.record_failure(PeerFailure.TIMEOUT, now=0.0, max_backoff_exponent=-1)
        with self.assertRaises(ValueError):
            retry.record_failure(
                PeerFailure.TIMEOUT,
                now=0.0,
                max_backoff_exponent=1.5,  # type: ignore[arg-type]
            )
        with self.assertRaises(ValueError):
            retry.record_failure(
                PeerFailure.TIMEOUT, now=0.0, jitter=lambda _attempt: math.inf
            )
