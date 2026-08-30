"""Compatibility facade for the optional Lark adapter.

New core code must import :mod:`memory_workspace.project_memory` or
:mod:`memory_workspace.connector_protocol`, never this provider facade.
"""

from .project_memory import load_project_memory, project_memory_path
from .providers.lark.adapter import sync_lark_workspace

__all__ = ["load_project_memory", "project_memory_path", "sync_lark_workspace"]
