"""Low-level tests for the Zeroconf discovery adapters.

These exercise only the transport adapter: resource ownership, start/stop
transactions, address decoding, and event delivery. Normalization, TTL,
deduplication by identity, and trust stay in ``NetworkDiscovery`` and above.
"""

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from expra_connect.discovery_backend import (
    ZeroconfBackend,
    _address_text,
    _address_texts,
    _service_address_texts,
)

SERVICE_TYPE = "_expra-peer._tcp.local."


class _FakeZeroconf:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.closed = False
        self.registered: list[Any] = []
        self.info: Any = None
        self.info_error: Exception | None = None

    def register_service(self, service_info: Any) -> None:
        self.registered.append(service_info)

    def get_service_info(self, _type: str, _name: str, *_a: Any, **_k: Any) -> Any:
        if self.info_error is not None:
            raise self.info_error
        return self.info

    def close(self) -> None:
        self.closed = True


class _FakeServiceBrowser:
    def __init__(self, zc: Any, type_: Any, handlers: Any) -> None:
        self.zc = zc
        self.type = type_
        self.handlers = handlers
        self.cancelled = False
        self.cancel_error: Exception | None = None

    def cancel(self) -> None:
        self.cancelled = True
        if self.cancel_error is not None:
            raise self.cancel_error


def _recording_module() -> tuple[Any, list[_FakeZeroconf], list[_FakeServiceBrowser]]:
    zcs: list[_FakeZeroconf] = []
    browsers: list[_FakeServiceBrowser] = []

    class RecordingZeroconf(_FakeZeroconf):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            zcs.append(self)

    class RecordingBrowser(_FakeServiceBrowser):
        def __init__(self, zc: Any, type_: Any, handlers: Any) -> None:
            super().__init__(zc, type_, handlers)
            browsers.append(self)

    module = SimpleNamespace(
        Zeroconf=RecordingZeroconf, ServiceBrowser=RecordingBrowser
    )
    return module, zcs, browsers


def _info(**attrs: Any) -> Any:
    return SimpleNamespace(**attrs)


class _BackendCase(unittest.TestCase):
    def _make_backend(
        self, module: Any
    ) -> tuple[ZeroconfBackend, list[tuple[str, dict[str, Any]]]]:
        patcher = patch("expra_connect.discovery_backend._zeroconf_module", module)
        patcher.start()
        self.addCleanup(patcher.stop)
        events: list[tuple[str, dict[str, Any]]] = []
        backend = ZeroconfBackend(
            SERVICE_TYPE, lambda event, payload: events.append((event, payload))
        )
        return backend, events


class StartLifecycleTests(_BackendCase):
    def test_missing_dependency_raises_and_leaves_no_state(self) -> None:
        backend, _events = self._make_backend(None)

        with self.assertRaises(RuntimeError):
            backend.start()

        self.assertIsNone(backend._zeroconf)
        self.assertIsNone(backend._browser)

    def test_retry_after_missing_dependency_can_succeed(self) -> None:
        backend, _events = self._make_backend(None)
        with self.assertRaises(RuntimeError):
            backend.start()

        module, zcs, browsers = _recording_module()
        with patch("expra_connect.discovery_backend._zeroconf_module", module):
            backend.start()

        self.assertEqual(len(zcs), 1)
        self.assertEqual(len(browsers), 1)

    def test_zeroconf_constructor_failure_leaves_no_partial_state(self) -> None:
        class FailingZeroconf(_FakeZeroconf):
            def __init__(self, **kwargs: Any) -> None:
                raise OSError("bind failed")

        module = SimpleNamespace(
            Zeroconf=FailingZeroconf, ServiceBrowser=_FakeServiceBrowser
        )
        backend, _events = self._make_backend(module)

        with self.assertRaises(OSError):
            backend.start()

        self.assertIsNone(backend._zeroconf)
        self.assertIsNone(backend._browser)
        self.assertIsNone(backend._token)

    def test_browser_constructor_failure_closes_zeroconf(self) -> None:
        zcs: list[_FakeZeroconf] = []

        class RecordingZeroconf(_FakeZeroconf):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                zcs.append(self)

        class FailingBrowser(_FakeServiceBrowser):
            def __init__(self, zc: Any, type_: Any, handlers: Any) -> None:
                raise RuntimeError("browser failed")

        module = SimpleNamespace(
            Zeroconf=RecordingZeroconf, ServiceBrowser=FailingBrowser
        )
        backend, _events = self._make_backend(module)

        with self.assertRaises(RuntimeError):
            backend.start()

        self.assertEqual(len(zcs), 1)
        self.assertTrue(zcs[0].closed)
        self.assertIsNone(backend._zeroconf)
        self.assertIsNone(backend._browser)
        self.assertIsNone(backend._token)

    def test_repeated_start_is_a_no_op(self) -> None:
        module, zcs, browsers = _recording_module()
        backend, _events = self._make_backend(module)

        backend.start()
        backend.start()

        self.assertEqual(len(zcs), 1)
        self.assertEqual(len(browsers), 1)

    def test_start_after_stop_creates_a_fresh_generation(self) -> None:
        module, zcs, browsers = _recording_module()
        backend, _events = self._make_backend(module)

        backend.start()
        backend.stop()
        backend.start()

        self.assertEqual(len(zcs), 2)
        self.assertEqual(len(browsers), 2)
        self.assertTrue(zcs[0].closed)
        self.assertTrue(browsers[0].cancelled)
        self.assertFalse(zcs[1].closed)


class StopLifecycleTests(_BackendCase):
    def test_stop_before_start_is_safe(self) -> None:
        module, _zcs, _browsers = _recording_module()
        backend, _events = self._make_backend(module)

        backend.stop()

        self.assertIsNone(backend._zeroconf)
        self.assertIsNone(backend._browser)

    def test_stop_is_idempotent(self) -> None:
        module, zcs, browsers = _recording_module()
        backend, _events = self._make_backend(module)
        backend.start()

        backend.stop()
        backend.stop()

        self.assertTrue(browsers[0].cancelled)
        self.assertTrue(zcs[0].closed)
        self.assertIsNone(backend._zeroconf)
        self.assertIsNone(backend._browser)

    def test_browser_cancel_failure_still_closes_zeroconf(self) -> None:
        module, zcs, browsers = _recording_module()
        backend, _events = self._make_backend(module)
        backend.start()
        browsers[0].cancel_error = RuntimeError("cancel failed")

        backend.stop()

        self.assertTrue(zcs[0].closed)
        self.assertIsNone(backend._zeroconf)
        self.assertIsNone(backend._browser)

    def test_zeroconf_close_failure_still_clears_state(self) -> None:
        module, zcs, browsers = _recording_module()
        backend, _events = self._make_backend(module)
        backend.start()
        zcs[0].close = lambda: (_ for _ in ()).throw(RuntimeError("close failed"))  # type: ignore[method-assign]

        backend.stop()

        self.assertTrue(browsers[0].cancelled)
        self.assertIsNone(backend._zeroconf)
        self.assertIsNone(backend._browser)


class EventDeliveryTests(_BackendCase):
    def _started(self, info: Any) -> tuple[Any, Any, list[tuple[str, dict[str, Any]]]]:
        module, zcs, browsers = _recording_module()
        backend, events = self._make_backend(module)
        backend.start()
        zcs[0].info = info
        return zcs[0], browsers[0], events

    def test_add_event_emits_payload(self) -> None:
        zc, browser, events = self._started(
            _info(addresses=[b"\xc0\xa8\x01\x02"], port=27321)
        )

        browser.handlers.add_service(zc, SERVICE_TYPE, "peer")

        self.assertEqual(
            events,
            [("add", {"name": "peer", "addresses": ("192.168.1.2",), "port": 27321})],
        )

    def test_update_event_emits_payload(self) -> None:
        zc, browser, events = self._started(
            _info(addresses=[b"\xc0\xa8\x01\x02"], port=27321)
        )

        browser.handlers.update_service(zc, SERVICE_TYPE, "peer")

        self.assertEqual(events[0][0], "update")

    def test_remove_event_emits_name_only_without_resolving(self) -> None:
        zc, browser, events = self._started(_info(addresses=[], port=1))

        def _explode(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("remove must not resolve ServiceInfo")

        zc.get_service_info = _explode

        browser.handlers.remove_service(zc, SERVICE_TYPE, "peer")

        self.assertEqual(events, [("remove", {"name": "peer"})])

    def test_remove_with_non_string_name_is_ignored(self) -> None:
        zc, browser, events = self._started(_info(addresses=[], port=1))

        browser.handlers.remove_service(zc, SERVICE_TYPE, None)
        browser.handlers.remove_service(zc, SERVICE_TYPE, b"peer")

        self.assertEqual(events, [])

    def test_service_info_none_is_ignored_for_add_and_update(self) -> None:
        zc, browser, events = self._started(None)

        browser.handlers.add_service(zc, SERVICE_TYPE, "peer")
        browser.handlers.update_service(zc, SERVICE_TYPE, "peer")

        self.assertEqual(events, [])

    def test_resolver_exception_is_contained_and_later_events_deliver(self) -> None:
        zc, browser, events = self._started(_info(addresses=[], port=1))
        zc.info_error = RuntimeError("resolution failed")

        browser.handlers.add_service(zc, SERVICE_TYPE, "broken")

        self.assertEqual(events, [])

        zc.info_error = None
        zc.info = _info(addresses=[b"\xc0\xa8\x01\x02"], port=1)
        browser.handlers.add_service(zc, SERVICE_TYPE, "healthy")

        self.assertEqual([event[0] for event in events], ["add"])

    def test_host_callback_exception_is_contained(self) -> None:
        module, zcs, browsers = _recording_module()
        calls: list[str] = []

        def on_event(event: str, _payload: dict[str, Any]) -> None:
            calls.append(event)
            if event == "add":
                raise RuntimeError("host callback failed")

        patcher = patch("expra_connect.discovery_backend._zeroconf_module", module)
        patcher.start()
        self.addCleanup(patcher.stop)
        backend = ZeroconfBackend(SERVICE_TYPE, on_event)
        backend.start()
        zcs[0].info = _info(addresses=[b"\xc0\xa8\x01\x02"], port=1)

        browsers[0].handlers.add_service(zcs[0], SERVICE_TYPE, "peer")
        browsers[0].handlers.update_service(zcs[0], SERVICE_TYPE, "peer")

        self.assertEqual(calls, ["add", "update"])

    def test_synchronous_callback_during_browser_construction_is_delivered(
        self,
    ) -> None:
        zcs: list[_FakeZeroconf] = []
        events: list[tuple[str, dict[str, Any]]] = []

        class RecordingZeroconf(_FakeZeroconf):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                zcs.append(self)

        class SynchronousBrowser(_FakeServiceBrowser):
            def __init__(self, zc: Any, type_: Any, handlers: Any) -> None:
                super().__init__(zc, type_, handlers)
                zc.info = _info(addresses=[b"\xc0\xa8\x01\x02"], port=1)
                handlers.add_service(zc, SERVICE_TYPE, "early")

        module = SimpleNamespace(
            Zeroconf=RecordingZeroconf, ServiceBrowser=SynchronousBrowser
        )
        patcher = patch("expra_connect.discovery_backend._zeroconf_module", module)
        patcher.start()
        self.addCleanup(patcher.stop)
        backend = ZeroconfBackend(
            SERVICE_TYPE, lambda event, payload: events.append((event, payload))
        )

        backend.start()

        self.assertEqual([event[0] for event in events], ["add"])

    def test_listener_callback_after_stop_is_ignored(self) -> None:
        module, zcs, browsers = _recording_module()
        backend, events = self._make_backend(module)
        backend.start()
        zcs[0].info = _info(addresses=[b"\xc0\xa8\x01\x02"], port=1)
        listener = browsers[0].handlers

        backend.stop()
        listener.add_service(zcs[0], SERVICE_TYPE, "late")
        listener.remove_service(zcs[0], SERVICE_TYPE, "late")

        self.assertEqual(events, [])

    def test_old_listener_after_restart_is_rejected(self) -> None:
        module, zcs, browsers = _recording_module()
        backend, events = self._make_backend(module)
        backend.start()
        zcs[0].info = _info(addresses=[b"\xc0\xa8\x01\x02"], port=1)
        old_listener = browsers[0].handlers
        old_zc = zcs[0]

        backend.stop()
        backend.start()
        zcs[1].info = _info(addresses=[b"\xc0\xa8\x01\x03"], port=1)

        old_listener.add_service(old_zc, SERVICE_TYPE, "stale")
        self.assertEqual(events, [])

        browsers[1].handlers.add_service(zcs[1], SERVICE_TYPE, "current")
        self.assertEqual([event[0] for event in events], ["add"])


class AddressDecodingTests(unittest.TestCase):
    def test_packed_ipv4_bytes(self) -> None:
        self.assertEqual(_address_text(b"\xc0\xa8\x01\x02"), "192.168.1.2")

    def test_packed_ipv6_bytes(self) -> None:
        packed = bytes.fromhex("20010db8000000000000000000000001")
        self.assertEqual(_address_text(packed), "2001:db8::1")

    def test_malformed_packed_lengths_are_skipped(self) -> None:
        for raw in (b"", b"\x01\x02\x03", b"\x01" * 5, b"\x01" * 15, b"\x01" * 17):
            self.assertIsNone(_address_text(raw), raw)

    def test_arbitrary_object_is_not_stringified(self) -> None:
        self.assertIsNone(_address_text(object()))
        self.assertIsNone(_address_text(1234))

    def test_string_addresses_are_preserved(self) -> None:
        self.assertEqual(_address_text("2001:db8::9"), "2001:db8::9")
        self.assertIsNone(_address_text("   "))

    def test_malformed_item_does_not_kill_valid_siblings(self) -> None:
        texts = _address_texts([b"\x01\x02\x03", b"\xc0\xa8\x01\x02"])
        self.assertEqual(texts, ["192.168.1.2"])

    def test_duplicate_addresses_collapse_preserving_order(self) -> None:
        texts = _address_texts(
            [b"\xc0\xa8\x01\x02", b"\xc0\xa8\x01\x02", b"\xc0\xa8\x01\x03"]
        )
        self.assertEqual(texts, ["192.168.1.2", "192.168.1.3"])

    def test_empty_and_scalar_addresses_are_safe(self) -> None:
        self.assertEqual(_address_texts([]), [])
        self.assertEqual(_address_texts(()), [])
        self.assertEqual(_address_texts(123), [])
        self.assertEqual(_address_texts(None), [])

    def test_parsed_strings_are_preserved_in_order(self) -> None:
        info = _info(
            addresses=[],
            parsed_addresses=lambda: ["192.168.1.2", "2001:db8::1"],
        )
        self.assertEqual(_service_address_texts(info), ["192.168.1.2", "2001:db8::1"])

    def test_scoped_ipv6_scope_is_preserved(self) -> None:
        info = _info(parsed_scoped_addresses=lambda: ["fe80::1234%3"])
        self.assertEqual(_service_address_texts(info), ["fe80::1234%3"])

    def test_scoped_api_is_preferred_over_parsed(self) -> None:
        info = _info(
            parsed_scoped_addresses=lambda: ["fe80::1234%3"],
            parsed_addresses=lambda: ["fe80::1234"],
        )
        self.assertEqual(_service_address_texts(info), ["fe80::1234%3"])

    def test_parsed_api_is_preferred_over_raw(self) -> None:
        info = _info(
            addresses=[b"\xc0\xa8\x01\x02"],
            parsed_addresses=lambda: ["2001:db8::9"],
        )
        self.assertEqual(_service_address_texts(info), ["2001:db8::9"])

    def test_raw_fallback_is_strict_when_parsed_api_absent(self) -> None:
        info = _info(addresses=[b"\x01\x02\x03", b"\xc0\xa8\x01\x02"])
        self.assertEqual(_service_address_texts(info), ["192.168.1.2"])

    def test_parsed_api_returning_empty_falls_back_to_raw(self) -> None:
        info = _info(
            addresses=[b"\xc0\xa8\x01\x02"],
            parsed_addresses=list,
        )
        self.assertEqual(_service_address_texts(info), ["192.168.1.2"])

    def test_parsed_api_exception_falls_back_to_raw(self) -> None:
        def _explode() -> list[str]:
            raise RuntimeError("parsed failed")

        info = _info(addresses=[b"\xc0\xa8\x01\x02"], parsed_addresses=_explode)
        self.assertEqual(_service_address_texts(info), ["192.168.1.2"])

    def test_empty_raw_addresses_return_empty(self) -> None:
        self.assertEqual(_service_address_texts(_info(addresses=[], port=1)), [])


if __name__ == "__main__":
    unittest.main()
