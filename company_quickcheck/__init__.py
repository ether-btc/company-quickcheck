#!/usr/bin/env python3
"""Company QuickCheck Austria: Batch Austrian company status checker."""


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
