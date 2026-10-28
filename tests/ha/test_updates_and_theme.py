"""
Orchestra keeping HACS and itself up to date, and the Conductor theme, inside a real Home Assistant core.

Needs `pytest-homeassistant-custom-component`, so it is skipped in the plain test run:
    pip install pytest-homeassistant-custom-component
    pytest tests/ha -p pytest_homeassistant_custom_component -o asyncio_mode=auto
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.orchestra.component_updates import UpdateError, async_install  # noqa: E402
from custom_components.orchestra.setup_agent import OrchestraSetupAgent  # noqa: E402
from custom_components.orchestra.theme_installer import (  # noqa: E402
    THEME_NAME,
    async_install_theme,
    async_restore_theme,
)

HACS_API = "https://api.github.com/repos/hacs/integration/releases/latest"
HACS_ZIP = "https://github.com/hacs/integration/releases/download/2.0.5/hacs.zip"
ORCHESTRA_API = "https://api.github.com/repos/Weaponised-Nerds/orchestra/releases/latest"
ORCHESTRA_ZIP = "https://github.com/Weaponised-Nerds/orchestra/archive/refs/tags/v9.9.9.zip"


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _installed(tmp_path: Path, name: str, version: str) -> Path:
    folder = tmp_path / "custom_components" / name
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"domain": name, "version": version}))
    (folder / "old_file.py").write_text("")
    return folder


async def test_hacs_is_kept_current_until_someone_sets_it_up(
    hass: HomeAssistant, tmp_path: Path, aioclient_mock
) -> None:
    hass.config.config_dir = str(tmp_path)
    folder = _installed(tmp_path, "hacs", "2.0.0")
    aioclient_mock.get(HACS_API, json={"tag_name": "2.0.5"})
    aioclient_mock.get(
        HACS_ZIP,
        content=_zip({"manifest.json": json.dumps({"domain": "hacs", "version": "2.0.5"}), "__init__.py": ""}),
    )
    agent = OrchestraSetupAgent(hass, {"users": []}, {})

    await agent._async_update_idle_hacs()
    assert json.loads((folder / "manifest.json").read_text())["version"] == "2.0.5"
    assert not (folder / "old_file.py").exists()
    assert not list((tmp_path / "custom_components").glob(".hacs.*"))

    # Once a day at most
    calls = aioclient_mock.call_count
    await agent._async_update_idle_hacs()
    assert aioclient_mock.call_count == calls

    # Never under a running HACS: that needs the person's OK and a restart
    agent.state["hacs_checked_at"] = 0
    MockConfigEntry(domain="hacs").add_to_hass(hass)
    await agent._async_update_idle_hacs()
    assert aioclient_mock.call_count == calls


async def test_orchestra_update_takes_only_its_own_folder(
    hass: HomeAssistant, tmp_path: Path, aioclient_mock
) -> None:
    hass.config.config_dir = str(tmp_path)
    folder = _installed(tmp_path, "orchestra", "0.4.3")
    aioclient_mock.get(ORCHESTRA_API, json={"tag_name": "v9.9.9"})
    aioclient_mock.get(
        ORCHESTRA_ZIP,
        content=_zip(
            {
                "orchestra-9.9.9/README.md": "",
                "orchestra-9.9.9/custom_components/orchestra/manifest.json": json.dumps(
                    {"domain": "orchestra", "version": "9.9.9"}
                ),
                "orchestra-9.9.9/custom_components/orchestra/frontend/orchestra-agent.js": "//",
            }
        ),
    )
    result = await async_install(hass, "orchestra")
    assert result == {"component": "orchestra", "installed": "9.9.9", "restart_needed": True}
    assert sorted(p.name for p in folder.rglob("*")) == ["frontend", "manifest.json", "orchestra-agent.js"]


async def test_a_bad_download_leaves_the_old_copy(hass: HomeAssistant, tmp_path: Path, aioclient_mock) -> None:
    hass.config.config_dir = str(tmp_path)
    folder = _installed(tmp_path, "hacs", "2.0.0")
    aioclient_mock.get(HACS_ZIP, content=_zip({"manifest.json": json.dumps({"domain": "not_hacs"})}))
    with pytest.raises(UpdateError):
        await async_install(hass, "hacs", "2.0.5")
    aioclient_mock.clear_requests()
    aioclient_mock.get(HACS_ZIP, content=_zip({"../../evil.py": "", "manifest.json": "{}"}))
    with pytest.raises(UpdateError):
        await async_install(hass, "hacs", "2.0.5")
    assert json.loads((folder / "manifest.json").read_text())["version"] == "2.0.0"
    assert not (tmp_path / "evil.py").exists()


async def _frontend(hass: HomeAssistant, tmp_path: Path, yaml: str) -> None:
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text(yaml)
    (tmp_path / "themes").mkdir(exist_ok=True)
    assert await async_setup_component(hass, "frontend", {})


async def test_theme_from_the_themes_folder(hass: HomeAssistant, tmp_path: Path) -> None:
    await _frontend(hass, tmp_path, "frontend:\n  themes: !include_dir_merge_named themes\n")
    result = await async_install_theme(hass)
    assert result["loaded_from"] == "themes folder"
    assert (tmp_path / "themes" / "conductor.yaml").exists()
    assert set(hass.data["frontend_themes"][THEME_NAME]["modes"]) == {"light", "dark"}
    assert hass.data["frontend_default_theme"] == THEME_NAME
    assert hass.data["frontend_default_dark_theme"] == THEME_NAME


async def test_theme_without_the_themes_folder_survives_a_restart(hass: HomeAssistant, tmp_path: Path) -> None:
    await _frontend(hass, tmp_path, "{}\n")
    result = await async_install_theme(hass)
    assert result["loaded_from"].startswith("Orchestra")
    assert hass.data["frontend_default_theme"] == THEME_NAME

    # What a restart looks like: the theme is gone and the frontend fell back to its own
    del hass.data["frontend_themes"][THEME_NAME]
    hass.data["frontend_default_theme"] = "default"
    await hass.async_block_till_done()
    await async_restore_theme(hass)
    assert THEME_NAME in hass.data["frontend_themes"]
    assert hass.data["frontend_default_theme"] == THEME_NAME
