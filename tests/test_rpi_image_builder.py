"""Tests for the Raspberry Pi 3 (`rpi3-64`) HAOS Image Builder & Repacker."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
import json

from haos_image.build_rpi_image import (
    MTOOLS_DIR,
    _build_gpt_disk_with_fat32_boot,
    parse_gpt_partitions,
    parse_user_spec,
    stage_boot_and_config_overlay,
    stage_orchestra_supervisor_payload,
)


class TestRPi3ImageBuilder(unittest.TestCase):
    """Verify GPT disk construction, mtools FAT32 injection, and supervisor payload staging."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)
        self.build_dir = self.work_dir / "build"
        self.dist_dir = self.work_dir / "dist"
        self.build_dir.mkdir(parents=True, exist_ok=True)
        self.dist_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _fake_hacs_zip(self) -> Path:
        path = self.work_dir / "hacs.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"domain": "hacs", "version": "2.0.5"}))
            zf.writestr("__init__.py", "")
        return path

    def test_parse_user_spec(self) -> None:
        self.assertEqual(
            parse_user_spec("Dee Ben:Dee:hunter22"),
            {"name": "Dee Ben", "username": "dee", "password": "hunter22", "admin": True},
        )
        generated = parse_user_spec("Sam:sam")
        self.assertGreaterEqual(len(generated["password"]), 16)
        with self.assertRaises(ValueError):
            parse_user_spec("nobody")

    def test_stage_payload_and_build_gpt_fat32_image(self) -> None:
        # 1. Stage onboarding, Orchestra, HACS and the setup plan; nothing about the home
        users = [parse_user_spec("Dee Ben:dee:hunter22")]
        stage_info = stage_orchestra_supervisor_payload(
            self.build_dir, self.dist_dir, users=users, hacs_zip=self._fake_hacs_zip()
        )
        preload_tar = Path(stage_info["preload_tar_path"])
        self.assertTrue(preload_tar.exists())
        self.assertEqual(stage_info["hacs_version"], "2.0.5")
        self.assertEqual(stage_info["users"], ["dee"])

        with tarfile.open(preload_tar, "r:gz") as tar:
            names = set(tar.getnames())
            self.assertIn("homeassistant/.storage/onboarding", names)
            self.assertIn("homeassistant/.storage/auth", names)
            self.assertIn("homeassistant/.storage/orchestra.setup_plan", names)
            self.assertIn("homeassistant/custom_components/orchestra/setup_agent.py", names)
            self.assertIn("homeassistant/custom_components/hacs/manifest.json", names)
            # Dropped: the DNS sinkhole, the add-on, a fixed dashboard and pre-filled registries
            self.assertFalse(any(n.startswith(("dns", "addons")) for n in names))
            self.assertNotIn("homeassistant/.storage/lovelace", names)
            self.assertNotIn("homeassistant/.storage/core.config_entries", names)
            self.assertFalse(any("__pycache__" in n for n in names))
            config_yaml = tar.extractfile("homeassistant/configuration.yaml").read().decode()
            self.assertIn("default_config:", config_yaml)
            self.assertIn("\norchestra:", config_yaml)
            plan = json.loads(tar.extractfile("homeassistant/.storage/orchestra.setup_plan").read())
            self.assertEqual(plan["key"], "orchestra.setup_plan")
            self.assertEqual(plan["data"]["users"][0]["username"], "dee")

        handover = Path(stage_info["handover_path"]).read_text()
        self.assertIn("username dee, password hunter22", handover)

        # 2. Build the boot overlay and GPT disk image
        boot_overlay = stage_boot_and_config_overlay(
            self.build_dir,
            preload_tar,
            wifi_ssid="TestHomeWiFi",
            wifi_psk="SecretPass123",
        )
        img_path = self.dist_dir / "test_haos_rpi3-64.img"
        _build_gpt_disk_with_fat32_boot(img_path, boot_overlay, preload_tar, total_size_mb=48)

        # 3. Verify GPT partitions
        partitions = parse_gpt_partitions(img_path)
        self.assertEqual(len(partitions), 3)
        self.assertEqual(partitions[0]["name"], "hassos-boot")
        self.assertEqual(partitions[1]["name"], "CONFIG")
        self.assertEqual(partitions[2]["name"], "hassos-data")

        # 4. Verify FAT32 files in Partition 1 (`hassos-boot`) using `mdir`
        boot_offset = partitions[0]["offset_bytes"]
        mdir_out = subprocess.run(
            [str(MTOOLS_DIR / "mdir"), "-i", f"{img_path}@@{boot_offset}", "::"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("CONFIG", mdir_out)
        self.assertIn("ORCHESTR", mdir_out.upper())
