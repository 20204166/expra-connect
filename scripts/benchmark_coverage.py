"""Canonical case inventory for supplemental release workload coverage."""

from __future__ import annotations

SUPPLEMENT_CASES: dict[str, frozenset[str]] = {
    "cluster_state": frozenset(
        f"{size}:{label}"
        for size in (1_000, 30_000, 100_000, 200_000)
        for label in (
            "role authorization",
            "role authorization worker denied",
            "role authorization subcoordinator grant",
            "role authorization subcoordinator missing grant",
            "role authorization subcoordinator expired grant",
            "role authorization paused source",
            "role authorization revoked destination",
            "role authorization invalid clock",
        )
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
