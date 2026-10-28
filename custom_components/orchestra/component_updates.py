"""
Keeps HACS and Orchestra up to date without HACS.

HACS itself only works once someone signs in to GitHub, so Orchestra fetches the public
release zips from GitHub on its own, checks them, and swaps them into custom_components.
While HACS isn't set up yet nothing is running it, so the setup agent updates it silently.
Anything that is running (Orchestra, or HACS once set up) only changes on a restart, which
the setup assistant asks the person about first.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
import shutil
from typing import Any
import zipfile

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"
TIMEOUT = aiohttp.ClientTimeout(total=120)
MAX_ZIP_BYTES = 30 * 1024 * 1024

# Where each component's release lives, and where its files sit inside the release zip
COMPONENTS: dict[str, dict[str, str]] = {
    "hacs": {
        "repo": "hacs/integration",
        "zip_url": "https://github.com/hacs/integration/releases/download/{tag}/hacs.zip",
        "zip_dir": "",
    },
    "orchestra": {
        "repo": "Weaponised-Nerds/orchestra",
        "zip_url": "https://github.com/Weaponised-Nerds/orchestra/archive/refs/tags/{tag}.zip",
        "zip_dir": "custom_components/orchestra/",
    },
}


class UpdateError(Exception):
    """Why an update couldn't be checked or installed, in plain words."""


def installed_version(hass: HomeAssistant, name: str) -> str | None:
    manifest = Path(hass.config.path("custom_components", name, "manifest.json"))
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version"))
    except (OSError, ValueError):
        return None


def is_running(hass: HomeAssistant, name: str) -> bool:
    """True when the component is loaded, so new files only take effect after a restart."""
    if name == "orchestra":
        return True
    return bool(hass.config_entries.async_entries(name))


async def async_check(hass: HomeAssistant, name: str) -> dict[str, Any]:
    if name not in COMPONENTS:
        raise UpdateError(f"Orchestra can only update {', '.join(COMPONENTS)}.")
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            GITHUB_API.format(repo=COMPONENTS[name]["repo"]),
            headers={"Accept": "application/vnd.github+json"},
            timeout=TIMEOUT,
        ) as resp:
            if resp.status != 200:
                raise UpdateError(f"GitHub didn't answer ({resp.status}). Try again later.")
            release = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise UpdateError("Couldn't reach GitHub. Check the internet connection.") from err
    latest = str(release.get("tag_name") or "")
    installed = await hass.async_add_executor_job(installed_version, hass, name)
    return {
        "component": name,
        "installed": installed,
        "latest": latest,
        "update_available": bool(latest) and _norm(latest) != _norm(installed or ""),
        "set_up": name == "orchestra" or bool(hass.config_entries.async_entries(name)),
    }


async def async_install(hass: HomeAssistant, name: str, tag: str | None = None) -> dict[str, Any]:
    """Download and install a release. Returns whether a restart is needed."""
    if tag is None:
        tag = (await async_check(hass, name))["latest"]
    spec = COMPONENTS[name]
    session = async_get_clientsession(hass)
    try:
        async with session.get(spec["zip_url"].format(tag=tag), timeout=TIMEOUT) as resp:
            if resp.status != 200:
                raise UpdateError(f"Couldn't download {name} {tag} ({resp.status}).")
            data = await resp.content.read(MAX_ZIP_BYTES + 1)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise UpdateError("Couldn't reach GitHub. Check the internet connection.") from err
    if len(data) > MAX_ZIP_BYTES:
        raise UpdateError("That download is too big to be a real release.")
    target = Path(hass.config.path("custom_components", name))
    version = await hass.async_add_executor_job(_swap_in, data, spec["zip_dir"], name, target)
    _LOGGER.info("Orchestra installed %s %s", name, version)
    return {"component": name, "installed": version, "restart_needed": is_running(hass, name)}


def _swap_in(data: bytes, zip_dir: str, name: str, target: Path) -> str:
    """Unpack next to the old copy, check it, then swap it in. The old copy is kept until then."""
    staging = target.with_name(f".{name}.new")
    backup = target.with_name(f".{name}.old")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.infolist():
                path = member.filename
                if zip_dir:
                    # GitHub source zips put everything under "<repo>-<tag>/"
                    _, _, path = path.partition("/")
                    if not path.startswith(zip_dir):
                        continue
                    path = path[len(zip_dir):]
                if not path or member.is_dir():
                    continue
                dest = (staging / path).resolve()
                if staging.resolve() not in dest.parents:
                    raise UpdateError(f"Unsafe path in the {name} download.")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member))
        manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    except (zipfile.BadZipFile, OSError, ValueError) as err:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError(f"The {name} download wasn't a valid release.") from err
    if manifest.get("domain") != name:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError(f"The download isn't {name}.")
    shutil.rmtree(backup, ignore_errors=True)
    if target.exists():
        target.rename(backup)
    staging.rename(target)
    shutil.rmtree(backup, ignore_errors=True)
    return str(manifest.get("version"))


def _norm(version: str) -> str:
    return version.strip().lstrip("vV")
