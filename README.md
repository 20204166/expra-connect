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

The online installer downloads the newest wheel named in `dist/SHA256SUMS`,
verifies its SHA256 digest, requires Python 3.10 or newer, installs the package
for the current user, verifies the import and installed version, and adds the
user scripts directory to `PATH`. Use `-System` with a downloaded copy of the
script for a system-wide installation.

If Windows does not have Python, install it first from an elevated PowerShell:

```powershell
winget install --id Python.Python.3.12 -e
```

Open a new PowerShell window after installation, then run the online installer.

## Two-Node Test Runner

`run_peer.py` is a redacted acceptance harness for one Linux node and one
Windows node. It records listener, TLS fingerprint, discovery, pairing,
connection, and shared-capability results in `peer-report.json`. It never writes
pairing secrets or private keys.

On the target node, run:

```sh
python run_peer.py --role target --profile .expra-target --report target-report.json
```

On the initiator node, copy the target node ID from its `started` event and run:

```sh
python run_peer.py --role initiator --profile .expra-initiator \
  --peer-id TARGET_NODE_ID --report initiator-report.json
```

On Windows, use `py` instead of `python` if required:

```powershell
py run_peer.py --role target --profile "$env:LOCALAPPDATA\expra-target" --report target-report.json
py run_peer.py --role initiator --profile "$env:LOCALAPPDATA\expra-initiator" --peer-id TARGET_NODE_ID --report initiator-report.json
```

To fetch the runner directly on Windows after installing the package:

```powershell
irm https://raw.githubusercontent.com/20204166/expra-connect/main/run_peer.py -OutFile run_peer.py
```

The runner is ordinary host code. Run it from the directory containing
`run_peer.py`, or provide its full path. Each report is a JSON array containing
all events in order, rather than only the final result.

The target runner approves only this test harness pairing and shares only
`test.read_state`. Stop it with `Ctrl+C` after the initiator reports
`shared_capability_result`. Run the same harness with host firewalls and VPN
policy enabled, and send both JSON reports plus the output of
`expra-peer --version` when reporting a Windows result.

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
