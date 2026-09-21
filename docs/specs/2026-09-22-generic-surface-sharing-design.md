# Generic Surface Sharing Design

## Goal

Expose application-neutral seams for sharing structured views and actions after
pairing, without embedding Expra analyser concepts, UI, scanner behavior,
hardware support, or pixel streaming in `expra-connect`.

## Concepts

### Trust

Pairing establishes and persists peer identity. Trust alone grants no surface
or action access.

### Surfaces

A surface is an opaque host-defined structured view. Examples include
`desktop`, `settings`, `device/status`, or `admin/users`. The library does not
interpret the surface name or its data schema.

Hosts register surface handlers for reads, optional reviews, and individually
named actions. A surface may represent a desktop state model, a phone/tablet
view, or an application section. It never represents pixel streaming or raw
input injection.

### Access

Access levels are independent:

- `read`: retrieve structured surface state.
- `review`: inspect host-defined reviewable information.
- `action:<name>`: invoke one explicitly registered action.

There is no broad control or all-surfaces permission.

## Public Host Seams

The runtime will expose a generic surface registration and authorization API
equivalent to:

```python
runtime.register_surface(
    "desktop",
    read=read_desktop_state,
    review=review_desktop_state,
    actions={"open_settings": open_settings},
)
runtime.grant_surface_access(peer_id, "desktop", access="read")
runtime.revoke_surface_access(peer_id, "desktop", access="read")
runtime.stop_surface_share(peer_id, "desktop")
```

Exact signatures may follow existing project naming and typing conventions,
but the semantics are fixed: registration defines host capability, grants
authorize a specific peer and surface, and stop/revoke immediately blocks
future requests.

Surface grants are session-scoped by default. Persistence is opt-in and must
not be implied by persisted pairing trust.

## Request Flow

1. Pairing authenticates and persists the peer relationship.
2. Host code registers opaque surfaces and handlers.
3. Host code grants a specific peer access to a specific surface and level.
4. The remote provider requests a structured snapshot, review, or named action.
5. The target checks identity, source grant, surface, access, expiry, revocation,
   and host registration before invoking the handler.
6. The handler result is returned through the authenticated connection.

Requests remain typed and bounded. Handler failures must not expose private
keys, HMAC secrets, invitation material, fencing tokens, transport proofs, or
raw sensitive exception details.

## Cluster Interaction

Cluster membership remains orthogonal to pairing, direct surface grants, and
surface registration. Existing cluster membership, role, fencing, and
management behavior is preserved.

The authorization evaluator may accept either a direct peer grant or an
explicit cluster capability grant. A cluster grant can authorize a selected
surface and access level, but cannot bypass the target's registered surface or
action policy, expiry, revocation, or fencing checks. Cluster membership alone
does not grant all surfaces or device control.

Valid combinations include a trusted non-member with a direct read grant, a
cluster member with no surface grants, and a cluster grant limited to one
review surface while another surface remains private.

## Client Surface API

The authenticated provider will expose typed operations for reading a surface,
requesting review data, and invoking a named allowed action. Existing generic
capability requests remain supported as the lower-level extension mechanism.

The protocol will use structured request/response data and preserve unknown
application-defined surface payloads as host-owned data. No streaming,
scanner, game-engine, or hardware dependency is introduced.

## Authorization and Lifecycle

- Pairing does not auto-grant surfaces.
- Read, review, and action access are independently checked.
- Surface IDs and action names are validated identifiers.
- Stop and revoke take effect immediately for subsequent requests.
- Peer self-revocation invalidates all of that peer's access.
- Runtime shutdown removes non-persistent surface grants.
- Cluster fencing and expiry remain enforced for cluster-sourced grants.

## Verification

Tests will cover:

- Trusted but ungranted peers being denied.
- One surface being readable while another remains denied.
- Read access not enabling review or actions.
- Individual action allowlisting.
- Stop, expiry, peer revocation, and restart behavior.
- Direct peer grants and bounded cluster grants.
- Arbitrary nested surface names and host-defined payloads.
- Handler failures without sensitive output.
- Existing pairing, connection, sharing, and cluster tests remaining green.

## Out Of Scope

- Pixel-level desktop streaming.
- Keyboard, pointer, or touch injection.
- A built-in dashboard UI.
- Expra-specific analyser or scanner models.
- Automatic access based solely on cluster membership.
