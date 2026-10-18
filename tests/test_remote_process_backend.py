import unittest
from typing import Any

from expra_connect.remote.models import ProcessActionResult
from expra_connect.remote.process_backend import RemoteProcessActionBackend


class _Provider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    def request_quit(self, refs: list[dict[str, Any]]) -> ProcessActionResult:
        self.calls.append(("request_quit", refs))
        return ProcessActionResult(0, (), (), ())

    def force_quit(self, refs: list[dict[str, Any]]) -> ProcessActionResult:
        self.calls.append(("force_quit", refs))
        return ProcessActionResult(0, (), (), ())


class RemoteProcessActionBackendTests(unittest.TestCase):
    def test_actions_forward_pids_with_optional_creation_times(self) -> None:
        provider = _Provider()
        backend = RemoteProcessActionBackend(provider)

        backend.request_quit([10, 20], {20: 123.5})
        backend.force_quit([30], {30: 456.0})

        self.assertEqual(
            provider.calls,
            [
                (
                    "request_quit",
                    [
                        {"pid": 10, "create_time": None},
                        {"pid": 20, "create_time": 123.5},
                    ],
                ),
                ("force_quit", [{"pid": 30, "create_time": 456.0}]),
            ],
        )


if __name__ == "__main__":
    unittest.main()
