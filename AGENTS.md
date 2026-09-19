# Repository Guide

Expra Connect is a headless connection library. Do not add Tk, scanner,
hardware, game-engine, or streaming dependencies. Keep discovery, pairing,
authentication, authorization, connection, sharing, and cluster membership as
separate axes.

Before changing extracted behavior, inspect the corresponding mature
implementation under `/home/btn17/Downloads/exp` and adapt its lifecycle,
security boundaries, persistence ordering, and edge-case semantics. Reuse the
proven mechanism; change only application-specific inputs and host callbacks.
For runtime, discovery, or pairing changes, compare both implementations and
add a regression test for any behavior carried across.

Never log private keys, HMAC secrets, invitation material, or fencing-token
values. A token's presence may be recorded, never its value. Do not weaken
firewalls or automatically delete persisted state.

Run the unittest suite, Ruff, Pyright, and Mypy before claiming completion.
