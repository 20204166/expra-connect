# Expra Connect Developer Guide

Expra Connect is a headless Python library for adding peer identity, discovery,
pairing, authenticated connections, target-side authorization, capability
sharing, and optional cluster coordination to a host application. The package
owns networking and its profile state; your application owns user approval,
application handlers, UI/event-loop delivery, and product policy.

This guide covers three ways to use the project:

1. **CLI** for local status, diagnostics, and a long-running peer process.
2. **Embedded runtime** for host applications that own callbacks and policy.
3. **Peer harness** for development-only two-host acceptance testing. It is not
   an application API or a production dependency.

For a package overview, see the [repository README](../README.md). For deeper
design details see [Architecture](ARCHITECTURE.md), [Security Model](SECURITY_MODEL.md),
[Sessions](SESSION_MODEL.md), [Cluster](CLUSTER.md), and [Testing](TESTING.md).

## Install

The distribution is named `expra-connect`; import it as `expra_connect`. It
requires Python 3.10 or newer. Runtime dependencies are installed with the
package; development checks are an optional extra. This repository does not
publish the package to PyPI. Install a wheel from the release source you trust,
or install from a checkout:

```sh
python -m pip install .
python -m pip install '.[dev]'
```

For a specific locally available wheel, install its exact path rather than a
wildcard:

```sh
python -m pip install /path/to/compatible_expra_connect.whl
```

Release wheels are platform/Python-version specific; select a compatible exact
wheel and verify it against the release checksum manifest.

The repository also provides a verified release wheel and an `expra-peer`
console command. Follow the distribution channel used by your application; do
not assume a development harness or repository script is part of the installed
library.

## Choose an integration path

| Need | Use | Notes |
|---|---|---|
| Check a profile or run a peer without writing an adapter | `expra-peer` CLI | Does not auto-approve incoming pairing. |
| Integrate peers with an application | `ConnectRuntime` | Host supplies policy/callbacks and calls `shutdown()`. |
| Exercise discovery, pairing, sharing and rotation across two hosts | `tools/peer_harness` | Development-only acceptance tool; requires explicit network setup. |
| Manage role/membership coordination | Runtime with `cluster_enabled=True` and `runtime.cluster` | Optional; pairing and connection do not create membership. |

## 1. Use the CLI

The CLI is useful for a local runtime smoke test and redacted operational
inspection. On Linux, its default profile is `$XDG_STATE_HOME/expra-connect` or
`~/.local/state/expra-connect`; on Windows it is under `%LOCALAPPDATA%\expra-connect`.
Use `--profile` to choose an explicit profile and `--no-discovery` to disable
discovery for a local check.

```sh
expra-peer --version
expra-peer --profile /path/to/connect-profile --no-discovery diagnostics
expra-peer --profile /path/to/connect-profile --no-discovery serve
```

`serve` remains active until interrupted. In another terminal, `status`,
`identity`, `peers`, and `diagnostics` show operational state. `pair PEER_ID`
starts outbound pairing; `revoke PEER_ID` revokes the stored relationship.
An inbound application must supply an approval callback—CLI `serve` does not
silently accept pairing. `demo ping`, `demo share`, and `demo loopback` are
isolated demonstrations, not proof of a real LAN pairing.

## 2. Embed the runtime

`ConnectRuntime` construction is side-effect-free. Start it explicitly; inspect
the returned status instead of assuming that the listener or discovery started.
Use a fresh profile path for a new identity. The profile is durable application
state and must not be shared by unrelated applications or processes.

```python
from pathlib import Path

from expra_connect import ConnectConfig, ConnectRuntime, RuntimeState

runtime = ConnectRuntime(
    ConnectConfig(
        profile_dir=Path.home() / ".local" / "state" / "my-app" / "connect",
        discovery_enabled=False,  # True by default; useful for local-only setup.
    )
)
try:
    status = runtime.start()
    if status.state is not RuntimeState.STARTED:
        raise RuntimeError(f"Connect runtime did not start: {status}")
    print(f"Listening on {status.bound_host}:{status.bound_port}")
    print(status)
finally:
    runtime.shutdown()
```

The runtime can also be used as a context manager; `__enter__` starts it and
`__exit__` shuts it down. Check `runtime.status` after entry because startup can
return a persistence or listener failure status. Shutdown is safe to call more
than once. The runtime holds a nonblocking profile lock for its lifetime; a
second runtime using the same profile fails closed.

For a host lifecycle that queues discovery callbacks and denies incoming
pairing unless the host adds an explicit policy, see
[`examples/host_integration.py`](../examples/host_integration.py). Runtime
callbacks can be invoked away from the host/UI thread; queue events for the
application's event loop rather than touching UI objects directly. Pairing
approval is synchronous: a host handoff must not block a runtime thread needed
to complete the request. If a safe handoff is unavailable, deny the request.
Always shut the runtime down in `finally`.

### Explicit profile migration

`migrate_profile(source, destination, *, convert, verify)` provides a staged,
schema-neutral migration boundary. The destination parent must already exist
and the destination path must be absent. `ProfileSourceReader` exposes regular
source files as relative names and bytes; `StagedProfileWriter` writes converted
bytes only beneath a private staging directory. The host supplies both
callbacks and owns all schema mapping and policy. The verifier receives the
staged directory before promotion; the result reports the destination and the
file/byte counts written by the converter. Files created by verification are
not included in those counts.

```python
from pathlib import Path

from expra_connect import migrate_profile


def convert(reader, writer):
    # Synthetic illustration only: map the host's real schema explicitly.
    writer.write_bytes("identity.yaml", reader.read_bytes("legacy.json"))


def verify(staged: Path) -> None:
    if not (staged / "identity.yaml").is_file():
        raise ValueError("converted identity is missing")


result = migrate_profile(
    Path("./legacy-profile"),
    Path("./connect-profile-new"),
    convert=convert,
    verify=verify,
)
```

The source is retained on both success and failure; failed staging is cleaned
up, while an existing destination is never overwritten. This is an explicit
host-controlled operation, not part of runtime startup. Coordinate it so no
other process is activating or writing the same destination: the helper does
not provide cross-process exclusion or a multi-file transaction. Callbacks are
trusted host code, not sandboxed; only the writer is constrained to the staging
tree. Test conversion and verification using synthetic fixtures before using
real state. If validating the staged profile by starting a runtime, shut that
runtime down before promotion and account for any files it creates.

To measure synthetic end-to-end migration cost at 1, 16, and 64 files, run:

```sh
.venv/bin/python -m scripts.benchmark_persistence --operation "Profile migration" --warmups 2 --repetitions 9 --json-output /tmp/opencode/profile-migration-benchmark.json
```

The benchmark reports source file count and payload bytes for fixed 256-byte
files. Results are diagnostic scaling measurements; there is no migration
latency SLO.

`ConnectConfig` defaults to listening on `0.0.0.0`, discovery enabled, preferred
service port `27321`, cluster disabled, and read-only base capabilities. Choose
`bind_host`, `preferred_port`, and `advertised_addresses` deliberately for your
deployment. Discovery being disabled/unavailable does not mean the listener
failed; inspect `RuntimeStatus` fields such as `listener_started`,
`discovery_started`, `discovery_disabled`, `bound_port`, and `reason`.

## 3. Pair peers and share a host capability

Pairing, network connection, and capability sharing are separate decisions. The
target must approve the incoming pairing request. A successful Pair does not
automatically allow any custom capability: the target separately calls
`runtime.sharing.allow()` for that peer and capability.

The following target-side pattern is adapted from the peer harness, with a
fixed allow-list standing in for an application's real approval UI/policy. Do
not replace that policy with unconditional approval in a deployed application.

```python
from pathlib import Path
from threading import Event

from expra_connect import ConnectConfig, ConnectRuntime, NodeId, RuntimeState

APPROVED_CALLERS = {"paste-a-public-node-id-approved-by-the-operator"}
runtime: ConnectRuntime


def read_status(peer_id: NodeId, _params: dict[str, object]) -> dict[str, object]:
    # Return only data this specific capability is meant to expose.
    return {"service": "ready", "requester": peer_id.value}


def approve_pairing(request: object) -> bool:
    caller_id = getattr(request, "caller_node_id", None)
    if not isinstance(caller_id, NodeId) or caller_id.value not in APPROVED_CALLERS:
        return False
    # This is an independent target-side capability grant, not Pair permission.
    runtime.sharing.allow(caller_id, "app.status.read")
    return True


runtime = ConnectRuntime(
    ConnectConfig(
        profile_dir=Path("./connect-profile"),
        on_pairing_request=approve_pairing,
    )
)
runtime.sharing.register("app.status.read", read_status)
try:
    status = runtime.start()
    if status.state is not RuntimeState.STARTED:
        raise RuntimeError(f"Connect runtime did not start: {status}")
    print(f"Target node ID: {runtime.identity.node_id.value}")
    print("Keep the target alive while the initiator pairs and connects.")
    Event().wait()  # Keep the demo target alive; Ctrl+C still reaches finally.
finally:
    runtime.shutdown()
```

On the initiating side, wait for the desired discovered candidate, pair
explicitly, then connect and call the granted capability:

```python
import time
from pathlib import Path

from expra_connect import ConnectConfig, ConnectRuntime, NodeId, RuntimeState

target_id = NodeId("paste-the-target-node-id")
runtime = ConnectRuntime(ConnectConfig(profile_dir=Path("./initiator-profile")))
try:
    status = runtime.start()
    if status.state is not RuntimeState.STARTED:
        raise RuntimeError(f"Connect runtime did not start: {status}")

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if any(peer.stable_id == target_id.value for peer in runtime.peers):
            break
        time.sleep(0.2)
    else:
        raise TimeoutError("target was not discovered")

    runtime.pair_peer(target_id)  # target approval is still required
    provider = runtime.connect_peer(target_id)
    result = provider.request_shared("app.status.read", {})
    print(result)
finally:
    runtime.shutdown()
```

The peer harness demonstrates the same sequence (`runtime.pair_peer`,
`runtime.connect_peer`, `provider.request_shared`) in
`tools/peer_harness/src/peer_harness/flows.py`. Its test capability and
auto-approval logic are for acceptance testing only. Capability grants are
ephemeral: after target shutdown/restart, register handlers again and explicitly
re-grant access after your host's approval policy runs. Pair trust itself is
persisted separately.

## 4. Share structured surfaces

Use structured surfaces when the host needs distinct read, review, and named
action operations. Registration describes host-owned handlers; each peer must
receive explicit access for each surface/access level. A surface grant does not
follow from Pair trust, generic `CapabilityShare`, or cluster membership.

```python
from expra_connect import NodeId


def read_document(_peer: NodeId, _params: dict[str, object]) -> dict[str, object]:
    return {"title": "Untitled"}


def review_document(_peer: NodeId, _params: dict[str, object]) -> dict[str, object]:
    return {"issues": []}


def save_document(_peer: NodeId, _params: dict[str, object]) -> dict[str, object]:
    # Replace with the host's validated, authorized save operation.
    return {"saved": True}


runtime.register_surface(
    "editor.document",
    read=read_document,
    review=review_document,
    actions={"save": save_document},
)

# After your host approval flow has identified and approved the peer:
runtime.grant_surface_access(approved_peer_id, "editor.document", access="read")
```

This is a host-side fragment: `runtime` is the configured runtime from the
previous section, `NodeId` is imported from `expra_connect`, and
`approved_peer_id` is the `NodeId` verified by your pairing/approval flow. The
sample handler is illustrative; replace it with real application behavior.

An authenticated client provider exposes `read_surface`, `review_surface`, and
`invoke_surface_action`. Treat action handlers as privileged host endpoints;
validate parameters and apply application-level consent/confirmation where
appropriate. Surface grants are process-local and must be re-established after
restart. See [Expra Engine Integration](EXPRA_ENGINE_INTEGRATION.md) for the
host-specific callback/UI handoff pattern.

## 5. Optional features

- **Discovery:** Zeroconf/mDNS provides expiring endpoint candidates. Candidates
  are untrusted observations; use explicit pairing to establish trust.
- **Connection and reconnection:** `runtime.connect_peer()` and
  `runtime.disconnect_peer()` manage outgoing connections without creating or
  removing trust or cluster membership. Routes/retries belong to the runtime.
- **Authentication and transport:** the runtime uses pinned TLS plus
  authenticated protocol envelopes. A `NodeId` is an identifier, not a secret.
  Do not construct low-level transports or bypass runtime security checks for
  normal integrations.
- **Built-in remote reads:** `AuthenticatedNodeProvider` exposes hello,
  dashboard snapshot, component summary, process-candidate, and storage-candidate
  operations. Read operations require the relevant host-supplied `provider`
  behavior and are bounded by the target's capability/permission grants; the
  default runtime provider is an empty placeholder, not a system scanner.
- **Process termination:** lower-level `RemoteService` supports an optional
  `process_manager`, and the protocol has separately authorized termination
  operations. `ConnectConfig` does not currently expose that backend, and
  `ConnectRuntime` does not wire one in. Do not assume process termination is
  available through the standard runtime integration.
- **Transport rotation:** `runtime.rotate_transport()` rotates TLS transport
  material while retaining the stable identity; callers should observe its
  returned status and handle `TransportStateError`/restart failure.
- **Observability:** `runtime.diagnostics()` returns operational state without
  credentials. `ObservabilityWatcher` (from
  `expra_connect.observability`) may be passed to the runtime when the host needs
  bounded operation metrics; do not add identity, paths, payloads, or secrets as
  labels.
- **Cluster:** opt in with `ConnectConfig(cluster_enabled=True)`. Cluster roles,
  targeted invitations, epochs, leases, and fencing are a separate durable
  membership system. Joining requires its own explicit Pair-gated flow; neither
  discovery nor Pair creates membership. See [Cluster](CLUSTER.md).
- **CLI:** `expra-peer --help` lists the CLI contract; use `diagnostics` for
  redacted operational inspection, not as a programmatic API.

For development acceptance only, install the harness beside the package with
`python -m pip install -e tools/peer_harness`. Its `target` and `initiator`
roles exercise pairing, a test capability, reconnect after transport rotation,
trust restoration, and revocation; `analyze` correlates redacted JSON reports.
The complete commands and explicit-approval procedure are in
[Peer Harness Acceptance](PEER_HARNESS_ACCEPTANCE.md). Do not ship the harness
as a library integration.

The root package facade lists supported imports. `runtime.pairing`,
`runtime.registry`, `runtime.cluster`, `runtime.peers`, and `runtime.connections`
are live runtime-owned state and are not substitutes for the relevant policy
operations.

## Profile storage and lifecycle

The profile directory is outside the installed package. Current runtime-owned
files include the following (some are created only when the corresponding
feature is used):

| Data | Typical files | Lifetime / handling |
|---|---|---|
| Node identity metadata and secret bundle | `identity.yaml`, `identity.msgpack` | Durable stable node ID, HMAC secret and private root material. |
| Device identity metadata and key bundle | `device_identity.yaml`, `device_identity.msgpack` | Durable device-root view and private-key material. |
| Peer grants/trust and pending pairing | `trust.yaml` | Durable directional relationship records; contains sensitive peer credentials. |
| Transport generation state and TLS keys | `transport.yaml`, `peer-tls*.crt`, `peer-tls*.key` | Durable rotation/pinning continuity; private key files are sensitive. |
| Cluster membership (if enabled) | `cluster.yaml` | Durable topology/roles/invitation state; separate from Pair trust. |
| Mutating request result state | `idempotency.yaml` | Bounded result reuse supporting safe request retries. |
| Active profile lock | `.expra-connect.lock` | OS lock held while a runtime owns the profile; not a trust record. |

Exact files depend on startup, transport generation, migration, and enabled
features. Profile writes use atomic replacement and owner-only permissions where
supported by the implementation. MessagePack is an encoding, **not encryption**:
processes with the same OS account/profile access can read private bundles.
Protect profiles from source control, broad backups, sync folders, and unrelated
processes. Do not copy profile contents into logs or support tickets.

Identity, trust, transport, and enabled cluster state survive orderly shutdown.
Discovery candidates, active connections, generic capability grants, structured
surface grants, and logical sessions are process-local and do not survive
restart. Re-grant host capabilities/surfaces after restart. A malformed or
mismatched persisted state fails closed; do not delete it to force startup.
Legacy JSON state may be migrated to current YAML/MessagePack forms. Older
JSON-only releases may not be able to read a migrated profile; do not downgrade
without a supported migration/backup plan. Generic state migration may retain its
JSON source file.

## Security and operational rules

- Treat discovery as untrusted routing information, not authentication.
- Pairing is directional: outbound trust and inbound grants are distinct.
- Pairing permission, remote capability grant, surface access, and cluster role
  are distinct authorization checks. Grant the minimum needed at each layer.
- The default listener bind is `0.0.0.0`; choose the intended interface and
  firewall policy before exposing a runtime to a network. Do not disable a
  firewall to make discovery work.
- Keep `on_pairing_request` tied to an explicit user/operator decision. Do not
  auto-approve arbitrary callers in a production host.
- Host capability/surface handlers run with host application authority. Validate
  inputs, constrain outputs, and gate destructive effects in the host.
- Do not log private keys, HMAC secrets, invitation material, transport proofs,
  or fencing-token values. Diagnostics intentionally omit credentials.
- Call `shutdown()` in `finally` or use the context manager. Do not share one
  profile across concurrent runtimes; profile locking prevents simultaneous
  ownership.

## Troubleshooting

| Symptom | First checks |
|---|---|
| `persistence_failed` | Check profile path/permissions and preserve malformed files for recovery; do not remove state. |
| `listener_unavailable` | Check the selected bind address/port and OS socket errors; inspect `RuntimeStatus.reason`. |
| Listener started but discovery did not | Inspect `discovery_disabled`, `discovery_started`, and `discovery_reason`; pairing/connection can be handled through another candidate source or host path. |
| Candidate visible but pair fails | Confirm the target host is running and its pairing callback approves the intended caller and requested permission. Discovery is not trust. |
| Connected but shared capability denied | Check target handler registration and explicit per-peer `runtime.sharing.allow()`; Pair alone does not grant it. |
| Surface call denied | Check the exact surface ID, access level, expiry, and target-side grant; surface grants are separate and ephemeral. |
| Second runtime cannot open profile | Find the process that owns it and shut that runtime down normally; do not delete the lock file to bypass ownership. |
| Peer cannot reconnect after transport rotation | Check discovery address/port and signed generation continuity; do not pin a newly advertised certificate solely because its `NodeId` matches. |

For real two-host acceptance, setup, explicit approval, reports, reverse roles,
rotation and the surface matrix, see
[Peer Harness Acceptance](PEER_HARNESS_ACCEPTANCE.md). It requires reachable
addresses and deliberate firewall/network setup. Its successful/blocked results
are test evidence, not a guarantee about another deployment.

## Development checks

For repository development, install editable and run the configured checks from
the repository root as described in [Testing](TESTING.md). The `peer_harness`
package under `tools/` is a development-only diagnostic/acceptance surface and
is not shipped in the library wheel.
