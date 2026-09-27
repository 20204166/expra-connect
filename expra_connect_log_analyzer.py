#!/usr/bin/env python3
"""Compatibility wrapper for the peer-harness log analyzer.

The analyzer implementation now lives in the development-only harness package
(``peer_harness.analyzer``). Run it directly as before, or through the harness
CLI with ``python -m peer_harness analyze ...``.

Examples:
    python expra_connect_log_analyzer.py endpoint.json
    python expra_connect_log_analyzer.py ./reports
    python expra_connect_log_analyzer.py a.json b.json --json
    python expra_connect_log_analyzer.py ./reports --full-identifiers
"""

from __future__ import annotations

import sys

from peer_harness.analyzer import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
