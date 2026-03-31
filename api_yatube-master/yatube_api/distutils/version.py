"""
Compatibility shim for `distutils.version.LooseVersion`.

Django 3.2 uses `LooseVersion` to parse the leading numeric parts of Django's
version string. Python 3.12 removed the stdlib `distutils` package, so we
provide a minimal, compatible subset here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Union


@dataclass(frozen=True)
class LooseVersion:
    """
    Minimal subset of `distutils.version.LooseVersion`.

    Django only relies on the `.version` attribute being a list where initial
    elements are ints from a dot-separated version prefix.
    """

    vstring: str
    version: List[Union[int, str]]

    def __init__(self, vstring: str):  # type: ignore[override]
        object.__setattr__(self, "vstring", vstring)

        # Extract the leading numeric part: e.g. "3.2.0" -> [3, 2, 0]
        # If there's no numeric prefix, keep it empty.
        m = re.match(r"^\s*(\d+(?:\.\d+)*)", vstring or "")
        if not m:
            object.__setattr__(self, "version", [])
            return

        nums = m.group(1).split(".")
        object.__setattr__(self, "version", [int(x) for x in nums])


# End of shim module.

# noqa: D107

# End of file.
