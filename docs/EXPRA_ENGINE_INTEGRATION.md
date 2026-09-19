# Expra Engine Integration

Expra Engine owns the application profile, Tk lifecycle, and UI delivery.
Expra Connect owns peer identity, persistence, TLS, discovery, pairing,
authorization, transport, capability sharing, and optional cluster state.

## Construction and Startup

Create the runtime during application composition, but do not expect network
activity from construction or import:

```python
runtime = ConnectRuntime(
    ConnectConfig(
        profile_dir=engine_profile / "connect",
        display_name=engine_name,
        provider=engine_peer_provider,
        service_type="_expra-engine._tcp.local.",
        on_pairing_request=engine_approve_pairing,
    )
)
status = runtime.start()
```

The runtime persists identity before it advertises. It starts the listener first,
then advertises the actual bound port and TLS fingerprint. `status` distinguishes
listener failure, disabled discovery, and unavailable discovery. Do not publish
your own endpoint from configuration values.

Network pairing is composed into the listener. `on_pairing_request` is the host
approval boundary: return `True` only after the Engine has obtained the user's
approval. The runtime then validates the exact confirm binding, persists the
grant, refreshes the live listener ACL, and supports authenticated hello/read
requests over the pinned TLS transport. Omitting the callback fails closed and
rejects pairing requests.

## Callback Handoff

Connect callbacks run on the worker/network context that produced the event and
are invoked only after canonical connection/discovery state has been updated.
They never run on Tk and the package never imports Tk. Expra Engine must enqueue
them:

```text
Expra Connect callback
    -> TkDeliveryQueue
    -> Tk main thread
    -> UICoordinator
```

The callback must be short and must not call Tk directly. A callback that needs
to update a page should enqueue a typed update and let the UI coordinator apply
visibility, stale-node, and presentation rules.

## State and Security Boundaries

Discovery, pairing, trust/grants, connection, authorization, and cluster
membership remain separate. A discovered peer is not trusted; a paired peer is
not necessarily connected; a connected peer is not automatically authorized or
a cluster member. Use the runtime's registry and pairing/cluster owners rather
than deriving one state from another.

The supplied profile is application-owned. Identity, `peer-tls.crt`,
`peer-tls.key`, and trust/grant state remain inside that profile. Do not share a
profile between applications unless shared identity is an explicit requirement.
Never log secrets, private keys, grant material, or fencing-token values.

## Shutdown

Call `runtime.shutdown()` during engine close. It is idempotent. The runtime
invalidates its lifecycle generation, cancels expiry work, stops discovery,
stops the listener, and preserves durable identity and trust state. On restart,
callbacks from the old lifecycle are ignored.
