"""Compatibility facade for the bundled Lark Provider.

Provider-neutral code belongs in :mod:`memory_workspace.connector_protocol` and
:mod:`memory_workspace.project_memory`. New Core modules must not import this
facade; it remains for the existing CLI and integrations.
"""

from .providers.lark.config import (  # noqa: F401
    LARK_CONNECTOR_ID,
    LARK_REQUIRED_SCOPES,
    SUPPORTED_PROVIDERS,
    connector_status,
    disable_connector,
    enable_lark_connector,
    list_connector_sources,
    map_lark_source,
    plan_connector_sync,
    record_sync_success,
    validate_workspace_connector_files,
)

__all__ = [
    "LARK_CONNECTOR_ID",
    "LARK_REQUIRED_SCOPES",
    "SUPPORTED_PROVIDERS",
    "connector_status",
    "disable_connector",
    "enable_lark_connector",
    "list_connector_sources",
    "map_lark_source",
    "plan_connector_sync",
    "record_sync_success",
    "validate_workspace_connector_files",
]
