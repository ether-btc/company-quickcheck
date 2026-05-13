#!/usr/bin/env python3
"""Company QuickCheck Austria: Batch Austrian company status checker."""


# Version is managed by pyproject.toml; read at runtime via importlib.metadata
try:
    from importlib.metadata import version
    __version__ = version("company-quickcheck")
except (ImportError, Exception):
    # Fallback: read from pyproject.toml when not installed
    import os, re
    _pyproject = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pyproject.toml")
    try:
        with open(_pyproject) as f:
            m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', f.read())
            __version__ = m.group(1) if m else "0.1.0"
    except Exception:
        __version__ = "0.1.0"


from .core import process_batch
from .cli import main
from .api import search_company, is_deleted, format_company

__all__ = [
    'process_batch',
    'search_company',
    'is_deleted',
    'format_company',
    'main',
    '__version__',
]
