from ..files import discovery
from ..files.access import excluded_paths


def apply_access_exclusions(mode: str) -> None:
    """The system/app-data folders are pruned only in "all" mode — a
    "limited" user who deliberately adds a folder under ~/Library asked
    for exactly that folder."""
    discovery.EXCLUDED_PATHS.clear()
    if mode == "all":
        discovery.EXCLUDED_PATHS.update(excluded_paths())
