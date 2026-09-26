# Linux/Windows Peer-Harness Acceptance

This runbook exercises the current `peer_harness` against two real hosts. The
standard pass uses an intentional, read-only pairing with operator approval,
then checks authenticated connection, shared capability access, transport
rotation/reconnect, trust restoration, and self-revocation. A separate run
checks bidirectional structured surfaces.

This is a development acceptance test, not a release build. Both hosts must use
the same pushed checkout and current source. Do not run with old peer-harness
code if you want to verify the new capability evidence events or pairing-race
fix.

## 1. Prepare Both Hosts

On each host, use a checkout at the commit under test and install the package
and development harness into that host's Python environment.

Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e . --no-build-isolation
.venv/bin/python -m pip install -e tools/peer_harness
.venv/bin/python -m peer_harness --help
```

Before using the machines, run the configured local gate on the Linux checkout:

```sh
.venv/bin/python -m unittest discover -s tests -t .
ruff check .
ruff format --check .
pyright
.venv/bin/mypy --ignore-missing-imports src tests
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . --no-build-isolation
.\.venv\Scripts\python.exe -m pip install -e tools/peer_harness
.\.venv\Scripts\python.exe -m peer_harness --help
```

Identify each host's reachable LAN IPv4 address. Use those addresses for
`--advertise-address`; do not disable or weaken a firewall. The examples below
use `192.168.55.103` for Windows and `192.168.55.107` for Linux; replace them
when the current network uses different addresses.

Use a new profile path and report path for every run. If a profile already
exists, choose a different path instead of deleting or reusing its state. A
profile contains identity and trust material; never commit it or print its
identity files.

## 2. Windows Target, Linux Initiator: Intentional One-Way Pairing

This is the primary pass. It requires an operator to approve the inbound
read-only pairing request on Windows by creating a one-shot approval file.
`read_state` pairing permission does not itself grant the harness capability;
the target report must show the explicit `test.read_state` share grant too.

### Windows terminal 1: start the target

```powershell
$windowsAddress = "192.168.55.103"
$base = Join-Path $env:LOCALAPPDATA "ExpraConnectAcceptance\win-target-20260926"
$profile = Join-Path $base "profile"
$report = Join-Path $base "target-report.json"
$approvalFile = "{0}.approve" -f $report

if (Test-Path $profile) { throw "Choose a new profile path; do not delete existing state." }
if (Test-Path $report) { throw "Choose a new report path; do not overwrite prior evidence." }
if (Test-Path $approvalFile) { throw "Choose a new approval-file path." }

.\.venv\Scripts\python.exe -m peer_harness target `
  --profile $profile `
  --report $report `
  --advertise-address $windowsAddress `
  --wait 480 `
  --explicit-approval `
  --approval-file $approvalFile `
  --approval-wait 180 `
  --rotate-after 240
```

Leave this process running. Once its report contains `started`, get the public
target `node_id` from that event:

```powershell
$windowsNodeId = (Get-Content -Raw $report | ConvertFrom-Json |
  Where-Object { $_.event -eq "started" }).node_id
$windowsNodeId
```

Copy that node ID to the Linux initiator command. Do not copy any identity-file
contents.

### Linux terminal 1: run the initiator

Set `WINDOWS_NODE_ID` to the value copied from the Windows target report.

```sh
linux_address=192.168.55.107
windows_node_id=WINDOWS_NODE_ID
base="$HOME/.local/share/expra-connect-acceptance/linux-initiator-20260926"
profile="$base/profile"
report="$base/initiator-report.json"

if [ -e "$profile" ] || [ -e "$report" ]; then
    printf '%s\n' "Choose a new profile path; do not delete existing state." >&2
    exit 1
fi

PYTHONPATH=tools/peer_harness/src .venv/bin/python -m peer_harness initiator \
  --profile "$profile" \
  --report "$report" \
  --advertise-address "$linux_address" \
  --peer-id "$windows_node_id" \
  --wait 120 \
  --reconnect-after-rotation \
  --rotation-wait 300 \
  --restart-check \
  --revoke-self
```

### Windows terminal 2: intentionally approve the request

Wait until the Windows target report/terminal shows `pairing_pending`. Verify
the caller ID is the Linux initiator's `started.node_id` and the requested
permission is only `read_state`. Then approve once:

```powershell
if (Test-Path $approvalFile) { throw "Approval file already exists; inspect the run before proceeding." }
New-Item -ItemType File -Path $approvalFile | Out-Null
```

The target consumes this approval file after approval. Pairing is persisted on
both hosts; keep the profiles and reports for diagnosis and do not remove them
automatically.

### Expected evidence

The Windows target report should include `capability_registered`, `started`,
`target_ready`, `pairing_pending`, `pairing_approved`, and
`capability_granted` for the Linux caller. The target later records `rotated`.

The Linux initiator report should include `started`, `discovered`, `paired`,
`connected` with `tls_verified=true`, `shared_capability_result`,
`reconnected_after_rotation`, `shared_after_rotation`, `self_revoked`,
`post_revoke_denied`, and `restored_trust`. A `capability_request_denied` event
is a failed share stage; compare it with the target's registration/grant events.

## 3. Analyze Both Reports

The current analyzer accepts multiple JSON files. Run it on both host reports
after copying them to one analysis machine:

```sh
python3 expra_connect_log_analyzer.py \
  win-target-report.json linux-initiator-report.json
```

For machine-readable output, add `--json`. The analyzer currently summarizes
each report and overall counts; it does not yet establish cross-role candidate
or peer correlation.

## 4. Reverse the Roles

Repeat the one-way pass with a fresh Linux target profile/report and a fresh
Windows initiator profile/report. Keep the same approval discipline and use the
Linux target's `started.node_id` as the Windows initiator's `--peer-id`.

Linux target terminal:

```sh
linux_address=192.168.55.107
base="$HOME/.local/share/expra-connect-acceptance/linux-target-20260926"
profile="$base/profile"
report="$base/target-report.json"
approval_file="$report.approve"

if [ -e "$profile" ] || [ -e "$report" ] || [ -e "$approval_file" ]; then
    printf '%s\n' "Choose new profile/report paths; do not delete existing state." >&2
    exit 1
fi

PYTHONPATH=tools/peer_harness/src .venv/bin/python -m peer_harness target \
  --profile "$profile" \
  --report "$report" \
  --advertise-address "$linux_address" \
  --wait 480 \
  --explicit-approval \
  --approval-file "$approval_file" \
  --approval-wait 180 \
  --rotate-after 240
```

Read the Linux target's public node ID from its report for the Windows
initiator's `--peer-id`:

```sh
.venv/bin/python -c 'import json,sys; print(next(e["node_id"] for e in json.load(open(sys.argv[1], encoding="utf-8")) if e.get("event") == "started"))' "$report"
```

Windows initiator terminal (after copying the Linux target's public node ID):

```powershell
$linuxAddress = "192.168.55.107"
$windowsAddress = "192.168.55.103"
$linuxNodeId = "LINUX_NODE_ID"
$base = Join-Path $env:LOCALAPPDATA "ExpraConnectAcceptance\win-initiator-20260926"
$profile = Join-Path $base "profile"
$report = Join-Path $base "initiator-report.json"

if (Test-Path $profile) { throw "Choose a new profile path; do not delete existing state." }
if (Test-Path $report) { throw "Choose a new report path; do not overwrite prior evidence." }

.\.venv\Scripts\python.exe -m peer_harness initiator `
  --profile $profile `
  --report $report `
  --advertise-address $windowsAddress `
  --peer-id $linuxNodeId `
  --wait 120 `
  --reconnect-after-rotation `
  --rotation-wait 300 `
  --restart-check `
  --revoke-self
```

Set `$windowsAddress` to the Windows host's reachable IPv4 address.

## 5. Bidirectional Structured-Surface Pass

Run this as a separate pass with fresh profiles. Both peers use
`--bidirectional-surfaces`; do **not** combine this mode with
`--explicit-approval`. Bidirectional surface mode installs its own harness-only
pairing callback so it can grant the separate read/review/action test matrix.

Windows target:

```powershell
$windowsAddress = "192.168.55.103"
$base = Join-Path $env:LOCALAPPDATA "ExpraConnectAcceptance\win-target-bidirectional-20260926"
$profile = Join-Path $base "profile"
$report = Join-Path $base "target-report.json"
if (Test-Path $profile) { throw "Choose a new profile path; do not delete existing state." }
if (Test-Path $report) { throw "Choose a new report path; do not overwrite prior evidence." }

.\.venv\Scripts\python.exe -m peer_harness target `
  --profile $profile `
  --report $report `
  --advertise-address $windowsAddress `
  --wait 480 `
  --rotate-after 240 `
  --bidirectional-surfaces
```

Linux initiator:

```sh
linux_address=192.168.55.107
windows_node_id=WINDOWS_NODE_ID
base="$HOME/.local/share/expra-connect-acceptance/linux-initiator-bidirectional-20260926"
profile="$base/profile"
report="$base/initiator-report.json"
if [ -e "$profile" ] || [ -e "$report" ]; then
    printf '%s\n' "Choose new profile/report paths; do not delete existing state." >&2
    exit 1
fi

PYTHONPATH=tools/peer_harness/src .venv/bin/python -m peer_harness initiator \
  --profile "$profile" \
  --report "$report" \
  --advertise-address "$linux_address" \
  --peer-id "$windows_node_id" \
  --wait 120 \
  --reconnect-after-rotation \
  --rotation-wait 300 \
  --restart-check \
  --revoke-self \
  --bidirectional-surfaces
```

Expected evidence includes `reverse_paired`, `reverse_connected`, surface
read/review/action results, explicit denial after access is stopped or revoked,
and the optional rotation/reconnect/restart events. Pairing alone must not
restore surface access.

## 6. Scope and Failure Triage

This cross-host harness covers discovery, directional pairing/trust,
authentication/TLS connection, logical-session reuse, target-owned capability
sharing, structured surfaces, reconnect after rotation, trust restore, and
revocation. Parser/malformed-input cases and cluster membership remain in the
dedicated unit tests and cluster demo; neither pairing nor connection silently
creates cluster membership.

- `discovery_timeout`: check both hosts' advertised IPv4 addresses and that the
  target process is still running.
- Pairing denial or timeout: compare the target's `pairing_pending` caller and
  permissions with the initiator's `started.node_id`; create the sentinel only
  after confirming the intended caller.
- `capability_request_denied`: confirm the target report contains
  `capability_registered` and a matching caller `capability_granted` event.
- No `connected` event: inspect route-attempt stages and verify the advertised
  TLS endpoint; do not treat discovery as trust or authentication.
- Reconnection failure: check that target rotation produced a changed
  transport generation and that both reports use the same target peer ID.

Do not print profile identity files, private keys, HMAC secrets, transport
proofs, or approval material. Do not delete profiles or reports as cleanup.
