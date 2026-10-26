"""Tests for the Raspberry Pi 3 (`rpi3-64`) HAOS Image Builder & Repacker."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
import json
import shutil

from haos_image.build_rpi_image import (
    MTOOLS_DIR,
    _build_gpt_disk_with_fat32_boot,
    _e2fs_tool,
    _write_gpt_headers,
    grow_last_gpt_partition,
    inject_supervisor_payload_into_ext4_partition,
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


def _have_e2fsprogs() -> bool:
    try:
        return all(_e2fs_tool(name) for name in ("debugfs", "e2fsck", "resize2fs", "mke2fs"))
    except RuntimeError:
        return False


@unittest.skipUnless(_have_e2fsprogs(), "needs e2fsprogs")
class TestDataPartitionInjection(unittest.TestCase):
    """The payload must land intact even though the stock `hassos-data` is nearly full."""

    def test_grows_a_full_data_partition_and_copies_every_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            mib = 1024 * 1024
            img = work / "disk.img"
            total_sectors = 48 * mib // 512
            with img.open("wb") as f:
                f.truncate(total_sectors * 512)
            # A boot partition, then an 8 MiB data partition at the end, like HAOS
            _write_gpt_headers(img, total_sectors, [("hassos-boot", 2048, 34815), ("hassos-data", 34816, 51199)])
            data = parse_gpt_partitions(img)[1]

            # Format the data partition, add `/supervisor` as the stock image has it, then fill it
            part = work / "p.img"
            with part.open("wb") as f:
                f.truncate(data["size_bytes"])
            subprocess.run([str(_e2fs_tool("mke2fs")), "-q", "-F", "-t", "ext4", "-L", "hassos-data", str(part)], check=True)
            filler = work / "filler.bin"
            filler.write_bytes(b"x" * (5 * mib))
            debugfs = str(_e2fs_tool("debugfs"))
            subprocess.run([debugfs, "-w", "-R", "mkdir /supervisor", str(part)], check=True, capture_output=True)
            subprocess.run([debugfs, "-w", "-R", f"write {filler} /filler", str(part)], check=True, capture_output=True)
            with img.open("r+b") as f:
                f.seek(data["offset_bytes"])
                f.write(part.read_bytes())

            staging = work / "staging"
            files = staging / "homeassistant" / "custom_components" / "orchestra"
            files.mkdir(parents=True)
            for i in range(8):
                (files / f"module_{i}.py").write_bytes(bytes([65 + i]) * (512 * 1024))

            grown = grow_last_gpt_partition(img, 16 * mib)
            self.assertEqual(grown["partition_index"], 2)
            data = parse_gpt_partitions(img)[1]
            self.assertEqual(data["size_bytes"], 24 * mib)
            self.assertEqual(img.stat().st_size, 64 * mib)

            build = work / "build"
            build.mkdir()
            result = inject_supervisor_payload_into_ext4_partition(
                img, data["offset_bytes"], data["size_bytes"], staging, build
            )
            self.assertEqual(result["files_written"], 8)

            with img.open("rb") as f:
                f.seek(data["offset_bytes"])
                part.write_bytes(f.read(data["size_bytes"]))
            self.assertEqual(subprocess.run([str(_e2fs_tool("e2fsck")), "-fn", str(part)], capture_output=True).returncode, 0)
            out = work / "out"
            out.mkdir()
            subprocess.run([debugfs, "-R", f"rdump /supervisor/homeassistant {out}", str(part)], check=True, capture_output=True)
            for src in files.iterdir():
                copied = out / "homeassistant" / "custom_components" / "orchestra" / src.name
                self.assertEqual(copied.read_bytes(), src.read_bytes())
            if shutil.which("sgdisk"):
                self.assertIn("No problems found", subprocess.run(["sgdisk", "-v", str(img)], capture_output=True, text=True).stdout)
