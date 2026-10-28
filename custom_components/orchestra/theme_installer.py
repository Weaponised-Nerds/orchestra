"""
The Conductor theme for Home Assistant.

Orchestra ships the theme (`themes/conductor.yaml`, light and dark modes from Conductor's
brand tokens). Installing it copies the file into `<config>/themes/`, which the Orchestra
image's configuration.yaml loads, and reloads themes. Where configuration.yaml doesn't load
that folder (a plain HACS install), Orchestra adds the theme to the frontend directly and
again on every start. Applying it makes it Home Assistant's default theme in both modes, so
everyone on "Use default theme" sees it; a person who picked another theme keeps theirs.
"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

THEME_STORE_KEY = f"{DOMAIN}.theme"
THEME_NAME = "Conductor"
THEME_FILE = Path(__file__).parent / "themes" / "conductor.yaml"
DATA_THEMES = "frontend_themes"  # homeassistant.components.frontend.DATA_THEMES
EVENT_THEMES_UPDATED = "themes_updated"


async def async_install_theme(hass: HomeAssistant, apply: bool = True) -> dict[str, Any]:
    """Install the Conductor theme and, by default, make it the default theme."""
    target = Path(hass.config.path("themes", "conductor.yaml"))
    await hass.async_add_executor_job(_copy, THEME_FILE, target)
    if hass.services.has_service("frontend", "reload_themes"):
        await hass.services.async_call("frontend", "reload_themes", blocking=True)
    themes = hass.data.get(DATA_THEMES)
    if themes is None:
        return {"error": "Home Assistant's frontend isn't running, so themes can't be installed."}
    loaded_from = "themes folder"
    inject = THEME_NAME not in themes
    if inject:
        await async_inject_theme(hass)
        loaded_from = "Orchestra (configuration.yaml doesn't load the themes folder)"
    await Store(hass, 1, THEME_STORE_KEY).async_save({"inject": inject, "applied": apply})
    if apply:
        await _async_set_default(hass)
    return {"theme": THEME_NAME, "installed": True, "applied": apply, "loaded_from": loaded_from}


async def async_restore_theme(hass: HomeAssistant) -> None:
    """On start: add the theme again where it isn't loaded from the themes folder."""
    saved = await Store(hass, 1, THEME_STORE_KEY).async_load() or {}
    if not saved.get("inject"):
        return
    await async_inject_theme(hass)
    # The frontend falls back to its own theme when the default was missing at start
    if saved.get("applied") and hass.data.get("frontend_default_theme") in (None, "default"):
        await _async_set_default(hass)


async def _async_set_default(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "frontend", "set_theme", {"name": THEME_NAME, "name_dark": THEME_NAME}, blocking=True
    )


async def async_inject_theme(hass: HomeAssistant) -> None:
    """Add the theme to the frontend without configuration.yaml."""
    themes = hass.data.get(DATA_THEMES)
    if themes is None or THEME_NAME in themes:
        return
    theme = await hass.async_add_executor_job(_load_theme)
    themes[THEME_NAME] = theme
    hass.bus.async_fire(EVENT_THEMES_UPDATED)


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.read_bytes() != source.read_bytes():
        shutil.copyfile(source, target)


def _load_theme() -> dict[str, Any]:
    import yaml

    return yaml.safe_load(THEME_FILE.read_text(encoding="utf-8"))[THEME_NAME]
