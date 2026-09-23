# Expra Connect 1.2.0.0 Acceptance Test

Date: 2026-09-22<br>
Package: `expra_connect-1.2.0.0`<br>
Wheel: `dist/expra_connect-1.2.0.0-py3-none-any.whl`

This report combines the versioned acceptance results with the standard test
endpoints from the former `docs/TEST_ENDPOINTS.md`. It records the 1.2.0.0
historical evidence and the later Linux initiator attempt made from the current
1.4.2.0 source checkout. Do not interpret later results as 1.2.0.0 package
results. Reports and profiles are kept outside the repository under
`/tmp/opencode/peer-tests-1.2.0.0/` or
`/tmp/opencode/peer-tests-1.4.2.0/`; they contain local runtime state and must
not be committed.

## Results

| Scenario | Initiator result | Status |
| --- | --- | --- |
| 1.2.0.0 `run_peer.py` baseline, target `30cbb263...` | Pairing, TLS connection, and `test.read_state` share succeeded | PASS |
| 1.2.0.0 `run_peer.py` baseline, target `c2b8e35a...` | Pairing, TLS connection, and `test.read_state` share succeeded | PASS |
| 1.2.0.0 `run_peer_extended.py` one-way | Rotation, reconnect, second share, self-revoke denial, trust restore, and diagnostics succeeded | PASS |
| 1.2.0.0 `run_peer_extended.py --bidirectional-surfaces` local attempt | Target selected a different discovered peer; surface matrix could not complete | BLOCKED |
| 1.4.2.0 Linux extended initiator to `192.168.55.103` | Discovery, pairing, and reverse TLS connection succeeded; target denied structured surface access | BLOCKED (target setup) |
| 1.4.2.0 Linux baseline initiator to `192.168.55.103` | Discovery, pairing, and TLS connection succeeded; target denied `test.read_state` | BLOCKED (target setup) |

The final bidirectional structured-surface share still requires one isolated
Linux target and one Windows initiator. It is not marked as passed in this
report.

## Combined Standard Acceptance Endpoints

Run these endpoints in order. Endpoint 1 is local runtime/CLI validation,
Endpoint 2 is a physical cross-host pairing, connection, and share, and Endpoint
3 checks persistence and revocation. Discovery, pairing, authentication,
authorization, connection, and cluster membership are independent outcomes;
record each stage separately. Do not disable the firewall or automatically
delete persisted state to make a test pass.

### Endpoint 1: Local Runtime And CLI

Run in a Linux checkout with the package installed, or prefix the Python
commands with `PYTHONPATH=src` for an editable source checkout. Commands and
outcomes must be recorded together:

```sh
expra-peer --version
python3 -c "import expra_connect; print(expra_connect.__version__)"
expra-peer --profile /tmp/expra-endpoint-1 --no-discovery diagnostics
expra-peer --profile /tmp/expra-endpoint-1 --no-discovery serve
```

Expected: release version, successful import, diagnostics state `started`, a
bound port and TLS fingerprint, and clean shutdown with Ctrl+C. Retain console
output and diagnostics; record any different outcome and its exact command.

### Endpoint 2: Physical Pair, Connect, And Share

Start the Linux target using the detailed example and a reachable LAN address.
Use separate target/initiator profiles, keep the target running until the
initiator finishes, and retain both consoles and reports:

```sh
PYTHONPATH=src python3 examples/linux_pair_target_detailed.py \
  --profile /tmp/expra-endpoint-2-target \
  --report /tmp/expra-endpoint-2-linux.json
```

Expected target events include `ready` with the current version and
`discovery_started: true`. On Windows, installation/runner preparation and
initiator commands are:

```powershell
irm https://raw.githubusercontent.com/20204166/expra-connect/main/install/install-online.ps1 | iex
irm https://raw.githubusercontent.com/20204166/expra-connect/main/run_peer.py -OutFile run_peer.py
py run_peer.py `
  --role initiator `
  --profile "$env:LOCALAPPDATA\expra-endpoint-2-initiator" `
  --wait 60 `
  --report endpoint-2-windows.json `
  | Tee-Object -FilePath endpoint-2-windows-console.log
```

Expected initiator sequence: `started`, `discovered`, `paired`, `connected`,
`shared_capability_result`. If discovery works but pairing fails, test the
advertised address/port `27321` from Windows using
`Test-NetConnection <address> -Port 27321`; record the exact test and output.
Do not disable the firewall. For multiple interfaces, constrain the target to
the intended reachable address by constructing its runtime with the
`advertised_addresses` setting; the detailed example uses the runtime default.

For rotation, use the current `run_peer.py` runner on both sides, with the
target command:

```sh
PYTHONPATH=src python3 run_peer.py \
  --role target \
  --profile /tmp/expra-endpoint-2-rotation-target \
  --advertise-address 192.168.55.107 \
  --rotate-after 15 \
  --wait 45 \
  --report /tmp/expra-endpoint-2-linux-rotation.json
```

And the Windows initiator command:

```powershell
py run_peer.py `
  --role initiator `
  --profile "$env:LOCALAPPDATA\expra-endpoint-2-initiator" `
  --peer-id LINUX_NODE_ID `
  --reconnect-after-rotation `
  --rotation-wait 20 `
  --wait 45 `
  --report endpoint-2-windows-rotation.json
```

Expected additional events: `rotated`, `reconnected_after_rotation`, and
`shared_after_rotation`. The stable node ID remains the same while transport
generation and TLS fingerprint change. Record target and initiator commands
and outcomes independently.

### Endpoint 3: Restart, Persistence, And Revoke

After a successful Endpoint 2 run, preserve identity state and collect the
initiator diagnostics before restart:

```powershell
expra-peer --profile "$env:LOCALAPPDATA\expra-endpoint-2-initiator" diagnostics > endpoint-3-before.json
```

Restart the initiator using the same profile, then verify existing trust with
the same peer ID:

```powershell
py run_peer.py `
  --role initiator `
  --profile "$env:LOCALAPPDATA\expra-endpoint-2-initiator" `
  --existing-peer-id LINUX_NODE_ID `
  --peer-id LINUX_NODE_ID `
  --report endpoint-3-after.json
```

To test revocation, execute the documented self-revoke operation and retain
the result:

```powershell
py run_peer.py `
  --role initiator `
  --profile "$env:LOCALAPPDATA\expra-endpoint-2-initiator" `
  --existing-peer-id LINUX_NODE_ID `
  --peer-id LINUX_NODE_ID `
  --revoke-self `
  --report endpoint-3-revoke.json
```

Expected: stable identity and trusted peer survive restart; revocation is
followed by `post_revoke_denied`, and the target grant is absent. Do not delete
the profile between these steps. If the harness/runtime combination does not
produce the listed events, record the exact command and actual outcome rather
than claiming the endpoint passed.

### Evidence Submission

Preserve exact commands, stdout/stderr, exit status, and JSON reports for every
endpoint. Suggested evidence includes Linux and Windows reports/consoles,
`endpoint-3-before.json`, `endpoint-3-after.json`, and
`endpoint-3-revoke.json`. Record VPN/firewall conditions separately. Reports
must not contain private keys, HMAC secrets, invitation material, fencing
token values, or raw identity-file contents.

## Baseline `run_peer.py`

The Windows initiator console supplied for this run showed both executions at
version `1.2.0.0`:

The original command lines for these two historical baseline executions were
not retained with the supplied console output, so this report records the
observed commands elsewhere as reproducible procedures rather than presenting
reconstructed invocations as commands actually run.

```text
started role=initiator version=1.2.0.0 state=started
discovered address=192.168.55.107 port=27321 source=ipv4
paired peer=30cbb263... permissions=read_state
connected peer=30cbb263... tls_verified=true generation=1
shared capability=test.read_state outcome=success

started role=initiator version=1.2.0.0 state=started
discovered address=192.168.55.107 port=27321 source=ipv4
paired peer=c2b8e35a... permissions=read_state
connected peer=c2b8e35a... tls_verified=true generation=1
shared capability=test.read_state outcome=success
```

Both runs also recorded successful route attempts for pairing and connection.
The observed pairing/connection latencies were `63.0/47.0 ms` and
`500.0/579.0 ms` respectively. The target-side reports confirmed
`version: 1.2.0.0`, `state: started`, and discovery enabled.

## Extended One-Way Run

Command shape used with the installed wheel:

```sh
.venv/bin/python run_peer_extended.py \
  --role initiator \
  --profile /tmp/opencode/peer-tests-1.2.0.0/extended-local-3/initiator-profile \
  --report /tmp/opencode/peer-tests-1.2.0.0/extended-local-3/initiator.json \
  --peer-id TARGET_NODE_ID \
  --advertise-address 192.168.55.107 \
  --wait 30 \
  --reconnect-after-rotation \
  --rotation-wait 25 \
  --restart-check \
  --revoke-self
```

Observed initiator event sequence:

```text
started
discovered
route_attempt pairing succeeded
paired permissions=read_state
route_attempt connection succeeded
connected tls_verified=true generation=1
shared_capability_result outcome=success
waiting_for_rotated_peer
route_attempt connection succeeded
reconnected_after_rotation
shared_after_rotation outcome=success
self_revoked
post_revoke_denied type=RemoteTransportError
restored_trust
diagnostics generation=1 routes=2 connections=3
```

The target report recorded `rotated generation=2`. The initiator exited with
status `0`.

## Windows Target Observation

The Windows target was also run with the current script and package:

```text
[27] started role=target version=1.2.0.0 state=started
[28] target_ready
[29] pairing_request
[30] rotated
[31] pairing_request
```

This proves that the target started on `1.2.0.0`, remained available through a
transport rotation, and received two pairing requests. In normal one-way mode,
the target callback approves the pairing and allows `test.read_state`; the
initiator report is the side that records the resulting share. A manual
`KeyboardInterrupt` while stopping the target can print a traceback because the
host harness does not suppress interruption around its keep-alive sleep; it is
not a runtime startup or rotation failure.

This target command did not include `--bidirectional-surfaces`, so it does not
register the structured `desktop`, `desktop/settings`, and `device/status`
surfaces. Both peers must include that flag for the full-share matrix.

## Device Identity Evidence

The `1.2.0.0` runtime startup creates or loads `device_identity.json` from the
active `NodeIdentity` root, and the extended harness runs on that runtime. The
acceptance event reports intentionally redact the device fingerprint, so the
target output above does not by itself prove persistence of the device identity
across restart. The extended `--restart-check` proves persisted trust
restoration, not the device identity fingerprint specifically. A profile that
contains an older unrelated device root is migrated to the existing network
root without changing `NodeId`, transport proofs, trust state, or cluster state.

An explicit two-start diagnostics check was run against a fresh profile using
the installed wheel. The result was:

```text
version= 1.2.0.0
node_id_same= True
fingerprint_same= True
hardware_hint_changed= False
```

This directly proves that the device identity was created, loaded again, and
remained bound to the same node across runtime restarts. The diagnostics output
contains only the public fingerprint and operational state.

Verify the device identity without printing the private key. Run this before
and after restarting the same profile, and compare the two outputs:

```powershell
py -c "import json,os; p=os.path.join(os.environ['LOCALAPPDATA'],'expra-extended-target','device_identity.json'); d=json.load(open(p,encoding='utf-8')); print('node_id='+d['node_id']); print('fingerprint='+d['fingerprint'])"
```

The `node_id` and `fingerprint` must remain unchanged. Do not print the whole
JSON file because it contains private key material.

## Bidirectional Full Share

The first local attempts were intentionally retained as diagnostic evidence:

- The target discovered and connected to an unrelated peer at
  `192.168.55.103`, rather than the local initiator.
- The local initiator then reported `discovery_timeout` with a nonmatching
  peer count.
- The target-side surface checks stopped at authorization denial because the
  selected peer was not the matching bidirectional harness.

This is a peer-selection/environment setup issue. It does not provide evidence
that the full-share matrix passed or failed against a matching pair.

A later cross-host retry used the Linux initiator against the advertised
Windows peer at `192.168.55.103`. Pairing and reverse connection both passed,
but the first granted surface read returned `RemoteAuthorizationError`. This
confirms that the Windows target was still running without
`--bidirectional-surfaces`; the target had not registered the structured
surfaces. The retry is therefore `BLOCKED`, not a full-share pass.

Run the final test with fresh profiles and the target kept in a dedicated
Linux terminal:

```sh
.venv/bin/python run_peer_extended.py \
  --role target \
  --profile /tmp/expra-1.2-bidirectional-target \
  --report /tmp/expra-1.2-bidirectional-target.json \
  --advertise-address 192.168.55.107 \
  --wait 120 \
  --rotate-after 30 \
  --bidirectional-surfaces
```

Read only the target's public node ID for the initiator command; do not print
the identity file contents:

```sh
.venv/bin/python -c 'import json; print(json.load(open("/tmp/expra-1.2-bidirectional-target/identity.json"))["node_id"])'
```

Then run the Windows initiator with that ID and preserve its console output
and JSON report:

```powershell
py run_peer_extended.py `
  --role initiator `
  --profile "$env:LOCALAPPDATA\expra-1.2-bidirectional-initiator" `
  --report expra-1.2-bidirectional-initiator.json `
  --peer-id TARGET_NODE_ID `
  --advertise-address 192.168.55.103 `
  --wait 60 `
  --reconnect-after-rotation `
  --rotation-wait 35 `
  --restart-check `
  --revoke-self `
  --bidirectional-surfaces
```

A passing initiator report must include `reverse_paired`,
`reverse_connected`, the complete surface denial/grant matrix,
`reconnected_after_rotation`, `shared_after_rotation`, `self_revoked`,
`surface_denied`, `restored_trust`, and `diagnostics`, with no `error` event.

## Evidence Locations

- Baseline target reports: `/tmp/opencode/peer-tests-1.2.0.0/run-peer*`
- Extended initiator report: `/tmp/opencode/peer-tests-1.2.0.0/extended-local-3/initiator.json`
- Extended target report: `/tmp/opencode/peer-tests-1.2.0.0/extended-local-3/target.json`
- Bidirectional diagnostic attempts: `/tmp/opencode/peer-tests-1.2.0.0/bidirectional-local*`
- 1.4.2.0 Linux baseline and extended initiator reports:
  `/tmp/opencode/peer-tests-1.4.2.0/linux-baseline.json` and
  `/tmp/opencode/peer-tests-1.4.2.0/linux-initiator.json`

No private keys, HMAC secrets, invitation material, fencing tokens, or raw
identity-file contents are included in this report.

## 1.4.2.0 Linux Initiator Command Outcomes

These commands were run from the Linux checkout on `192.168.55.107` using
Python 3.12.3 and the source tree (`PYTHONPATH=src`). The discovered target was
`192.168.55.103:27321`. The target identity is not printed here.

### Extended Bidirectional Attempt

Command used:

```sh
PYTHONPATH=src python3 run_peer_extended.py \
  --role initiator \
  --profile /tmp/opencode/peer-tests-1.4.2.0/linux-initiator \
  --report /tmp/opencode/peer-tests-1.4.2.0/linux-initiator.json \
  --advertise-address 192.168.55.107 \
  --wait 240 \
  --rotate-after 90 \
  --bidirectional-surfaces
```

Outcome: exit status `1`. Events, in order:
`started`, `discovered`, pairing route succeeded, `reverse_paired`, connection
route succeeded, `reverse_connected`, three `surface_denied` events for
`desktop` read/review/action, then `error type=RemoteAuthorizationError`.
The structured surface matrix did not complete, so this is `BLOCKED` pending a
target started with `--bidirectional-surfaces`. The 90-second rotation was not
reached because the harness exited at authorization denial. Target must also
be restarted with fresh/appropriate profiles and the bidirectional flag before
repeating.

### Baseline One-Way Attempt

Command used:

```sh
PYTHONPATH=src python3 run_peer.py \
  --role initiator \
  --profile /tmp/opencode/peer-tests-1.4.2.0/linux-baseline \
  --advertise-address 192.168.55.107 \
  --wait 60 \
  --report /tmp/opencode/peer-tests-1.4.2.0/linux-baseline.json
```

Outcome: exit status `1`. The initiator discovered the same target, completed
pairing with `read_state`, connected with `tls_verified=true`, then emitted
`error type=RemoteAuthorizationError` rather than
`shared_capability_result`. This is an authorization/setup block: the reachable
target did not authorize `test.read_state` for this run. A successful TCP/TLS
connection alone is not evidence of a successful share.

### Supporting Commands And Outcomes

| Command | Outcome |
| --- | --- |
| `hostname -I` | Reported LAN address `192.168.55.107` plus other host interfaces; LAN address used for advertisement. |
| `python3 --version` | `Python 3.12.3`. |
| `python3 -c "import expra_connect; print(expra_connect.__version__)"` | Failed with `ModuleNotFoundError`; package is not installed into system Python. |
| `PYTHONPATH=src python3 -c "import expra_connect; print(expra_connect.__version__)"` | Succeeded; printed `1.4.2.0`. |
| `python3 run_peer_extended.py --help` | Failed because source import path was not set (`ModuleNotFoundError`). |
| `PYTHONPATH=src python3 run_peer.py --help` | Succeeded and listed runner options. |
| `python3 -m compileall -q run_peer.py run_peer_extended.py examples` | Succeeded with no output. |
| `git status --short --branch` | Reported `main...origin/main` and pre-existing untracked `.expra-*` target profiles; no changes were made to those profiles. |
| `python3 -m unittest discover -s tests` | Initial attempt failed because system Python could not import the source package; rerun below passed. |
| `PYTHONPATH=src python3 -m unittest discover -s tests` | Passed: 475 tests. Existing code-size warning and expected diagnostic log messages appeared. |
| `ruff check .` | Failed with 24 existing findings across source/tests, including unused noqa directives, loop-closure warnings, and import/style findings. No lint-related files were changed in this documentation task. |
| `pyright` | Failed with 6 existing typing errors across `observability.py` and tests. No typing-related files were changed in this documentation task. |
| `mypy src tests` | Could not run: `mypy` executable is not installed in this environment. |
| `git diff --check` | Passed with no output. |

The attempted extended run supersedes the earlier draft command that omitted
`--peer-id`, but the harness intentionally supports discovery selection when
that option is absent. In the later 1.4.2.0 run, discovery selected the target
at `192.168.55.103` as expected. Keep generated reports/profiles outside the
checkout and do not commit them.
