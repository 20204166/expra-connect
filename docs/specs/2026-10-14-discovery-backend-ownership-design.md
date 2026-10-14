# Discovery Backend Ownership Design

## Goal

Give each Zeroconf responsibility one clear production owner without losing
address-decoding behavior or protection against callbacks arriving after a
backend stop/restart.

## Ownership

- `discovery/backend.py` retains only shared Zeroconf service-address and
  attribute-reading helpers used by discovery normalization. It does not own a
  Zeroconf client, browser, listener, or lifecycle.
- `discovery/network.py` remains the sole production owner of the Zeroconf
  adapter because it registers the local service and feeds raw service records
  into `NetworkDiscovery` through the `DiscoveryBackend` contract.
- The standalone `ZeroconfBackend` in `backend.py` is removed. Its
  generation-bound listener behavior is transferred to
  `ZeroconfDiscoveryBackend` before removal.

## Behavior and lifecycle

Each production backend start binds callbacks to a unique generation. Stop
retires that generation before cancelling the browser or closing Zeroconf.
Callbacks from a stopped generation, including callbacks arriving after a
subsequent start, are ignored. Current-generation add/update/remove events keep
their existing raw-record contract. Existing service registration, IPv4-only
fallback, cleanup, and `NetworkDiscovery` lifecycle semantics remain unchanged.

Address decoding remains shared and behavior-compatible: prefer scoped parsed
addresses, then parsed addresses, then raw packed addresses; preserve order and
deduplicate; skip malformed values without losing valid siblings.

## Tests

Move lifecycle/event-delivery coverage off the removed adapter and onto the
production adapter. Cover callbacks during browser construction, after stop,
and from an old listener after restart, alongside current-generation event
delivery and cleanup. Retain address-decoding edge-case tests against the shared
helpers. Run focused discovery tests, the full repository suite, Ruff,
formatting, Pyright, Mypy, and diff checks.

## Out of scope

Do not consolidate `NetworkDiscovery` candidate normalization, TTL, trust,
advertisement encoding, or service registration with the address-decoding
helpers. Do not change the discovery event model or public package exports.
