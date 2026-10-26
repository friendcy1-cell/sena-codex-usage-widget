"""Locate bundled Sena assets separately from external optional packs."""
from pathlib import Path


def application_paths(executable, module_file, frozen=False, bundle_dir=None):
    base = Path(executable if frozen else module_file).resolve().parent
    resources = base
    if frozen and bundle_dir:
        embedded = Path(bundle_dir).resolve()
        if (embedded / 'assets' / 'idle_alt.png').is_file():
            resources = embedded
    return base, resources
