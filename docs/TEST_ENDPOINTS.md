# Test Endpoints

This document defines the three standard acceptance endpoints for Expra
Connect. Run them in order. The first two are automated or host-level checks;
the second endpoint is the physical Linux-to-Windows acceptance path. The third
checks durable state and revocation after the connection test.

## Endpoint 1: Local Runtime And CLI

Purpose: prove installation, import, listener startup, diagnostics, and clean
shutdown without involving a second machine.

Linux:

```sh
expra-peer --version
python -c "import expra_connect; print(expra_connect.__version__)"
expra-peer --profile .expra-endpoint-1 --no-discovery diagnostics
expra-peer --profile .expra-endpoint-1 --no-discovery serve
```

Windows:

```powershell
expra-peer --version
py -c "import expra_connect; print(expra_connect.__version__)"
expra-peer --profile "$env:LOCALAPPDATA\expra-endpoint-1" --no-discovery diagnostics
expra-peer --profile "$env:LOCALAPPDATA\expra-endpoint-1" --no-discovery serve
```

Expected evidence:

- Version is `0.6.0.0` or the current release version.
- Import succeeds.
- Diagnostics report `state: started`.
- A real bound port and TLS fingerprint are reported.
- `Ctrl+C` stops the process without a traceback.

## Endpoint 2: Physical Pair, Connect, And Share

Purpose: prove real discovery, TLS pinning, pairing persistence, authenticated
connection, and a target-owned shared capability across Linux and Windows.

Start the Linux target using the import-level example. This confirms the
installed package version and registers the target-owned shared capability
before discovery begins:

```sh
python3 examples/linux_pair_target_detailed.py \
  --profile .expra-endpoint-2-target \
  | tee endpoint-2-linux-console.log
```

The Linux target must print a `ready` event with the current release version and
`discovery_started: true`. On Windows, install or refresh the package and
fetch the current runner:

```powershell
irm https://raw.githubusercontent.com/20204166/expra-connect/main/install/install-online.ps1 | iex
irm https://raw.githubusercontent.com/20204166/expra-connect/main/run_peer.py -OutFile run_peer.py
```

Run the Windows initiator without manually copying the Linux node ID. It uses
the first discovered compatible peer:

```powershell
py run_peer.py `
  --role initiator `
  --profile "$env:LOCALAPPDATA\expra-endpoint-2-initiator" `
  --wait 60 `
  --report endpoint-2-windows.json `
  | Tee-Object -FilePath endpoint-2-windows-console.log
```

Expected initiator events, in order:

```text
started
discovered
paired
connected
shared_capability_result
```

For multi-interface hosts, restrict advertisement to the address reachable by
the other machine. Repeat the option for more than one approved interface:

```sh
python run_peer.py --role target --profile .expra-endpoint-2-target \
  --advertise-address 192.168.55.107 \
  --report endpoint-2-linux.json
```

Every pairing and connection route attempt is recorded as a `route_attempt`
event with phase, address, port, source, outcome, and a redacted error string.

To test active transport rotation, start the target with a delayed rotation and
keep the initiator alive long enough to reconnect:

```sh
python run_peer.py --role target --profile .expra-endpoint-2-target \
  --rotate-after 15 --report endpoint-2-linux-rotation.json
```

```powershell
py run_peer.py `
  --role initiator `
  --profile "$env:LOCALAPPDATA\expra-endpoint-2-initiator" `
  --peer-id LINUX_NODE_ID `
  --reconnect-after-rotation `
  --rotation-wait 20 `
  --report endpoint-2-windows-rotation.json
```

Expected additional events are `rotated`, `reconnected_after_rotation`, and
`shared_after_rotation`. The stable node ID must remain unchanged while the
transport generation and TLS fingerprint change.

Expected evidence:

- Discovery reports the Linux candidate and matching advertised TLS fingerprint.
- Pairing grants only `read_state`.
- The authenticated connection completes.
- `test.read_state` returns a result from the Linux target.
- Linux and Windows JSON reports contain no private keys or pairing secrets.
- The Linux target console contains the Windows caller ID and pairing request.
- If discovery succeeds but pairing times out, test each advertised Linux
  address with `Test-NetConnection <address> -Port 27321` from Windows.

Keep the Linux target running until the Windows report reaches
`shared_capability_result`. Preserve both reports and both console logs.

## Endpoint 3: Restart, Persistence, And Revoke

Purpose: prove durable identity/trust behavior and immediate authorization
revocation after a successful physical connection.

On the initiator, first collect diagnostics:

```powershell
expra-peer --profile "$env:LOCALAPPDATA\expra-endpoint-2-initiator" diagnostics > endpoint-3-before.json
```

Stop and restart the initiator, then run the harness against the same profile
with the existing peer ID. This proves that trust and the stable identity are
loaded from disk rather than silently pairing again:

```powershell
py run_peer.py `
  --role initiator `
  --profile "$env:LOCALAPPDATA\expra-endpoint-2-initiator" `
  --existing-peer-id LINUX_NODE_ID `
  --peer-id LINUX_NODE_ID `
  --report endpoint-3-after.json
```

Confirm that the node identity, trusted peer, and grant survive the restart.
Then revoke the caller on the Linux target and prove a subsequent capability
request is denied:

```powershell
py run_peer.py `
  --role initiator `
  --profile "$env:LOCALAPPDATA\expra-endpoint-2-initiator" `
  --existing-peer-id LINUX_NODE_ID `
  --peer-id LINUX_NODE_ID `
  --revoke-self `
  --report endpoint-3-revoke.json
```

Expected evidence:

- The initiator identity remains unchanged after restart.
- The trusted peer and grant are present before revoke.
- The revoke command succeeds.
- The report contains `self_revoked` followed by `post_revoke_denied`.
- The target grant is absent immediately afterward.
- The Linux target remains a separate connection/membership concern; local
  revoke must not silently delete unrelated discovery or cluster state.

## Evidence Submission

Submit the following for evaluation:

```text
endpoint-2-linux.json
endpoint-2-linux-console.log
endpoint-2-windows.json
endpoint-2-windows-console.log
endpoint-3-before.json
endpoint-3-after.json
```

Also include the output of:

```text
expra-peer --version
```

Record firewall and VPN conditions separately. Do not disable the firewall or
open arbitrary ports. Classify failures as discovery, transport,
authentication, authorization, persistence, pairing, or cluster state.
