# Expra Connect

Expra Connect is a headless Python library for reusable peer connectivity.
It owns identity, discovery, TLS-pinned transport, pairing, authorization,
explicit capability sharing, and optional cluster membership. It does not own
Tk, hardware scanning, a game engine, or streaming.

## Boundary Design

The host application owns its UI and application logic. Expra Connect is the
headless composition root and capability boundary beneath the application
adapter:

```text
HOST APPLICATION (Expra Engine)
        |-- Application UI: nodes and remote tools
        |-- Application logic: editor or game runtime
        `-- application adapter
                    |
             ConnectRuntime
             composition root and lifecycle facade
                    |
     +--------------+---------------+----------------+
     |              |               |                |
  IDENTITY      DISCOVERY        SECURITY        PAIRING
  NodeId        Zeroconf/mDNS    TLS/HMAC        pending transaction
  profile       candidates/TTL   fingerprints    human approval
  persistence                    auth            confirm/abort
     |              |               |             trust/grants
     +--------------+---------------+----------------+
                    |
              CONNECTION
              online/offline/retry
              endpoint changes
                    |
          authenticated authorization
                    |
           CAPABILITY ROUTER
           what may this peer do?
          /             |              \
 remote.inspect   asset.transfer   demo.read_state
                    |
          OPTIONAL CLUSTER (explicit opt-in)
          Coordinator / Worker, roles, epoch, fencing
                    |
                 REMOTE PEER
```

The capability router is not a second trust system. Pairing and live grants
establish authorization first; `CapabilityShare` then applies the target-owned
per-capability allowlist for an authenticated request. UI delivery and event
loop marshalling remain host responsibilities. The library never imports Tk or
application modules.

## Development

Install the local distribution with pip:

```sh
python -m pip install .
python -m pip install dist/expra_connect-*.whl
```

The distribution name is `expra-connect`; the Python import name is
`expra_connect`:

```python
import expra_connect
```

Windows online installation from any PowerShell directory:

```powershell
irm https://raw.githubusercontent.com/20204166/expra-connect/main/install/install-online.ps1 | iex
```
```Or powershell
irm "https://raw.githubusercontent.com/20204166/expra-connect/main/install/install-online.ps1?cache=$([guid]::NewGuid())" | iex
```
The online installer detects when Windows has no Python and bootstraps Python
3.12 through `winget`. It then downloads the newest wheel named in
`dist/SHA256SUMS`, verifies its SHA256 digest, installs the package for the
current user, verifies the import and installed version, and adds the user
scripts directory to `PATH`. Use `-System` with a downloaded copy of the script
for a system-wide installation. If `winget` is unavailable, install Python 3.10
or newer and retry the same command.

## Two-Node Test Runner

`run_peer.py` is a redacted acceptance harness for one Linux node and one
Windows node. It records listener, TLS fingerprint, discovery, pairing,
connection, and shared-capability results in `peer-report.json`. It never writes
pairing secrets or private keys.

### Linux Setup

From a fresh checkout:

```sh
git clone https://github.com/20204166/expra-connect.git
cd expra-connect
python3 -m venv .venv
. .venv/bin/activate
python -m pip install dist/expra_connect-*.whl
expra-peer --version
expra-peer --profile .expra-linux diagnostics
```

### Linux-to-Windows Pairing Example

The repository includes a documented Linux target in `examples/` that uses the
installed Python import, prints its package version, records redacted discovery
and pairing evidence, approves read-only pairing, and registers
`test.read_state` for the Windows acceptance run:

```sh
python3 examples/linux_pair_target_detailed.py \
  --profile .expra-windows-target \
  --report linux-target.json
```

Leave it running, then run the current `run_peer.py` initiator on Windows. The
expected final event is `shared_capability_result`. This test exercises mDNS
discovery, multi-route pairing fallback, TLS pinning, authenticated connect,
target-owned capability authorization, and redacted evidence reporting. See
`examples/README.md` for the complete walkthrough and failure boundaries.

The acceptance runner also records per-route attempts, supports explicit
advertised-address selection, exercises active transport rotation, verifies
persisted trust after restart, and verifies target-side self-revocation.

### Windows Setup Without Python

Open PowerShell and run the single bootstrap command:

```powershell
irm https://raw.githubusercontent.com/20204166/expra-connect/main/install/install-online.ps1 | iex
```

Verify the installation:

```powershell
expra-peer --version
expra-peer --profile "$env:LOCALAPPDATA\expra-connect" diagnostics
```

The online installer is the standard Windows installation path. It downloads
the wheel and checksum from this repository, verifies the wheel, installs the
package for the current user, verifies the import, and updates the user PATH.
Each release must commit the wheel named by `dist/SHA256SUMS`; otherwise the
raw GitHub download URL returns `404` even when the wheel exists locally.

### CLI Runtime Smoke Test

Start a peer and leave it running:

```sh
expra-peer --profile .expra-peer serve
```

From another terminal, inspect it:

```sh
expra-peer --profile .expra-peer status
expra-peer --profile .expra-peer identity
expra-peer --profile .expra-peer peers
expra-peer --profile .expra-peer diagnostics
```

On Windows use the same commands with the profile path changed:

```powershell
expra-peer --profile "$env:LOCALAPPDATA\expra-peer" serve
expra-peer --profile "$env:LOCALAPPDATA\expra-peer" diagnostics
```

The CLI starts a normal runtime and is suitable for listener and discovery
smoke tests. It does not automatically approve pairing requests. The complete
three-endpoint acceptance procedure, including `run_peer.py`, evidence files,
and Linux-to-Windows commands, is in `docs/TEST_ENDPOINTS.md`.

This repository is local-only and does not publish to PyPI. The wheel and
install scripts are the supported application distribution boundary.

Release builds run `scripts/release.py prepare-build` first. The helper compares
the importable package manifest with the newest four-segment wheel, selects a
repo-specific patch/feature/minor bump, updates `_version.py`, then builds and
verifies the wheel. Use `--bump none` for an intentional baseline build.

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
ruff check .
ruff format --check .
pyright
mypy --ignore-missing-imports src tests
```

The CLI includes diagnostics and a deliberately small demo surface:

```sh
expra-peer demo ping
expra-peer demo share
expra-peer demo loopback
expra-peer --profile .expra-connect --no-discovery diagnostics
expra-peer --profile .expra-connect pair PEER_NODE_ID
```

`demo loopback` starts a local framed listener and performs an authenticated
hello request. The loopback authenticated-service integration is covered by
`tests.test_remote_service`; it starts a framed server, signs a request, and
verifies the response. The demo CLI does not advertise or pair real machines.
Diagnostics are backed by `ConnectRuntime` and report actual listener,
identity, discovery, trust, peer, and optional cluster state.

See `docs/ARCHITECTURE.md`, `docs/SECURITY_MODEL.md`, and
`docs/TESTING.md` for boundaries and validation rules.

## Hosted Runtime

Construction is side-effect free. Network activity begins only after an explicit
`start()` call:

```python
from pathlib import Path

from expra_connect import ConnectConfig, ConnectRuntime

runtime = ConnectRuntime(ConnectConfig(profile_dir=Path(".expra-connect")))
status = runtime.start()
try:
    print(status.bound_port, status.tls_fingerprint)
finally:
    runtime.shutdown()
```

The package also exports the public discovery, pairing, authorization, sharing,
and cluster models, including `DiscoveredNodeCandidate`, `PeerGrant`,
`TrustedPeer`, `PendingPairing`, `NodeCapability`, `NodePermission`, and
`CapabilityShare`. `runtime.peers`, `runtime.pairing`, and `runtime.cluster`
expose the corresponding live results after `start()`.

Discovery is enabled by default, binds `0.0.0.0`, and prefers port `27321`; all
three are configurable. Cluster participation is disabled unless explicitly
enabled. Callback delivery is headless and occurs on the network/discovery
worker context; the host owns dispatching to its UI or event loop.
