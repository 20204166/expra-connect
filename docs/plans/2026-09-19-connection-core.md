# Connection Core Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task.

**Goal:** Build an independently installable headless peer-connection repository without modifying System Analyzer.

**Architecture:** Pure identity, discovery, protocol, TLS, transport, pairing, sharing, and cluster modules use explicit persistence and callback seams. Application UI, scanners, and handlers remain outside the package.

**Tech Stack:** Python 3.10+, `cryptography`, optional Zeroconf, unittest, Ruff, Pyright, and Mypy.

---

### Tasks

- [ ] Implement identity and persistence-safe value types.
- [ ] Implement protocol framing, HMAC envelopes, and replay freshness.
- [ ] Implement TLS material, fingerprint pinning, socket deadlines, and cancellation.
- [ ] Implement discovery candidates, validation, ranking, TTL, and self-filtering.
- [ ] Implement directional pairing records, pending approval, confirmation, abort, and expiry.
- [ ] Implement generic capability sharing and authorization.
- [ ] Implement invite/join, role assignments, coordinator epochs, and offline membership.
- [ ] Add CLI demo and import-boundary tests.
- [ ] Run all quality gates and verify the source repository is untouched.
