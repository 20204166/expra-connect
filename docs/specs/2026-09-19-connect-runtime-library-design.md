# ConnectRuntime Library and Release Design

**Status:** Approved design for implementation

## Goal

Make the existing headless connection core a reusable installable library with a
single composition root, `ConnectRuntime`, while preserving the mature
System Analyzer lifecycle and security boundaries.

## Architecture

`ConnectConfig` owns application-neutral inputs: `app_id`, profile directory,
display name, bind host, preferred port, configurable discovery service type,
discovery enablement, and optional cluster composition. `ConnectRuntime` owns
construction and lifecycle only. Existing identity, persistence, TLS,
listener, discovery, pairing, registry, sharing, and cluster classes remain
the implementation owners.

Construction performs no network I/O. `start()` performs the proven sequence:
resolve profile, load/create and durably persist identity, load state, prepare
TLS, construct/start the listener, capture its actual endpoint and fingerprint,
construct/start discovery, then begin reconciliation/expiry processing. The
default is `discovery_enabled=True`; the default bind host is `0.0.0.0` and the
preferred port is `27321`, with the existing fallback behavior.

## Lifecycle and Failure Model

Startup is idempotent and returns a structured runtime status. Persistence
failure prevents advertisement. Listener failure never publishes a connectable
endpoint. Discovery-unavailable and discovery-disabled are reported separately
from listener failure. Partial startup is unwound in reverse order.

Shutdown invalidates the lifecycle generation, cancels reconciliation and expiry
work, stops discovery, stops the listener, closes runtime-owned connections, and
clears runtime references while preserving profile state. Shutdown is
idempotent. Old-generation callbacks cannot mutate the current runtime.

## Public API

Stable application-facing exports are `ConnectConfig`, `ConnectRuntime`,
`NodeId`, `PeerInfo`, and connection/status models. Low-level classes remain
available as advanced exports. The façade exposes peer inspection, pairing and
revocation composition, capability registration/sharing, connection state, and
shutdown without bypassing target authorization or conflating trust, discovery,
membership, and connection state.

## Events and Threading

Runtime callbacks are application-neutral and invoked after canonical state is
updated. Discovery, connection, pairing, sharing, and cluster callbacks run on
the worker/network callback context that produced the event; the library does
not marshal to Tk, Qt, asyncio, or any UI loop. Consumers own dispatching to
their application thread. Lifecycle generation checks apply before callback
delivery.

## Profiles and Security

The host application supplies the profile directory. Identity, TLS material, and
trust/grant state are scoped to that profile, so separate applications do not
share credentials accidentally. Trusted rediscovery validates stable identity
and transport fingerprint before endpoint reconciliation. Cluster composition is
optional and defaults disabled; generic peer/capability use does not implicitly
join a cluster.

## Packaging and Release

`src/expra_connect/_version.py` is the single version source and packaging reads
it dynamically. The wheel contains only the library and metadata. The CLI is a
consumer of the library and supports `--help`, `--version`, and the existing
demos. Release tooling builds and inspects the wheel, compares all package/build
inputs, emits `SHA256SUMS`, and installers verify the checksum before installing.

## Verification

Add façade tests for startup ordering, durable identity, actual endpoint/TLS
advertisement, preferred/fallback ports, disabled/unavailable discovery,
idempotence, restart generation fencing, profile separation, trust rediscovery,
partial-start cleanup, context-manager lifecycle, clean wheel installation,
CLI behavior, and package-boundary imports. Existing lower-level tests remain
unchanged. Physical multi-machine validation remains separately identified from
inherited System Analyzer evidence.
