"""Process-action adapter for authenticated remote providers."""

from typing import Any, Protocol

from .models import ProcessActionResult


def _process_refs(
    pids: list[int], expected_create_times: dict[int, float] | None
) -> list[dict[str, Any]]:
    create_times = expected_create_times or {}
    return [{"pid": pid, "create_time": create_times.get(pid)} for pid in pids]


class _ProcessActionProvider(Protocol):
    def request_quit(self, refs: list[dict[str, Any]]) -> ProcessActionResult: ...

    def force_quit(self, refs: list[dict[str, Any]]) -> ProcessActionResult: ...


class RemoteProcessActionBackend:
    """Target-bound process action adapter over an authenticated provider."""

    def __init__(self, provider: _ProcessActionProvider) -> None:
        self._provider = provider

    def request_quit(
        self,
        pids: list[int],
        expected_create_times: dict[int, float] | None = None,
    ) -> ProcessActionResult:
        return self._provider.request_quit(_process_refs(pids, expected_create_times))

    def force_quit(
        self,
        pids: list[int],
        expected_create_times: dict[int, float] | None = None,
    ) -> ProcessActionResult:
        return self._provider.force_quit(_process_refs(pids, expected_create_times))
