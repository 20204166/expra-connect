"""Development peer acceptance harness for Expra Connect.

This package is a host-level test runner, not part of the library API. It is
never packaged into the ``expra_connect`` wheel. Run it with
``python -m peer_harness``.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
