# Physical Two-Node Acceptance

This checklist validates the boundary that unit tests cannot prove. Run it on
two real machines, with the host firewall and VPN policy left enabled.

## Linux-to-Linux

1. Install the same wheel into isolated virtual environments on both nodes.
2. Start each node with a separate profile and `expra-peer ... serve`.
3. Confirm each status reports its actual bound port and TLS fingerprint.
4. Confirm discovery shows candidates only after `start()` and expires a stopped peer.
5. Approve pairing on the target through the host callback.
6. Confirm the initiator completes the exact transaction and authenticated `hello`.
7. Invoke one explicitly allowed shared capability.
8. Revoke the grant and confirm the next request is denied immediately.
9. Change or regenerate the target TLS material and confirm the pinned connection fails closed.
10. Restart both profiles and confirm identity, trust, and grants persist without logging secrets.

## Windows Runtime Node

Use Windows only as the black-box runtime node. Do not develop or run source
tools there. Install the wheel using the PowerShell installer, repeat the
listener, discovery, pairing, authenticated hello, capability, revoke, and
restart checks, and record firewall/VPN conditions with the result.

Record separately whether failures are discovery, transport, authentication,
authorization, or cluster-state failures. Passing the automated suite is not
evidence that the physical two-node path is fully validated.
