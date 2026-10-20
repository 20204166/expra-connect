"""Canonical case inventory for supplemental release workload coverage."""

from __future__ import annotations

SUPPLEMENT_CASES: dict[str, frozenset[str]] = {
    "cluster_state": frozenset(
        {
            "1000:role authorization",
            "30000:role authorization",
            "100000:role authorization",
        }
    ),
    "persistence": frozenset(
        f"{size}:ProfileMigration:Profile migration" for size in (1, 16, 64)
    ),
    "connect": frozenset(
        {
            "discovery candidate validation",
            "discovery preferred endpoint",
            "discovery registry add + expire",
            "pairing relationship projection",
            "pairing trusted-candidate classification",
            "pairing next-generation proof classification",
            "capability authorized dispatch",
            "surface peer-granted dispatch",
            "surface cluster-granted dispatch",
        }
    ),
}
