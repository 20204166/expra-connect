# Expra Connect Examples

These examples are host-level demonstrations. They intentionally show the
composition work that an application must do around the library rather than

## Linux Pairing Target

`linux_pair_target_detailed.py` runs a Linux target for a Windows initiator.
The original `linux_pair_target.py` remains as the minimal version. The
detailed version shows:

- Importing `expra_connect.__version__` from the installed package.
- Creating a durable `ConnectRuntime` profile.
- Registering a target-owned capability before the runtime starts.
- Publishing redacted startup, discovery, and pairing evidence.
- Applying an explicit read-only pairing policy.
- Allowing the capability only for the authenticated caller.
- Keeping the runtime alive until the Windows side completes.
- Persisting a JSON report without private keys, secrets, or token values.

Run it from a checkout:

```sh
python3 examples/linux_pair_target_detailed.py \
  --profile .expra-windows-target \
  --report linux-target.json
```

Run it with an already installed package by downloading only the example:

```sh
curl -fsSL \
  https://raw.githubusercontent.com/20204166/expra-connect/main/examples/linux_pair_target_detailed.py \
  -o linux_pair_target.py
python3 -c "import expra_connect; print(expra_connect.__version__)"
python3 linux_pair_target.py
```

Leave the target running. On Windows, refresh and run the current acceptance
runner:

```powershell
irm https://raw.githubusercontent.com/20204166/expra-connect/main/run_peer.py -OutFile run_peer.py
py run_peer.py `
  --role initiator `
  --profile "$env:LOCALAPPDATA\expra-endpoint-2-initiator" `
  --wait 60 `
  --report endpoint-2-windows.json
```

The expected Windows event sequence ends with:

```text
started
discovery_event(candidate)
discovered
paired
connected
shared_capability_result
```

The Linux report should contain `ready`, `discovery_event`, and
`pairing_request`. If discovery works but pairing fails, inspect the advertised
endpoint addresses and test TCP port `27321` from Windows. If pairing works but
the capability request fails, compare the caller ID in `pairing_request` with
the capability allowlist decision.

## Cluster Membership Demo

`cluster_membership_demo.py` is deliberately separate from the network pairing
example. It demonstrates the cluster state machine after authenticated nodes
exist:

- Coordinator invite creation.
- Worker invite validation and membership assignment.
- Coordinator/worker role separation.
- Fencing-token presence without printing the secret value.
- Stale epoch rejection.
- Cluster persistence and restore.

Run it with:

```sh
python3 examples/cluster_membership_demo.py
```

Cluster membership is opt-in. A successful pair does not silently join either
node to a cluster.
