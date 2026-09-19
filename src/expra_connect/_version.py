"""Single source of truth for the Expra Connect package version.

The release helper derives the next four-segment version from source changes
and the newest wheel. Keeping this module a leaf lets setuptools read the
version without importing transport, TLS, or discovery dependencies.
"""

__version__ = "0.1.2.0"
