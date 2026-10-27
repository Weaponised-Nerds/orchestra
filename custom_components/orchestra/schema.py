"""
The schema library Home Assistant uses.

Home Assistant 2026.10 replaced voluptuous with probatio (the same API), and stopped
shipping voluptuous, voluptuous_openapi and voluptuous_serialize. Orchestra imports `vol`
from here so it runs on both. Raises ImportError outside Home Assistant, like the
direct imports it replaces.
"""

from __future__ import annotations

from typing import Any

try:
    import probatio as vol
except ImportError:  # Home Assistant before 2026.10
    import voluptuous as vol  # type: ignore[no-redef]


def to_openapi(schema: Any) -> dict[str, Any]:
    """JSON schema for a tool's parameters."""
    if hasattr(vol, "to_openapi"):
        return vol.to_openapi(schema)
    from voluptuous_openapi import convert

    return convert(schema)


def to_field_list(schema: Any, custom_serializer: Any = None) -> list[dict[str, Any]]:
    """A form's fields, the way Home Assistant sends them to its frontend."""
    if hasattr(vol, "to_field_list"):
        return list(vol.to_field_list(schema, custom_serializer=custom_serializer))
    import voluptuous_serialize

    return voluptuous_serialize.convert(schema, custom_serializer=custom_serializer)
