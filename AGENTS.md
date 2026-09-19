# Repository Guide

Expra Connect is a headless connection library. Do not add Tk, scanner,
hardware, game-engine, or streaming dependencies. Keep discovery, pairing,
authentication, authorization, connection, sharing, and cluster membership as
separate axes.

Never log private keys, HMAC secrets, invitation material, or fencing-token
values. A token's presence may be recorded, never its value. Do not weaken
firewalls or automatically delete persisted state.

Run the unittest suite, Ruff, Pyright, and Mypy before claiming completion.
