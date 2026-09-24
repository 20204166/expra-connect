# Expra Connect Development MCP

This is a separate development/debugging package. It does not add MCP or
development dependencies to `expra-connect`.

## Install

```bash
python -m venv tools/expra_connect_mcp/.venv
tools/expra_connect_mcp/.venv/bin/pip install -e tools/expra_connect_mcp
```

## Run

Stdio is the primary transport:

```bash
expra-connect-mcp --config .expra-connect-mcp.toml
```

Loopback Streamable HTTP is available for local integration tests:

```bash
expra-connect-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

Initialize a local configuration with safe, non-mutating defaults:

```bash
expra-connect-mcp config init --path .expra-connect-mcp.toml
```

The server exposes `workspace_doctor`, source inspection, named `run_checks`
profiles, static architecture inspections, loopback discovery and transport
probes, plus read-only source resources and debugging prompts.

`multi_node_scenario` runs `pair_reconnect`, `endpoint_change`, and
`peer_restart` using one child process per Connect node. It requires both
`execution.allow_pairing = true` and `execution.allow_trust_mutation = true`;
the child profiles are temporary and all returned evidence is redacted.

`security_audit` exercises wrong-fingerprint, revoked-peer, unauthorized-
capability, and stale-transaction denial paths under the same explicit gates.

`cluster_audit` reports membership, roles, coordinator epoch, persistence
round-trips, and fencing-token presence using the canonical cluster engine.
The `failover` action requires `execution.allow_cluster_mutation = true` and
reports token presence and monotonicity without returning token values.

`performance_audit` measures warm-up-aware discovery, connect, and reconnect
latency distributions, canonical retry cadence, and child-runtime retention
counts for threads, tasks, sockets, registry records, and worker processes.

The read-only evaluation set is at `evaluations/phase2.xml` and contains ten
independent, directly verifiable questions covering the MCP and Connect
contracts.
