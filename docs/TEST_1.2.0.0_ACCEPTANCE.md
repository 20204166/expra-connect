# Expra Connect 1.2.0.0 Acceptance Test

Date: 2026-09-22<br>
Package: `expra_connect-1.2.0.0`<br>
Wheel: `dist/expra_connect-1.2.0.0-py3-none-any.whl`

This report records host-level acceptance evidence for the installed `1.2.0.0`
wheel. Reports and profiles remain outside the repository under
`/tmp/opencode/peer-tests-1.2.0.0/`; they contain local runtime state and must
not be committed.

## Results

| Scenario | Initiator result | Status |
| --- | --- | --- |
| `run_peer.py` baseline, target `30cbb263...` | Pairing, TLS connection, and `test.read_state` share succeeded | PASS |
| `run_peer.py` baseline, target `c2b8e35a...` | Pairing, TLS connection, and `test.read_state` share succeeded | PASS |
| `run_peer_extended.py` one-way | Rotation, reconnect, second share, self-revoke denial, trust restore, and diagnostics succeeded | PASS |
| `run_peer_extended.py --bidirectional-surfaces` local attempt | Target selected a different discovered peer; surface matrix could not complete | BLOCKED |

The final bidirectional structured-surface share still requires one isolated
Linux target and one Windows initiator. It is not marked as passed in this
report.

## Baseline `run_peer.py`

The Windows initiator console supplied for this run showed both executions at
version `1.2.0.0`:

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

No private keys, HMAC secrets, invitation material, fencing tokens, or raw
identity-file contents are included in this report.
