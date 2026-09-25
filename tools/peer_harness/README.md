# Expra Connect Peer Harness

Development-only peer acceptance harness. It drives the public `expra_connect`
runtime API as a **target** or an **initiator** and writes an ordered, redacted
JSON report. It is never packaged into the `expra_connect` wheel.

## Install

From the repository root, install the harness into the same environment as the
library:

```sh
pip install -e tools/peer_harness
```

Without installing, run it from the checkout with the source on the path:

```sh
PYTHONPATH=tools/peer_harness/src python -m peer_harness --help
```

## Single-file download (no install)

For a machine that can only download files, build a self-contained zipapp and
run it with any Python 3.10+ that has the `expra_connect` wheel installed:

```sh
bash scripts/build_harness.sh          # produces dist/peer_harness.pyz
```

On the other machine, download that one file and run it directly:

```powershell
irm https://raw.githubusercontent.com/20204166/expra-connect/main/dist/peer_harness.pyz -OutFile peer_harness.pyz
py peer_harness.pyz --role initiator --profile "$env:LOCALAPPDATA\expra-initiator" --wait 60 --report endpoint-2-windows.json
```

The archive bundles only the harness and imports `expra_connect` from the
environment, so it stays a single downloadable file. Rebuild and commit
`dist/peer_harness.pyz` whenever the harness changes.

## Usage

```sh
python -m peer_harness \
  --role target \
  --profile /tmp/expra-target \
  --report /tmp/expra-target.json \
  --advertise-address 192.168.55.103 \
  --wait 120 \
  --rotate-after 30
```

```sh
python -m peer_harness \
  --role initiator \
  --profile /tmp/expra-initiator \
  --report /tmp/expra-initiator.json \
  --peer-id TARGET_NODE_ID \
  --wait 60 \
  --reconnect-after-rotation \
  --rotation-wait 30 \
  --restart-check \
  --revoke-self
```

Add `--bidirectional-surfaces` to exercise the surface authorization matrix on
both roles.

## Flags

| Flag | Role | Purpose |
|---|---|---|
| `--role` | both | `target` or `initiator` |
| `--profile` | both | durable runtime profile directory |
| `--report` | both | JSON report path (appended per run) |
| `--peer-id` | both | expected peer node ID |
| `--existing-peer-id` | initiator | require persisted trust after a restart |
| `--advertise-address` | both | explicit mDNS address; repeatable |
| `--rotate-after` | target | rotate transport after N seconds |
| `--reconnect-after-rotation` | initiator | reconnect and re-share after rotation |
| `--rotation-wait` | initiator | seconds to wait for the rotated advertisement |
| `--revoke-self` | initiator | revoke the caller and verify denial |
| `--wait` | both | discovery/hold window in seconds |
| `--bidirectional-surfaces` | both | run the structured surface matrix |
| `--restart-check` | initiator | restart and prove persisted trust restore |
| `--explicit-approval` | target | require an approval file before allowing the caller |
| `--approval-file` | target | sentinel file to create; defaults to `<report>.approve` |
| `--approval-wait` | target | seconds to wait for the sentinel before denying |

## Explicit approval

By default the target auto-approves pairing and allows `test.read_state`. With
`--explicit-approval`, the target records `pairing_pending` and waits for an
operator to create the approval file (default `<report>.approve`). The file is
consumed on approval, so every caller needs a fresh trigger:

```sh
python -m peer_harness --role target --profile /tmp/expra-target \
  --report /tmp/expra-target.json --explicit-approval --approval-wait 60 &

# On the operator side, after the initiator pairs:
touch /tmp/expra-target.json.approve
```

A missing trigger within `--approval-wait` records `pairing_denied` and fails
closed; the capability is never allowed.

Reports and terminal output contain only allowlisted operational fields; private
keys, HMAC secrets, invitation material, fencing tokens, transport proofs, raw
payloads, and raw exception text are never written.
