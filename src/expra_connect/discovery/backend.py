"""Shared decoding helpers for python-zeroconf service records."""

from __future__ import annotations

import logging
import socket
from collections.abc import Iterable
from typing import Any, cast

LOGGER = logging.getLogger(__name__)

_PARSED_ADDRESS_METHODS = ("parsed_scoped_addresses", "parsed_addresses")


def _address_text(address: Any) -> str | None:
    """Decode one observed address into text, or ``None`` when unusable.

    Only 4-byte IPv4 and 16-byte IPv6 packed forms and textual addresses are
    accepted. Malformed packed lengths and arbitrary objects are skipped rather
    than stringified into a bogus endpoint.
    """

    if isinstance(address, str):
        return address.strip() or None
    if not isinstance(address, bytes):
        return None
    if len(address) == 4:
        family = socket.AF_INET
    elif len(address) == 16:
        family = socket.AF_INET6
    else:
        return None
    try:
        return socket.inet_ntop(family, address)
    except OSError:
        return None


def _address_texts(addresses: Any) -> list[str]:
    """Decode an address collection, preserving first-observed order."""

    if not addresses:
        return []
    candidates: Iterable[Any]
    if isinstance(addresses, (str, bytes)):
        candidates = (addresses,)
    else:
        try:
            candidates = iter(addresses)
        except TypeError:
            return []
    texts: list[str] = []
    seen: set[str] = set()
    for address in candidates:
        text = _address_text(address)
        if text is not None and text not in seen:
            seen.add(text)
            texts.append(text)
    return texts


def _service_address_texts(info: Any) -> list[str]:
    """Read all resolved service addresses across Zeroconf IP versions.

    Modern APIs (``parsed_scoped_addresses`` then ``parsed_addresses``) are
    preferred so IPv6 and link-local scope survive; the raw ``addresses``
    property is IPv4-only and is used only as a feature-detected fallback.
    """

    for method_name in _PARSED_ADDRESS_METHODS:
        method = _attr(info, method_name)
        if callable(method):
            try:
                parsed = method()
            except Exception:
                LOGGER.debug("Failed to parse service addresses", exc_info=True)
            else:
                if parsed:
                    try:
                        return _address_texts(cast(Iterable[Any], parsed))
                    except TypeError:
                        LOGGER.debug(
                            "Parsed service addresses are not iterable", exc_info=True
                        )
    return _address_texts(_attr(info, "addresses"))


def _attr(info: Any, name: str) -> Any:
    if info is None:
        return None
    if isinstance(info, dict):
        return info.get(name)
    return getattr(info, name, None)
