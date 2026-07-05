"""Use-case extractors. Importing this package registers all extractors."""

from __future__ import annotations

# Import extractor modules for their registration side effects. Guarded so a
# not-yet-built extractor doesn't break the whole CLI.
for _mod in (
    "household_readiness",
    "condition_explainer",
    "visit_prep",
    "medication",
    "aging_home_safety",
    "travel_health",
):
    try:
        __import__(f"{__name__}.{_mod}")
    except ModuleNotFoundError:
        pass
