"""Process-action adapter for authenticated remote providers."""

from typing import Any, cast

from .remote_models import ProcessActionResult


class RemoteProcessActionBackend:
    """Target-bound process action adapter over an authenticated provider."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def request_quit(
        self,
        pids: list[int],
        expected_create_times: dict[int, float] | None = None,
    ) -> ProcessActionResult:
        return cast(
            ProcessActionResult,
            self._provider.request_quit(
                [
                    {
                        "pid": pid,
                        "create_time": (expected_create_times or {}).get(pid),
                    }
                    for pid in pids
                ]
            ),
        )

    def force_quit(
        self,
        pids: list[int],
        expected_create_times: dict[int, float] | None = None,
    ) -> ProcessActionResult:
        return cast(
            ProcessActionResult,
            self._provider.force_quit(
                [
                    {
                        "pid": pid,
                        "create_time": (expected_create_times or {}).get(pid),
                    }
                    for pid in pids
                ]
            ),
        )
