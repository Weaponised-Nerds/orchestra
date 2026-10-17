"""Tests for the Raspberry Pi 3 (`rpi3-64`) HAOS Image Builder & Repacker."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from haos_image.build_rpi_image import (
    _build_gpt_disk_with_fat32_boot,
    parse_gpt_partitions,
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

    def test_stage_payload_and_build_gpt_fat32_image(self) -> None:
        # 1. Stage the pre-hijacked supervisor bundle
        stage_info = stage_orchestra_supervisor_payload(self.build_dir, self.dist_dir)
        preload_tar = Path(stage_info["preload_tar_path"])
        self.assertTrue(preload_tar.exists())
        self.assertEqual(stage_info["devices_onboarded"], 23)
        self.assertEqual(stage_info["circuits_identified"], 5)

        # Verify archive contains homeassistant/.storage/onboarding, lovelace, energy, and addons/local/orchestra
        with tarfile.open(preload_tar, "r:gz") as tar:
            names = set(tar.getnames())
            self.assertIn("homeassistant/.storage/onboarding", names)
            self.assertIn("homeassistant/.storage/lovelace", names)
            self.assertIn("homeassistant/.storage/energy", names)
            self.assertIn("homeassistant/.storage/conductor.entity_bindings", names)
            self.assertIn("homeassistant/custom_components/orchestra/manifest.json", names)
            self.assertIn("addons/local/orchestra/config.yaml", names)

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
            ["/opt/homebrew/bin/mdir", "-i", f"{img_path}@@{boot_offset}", "::"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("CONFIG", mdir_out)
        self.assertIn("ORCHESTR", mdir_out.upper())
