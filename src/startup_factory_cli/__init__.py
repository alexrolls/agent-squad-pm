"""Startup Factory project-scoped installer."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("startup-factory")
except PackageNotFoundError:  # Source checkout and focused unit tests.
    __version__ = "0+unknown"


__all__ = ["__version__"]
