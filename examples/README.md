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

Leave the target running. On Windows, download the single-file harness and run
the initiator (the machine needs Python 3.10+ and the installed
`expra_connect` wheel, but not the repository or an extra install):

```powershell
irm https://raw.githubusercontent.com/20204166/expra-connect/main/dist/peer_harness.pyz -OutFile peer_harness.pyz
py peer_harness.pyz `
  --role initiator `
  --profile "$env:LOCALAPPDATA\expra-endpoint-2-initiator" `
  --wait 60 `
  --report endpoint-2-windows.json
```

From a checkout you can instead run `python -m peer_harness` after
`pip install -e tools/peer_harness`.

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

The harness also supports:

```text
--advertise-address ADDRESS       restricts mDNS to approved interfaces
--rotate-after SECONDS            rotates the target transport generation
--reconnect-after-rotation        reconnects after a new advertisement
--existing-peer-id NODE_ID        verifies persisted trust after restart
--revoke-self                     verifies target-side authorization revocation
--bidirectional-surfaces          exercises the structured surface matrix
--restart-check                   restarts and proves persisted trust restore
```

## Peer Harness

`tools/peer_harness` is the consolidated host-level acceptance harness for the
public runtime API. Run it from the checkout with the current installed wheel
(or with `PYTHONPATH=tools/peer_harness/src` while developing); both nodes must
use the same current wheel. It does not perform unsupported hardware, scanner,
streaming, or cluster operations.

Start the target and leave it running:

```sh
PYTHONPATH=tools/peer_harness/src .venv/bin/python -m peer_harness \
  --role target \
  --profile /tmp/expra-extended-target \
  --report /tmp/expra-extended-target.json \
  --advertise-address 192.168.55.107 \
  --wait 120 \
  --rotate-after 30
```

Run the initiator with a separate durable profile:

```sh
PYTHONPATH=tools/peer_harness/src .venv/bin/python -m peer_harness \
  --role initiator \
  --profile /tmp/expra-extended-initiator \
  --report /tmp/expra-extended-initiator.json \
  --peer-id TARGET_NODE_ID \
  --wait 60 \
  --reconnect-after-rotation \
  --rotation-wait 30 \
  --restart-check \
  --revoke-self
```

The normal initiator event order is `started`, `discovered`, `paired` (or
`restored_trust`), `connected`, `shared_capability_result`, and `diagnostics`.
With rotation, `waiting_for_rotated_peer`, `reconnected_after_rotation`, and
`shared_after_rotation` are inserted before diagnostics. With self-revocation,
`self_revoked` and `post_revoke_denied` are emitted after the first capability
result. With restart checking, `restored_trust` is emitted after the fresh
runtime verifies the persisted trust record. Target rotation emits
`rotated` after `target_ready` and before shutdown.

Optional flags are `--rotate-after SECONDS` (target),
`--reconnect-after-rotation` and `--rotation-wait SECONDS` (initiator),
`--restart-check` (initiator), and `--revoke-self` (initiator). A rotation
must advertise a changed transport generation; timeout or unchanged
generation is reported as a type-only error. Restart checking never deletes
profile state.

### Bidirectional Surface Run

Surfaces are structured data, not pixel streaming, and pairing alone grants no surface access.
Use two fresh profiles and reports, pin the reachable address,
and run both terminals with the same current wheel.

Terminal 1, target:

```sh
PYTHONPATH=tools/peer_harness/src .venv/bin/python -m peer_harness \
  --role target \
  --profile /tmp/expra-bidirectional-target-fresh \
  --report /tmp/expra-bidirectional-target.json \
  --advertise-address 192.168.55.103 \
  --wait 120 \
  --rotate-after 30 \
  --bidirectional-surfaces
```

Terminal 2, initiator:

```sh
PYTHONPATH=tools/peer_harness/src .venv/bin/python -m peer_harness \
  --role initiator \
  --profile /tmp/expra-bidirectional-initiator-fresh \
  --report /tmp/expra-bidirectional-initiator.json \
  --peer-id TARGET_NODE_ID \
  --advertise-address 192.168.55.107 \
  --wait 60 \
  --reconnect-after-rotation \
  --rotation-wait 30 \
  --restart-check \
  --revoke-self \
  --bidirectional-surfaces
```

The bidirectional order is `started`, `discovered`, `reverse_paired`,
`reverse_connected`, the surface matrix, then optional
`waiting_for_rotated_peer`, `reconnected_after_rotation`, explicit surface
re-grants and checks, `self_revoked` with typed `surface_denied`, and
`restored_trust` followed by denial until an explicit re-grant. Reports record
surface metadata and error types only.

Reports are JSON event arrays with ordered sequence numbers. Terminal output
and reports contain only allowlisted operational fields; private keys, HMAC
secrets, invitation material, fencing tokens, transport proofs, raw payloads,
fingerprints, and raw exception text are omitted. Preserve reports for
diagnosis, but do not commit profiles or reports containing local state.

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

## Structured Surface Sharing

Surfaces are host-owned structured data, not pixel streaming. Pairing establishes
trust only; it grants no surface access. Applications register arbitrary opaque
surface IDs and grant each access level separately:

```python
runtime.register_surface(
    "desktop/settings",
    read=lambda peer, params: {"theme": "dark"},
    review=lambda peer, params: {"pending": []},
    actions={"save": lambda peer, params: {"saved": True}},
)
runtime.grant_surface_access(peer_id, "desktop/settings", access="read")
runtime.grant_surface_access(peer_id, "desktop/settings", access="review")
runtime.grant_surface_access(peer_id, "desktop/settings", access="action")

# Stop all access, or revoke one access level explicitly.
runtime.stop_surface_share(peer_id, "desktop/settings")
runtime.revoke_surface_access(peer_id, "desktop/settings", access="read")
```

The typed provider exposes `read_surface`, `review_surface`, and
`invoke_surface_action`; named actions require their own explicit action grant.
