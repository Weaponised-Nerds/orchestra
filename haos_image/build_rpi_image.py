"""
Home Assistant OS (HAOS) Raspberry Pi 5 (`rpi5-64`) & Pi 3/4 Image Builder & Repacker.

Builds a flashable Raspberry Pi 5 image (`.img.xz` & `.img`) pre-loaded with:
1. Home Assistant OS (`rpi5-64` architecture for BCM2712 + RP1 Southbridge, with PCIe NVMe,
   RP1 UART0, and I2C enabled).
2. Pre-hijacked `.storage/` registries (`onboarding`, `auth`, `core.config`, `core.config_entries`,
   `core.device_registry`, `core.area_registry`, `core.entity_registry`).
3. Pre-bound Conductor Demand Response plugin (`.storage/conductor.entity_bindings`) with
   active load-step-verified switchboard circuit names.
4. Custom Grandpa-Simple Lovelace & Energy Dashboards (`.storage/lovelace`, `.storage/energy`).
5. In-process `custom_components/orchestra` + all 23 zero-cloud hardware adapters (including
   the iStore 180L/270L Heat Pump Wi-Fi & Modbus prober).
6. Local HAOS Supervisor Add-on (`/mnt/data/supervisor/addons/local/orchestra`).
"""

from __future__ import annotations

import argparse
import asyncio
import binascii
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
from typing import Any
import urllib.request
import uuid

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestra.agent.framework import GrandpaCommissioningAgent
from orchestra.config import OrchestraConfig
from orchestra.orchestrator import OrchestraEngine

OFFICIAL_HAOS_VERSION = "18.3"

BOARD_RELEASE_URLS: dict[str, str] = {
    "rpi5-64": (
        f"https://github.com/home-assistant/operating-system/releases/download/"
        f"{OFFICIAL_HAOS_VERSION}/haos_rpi5-64-{OFFICIAL_HAOS_VERSION}.img.xz"
    ),
    "rpi4-64": (
        f"https://github.com/home-assistant/operating-system/releases/download/"
        f"{OFFICIAL_HAOS_VERSION}/haos_rpi4-64-{OFFICIAL_HAOS_VERSION}.img.xz"
    ),
    "rpi3-64": (
        f"https://github.com/home-assistant/operating-system/releases/download/"
        f"{OFFICIAL_HAOS_VERSION}/haos_rpi3-64-{OFFICIAL_HAOS_VERSION}.img.xz"
    ),
}

MTOOLS_DIR = Path("/opt/homebrew/bin")
XZ_BIN = Path("/opt/homebrew/bin/xz")


def stage_orchestra_supervisor_payload(build_dir: Path, dist_dir: Path) -> dict[str, Any]:
    """
    Run the full 6-stage Grandpa-Simple Autonomous Agent pipeline into a staging
    `/mnt/data/supervisor` tree and pack it into `dist/orchestra-preload.tar.gz`.
    """
    staging_supervisor = build_dir / "staging_supervisor"
    if staging_supervisor.exists():
        shutil.rmtree(staging_supervisor)

    ha_config_dir = staging_supervisor / "homeassistant"
    addon_target_dir = staging_supervisor / "addons" / "local" / "orchestra"
    dns_target_dir = staging_supervisor / "dns"

    ha_config_dir.mkdir(parents=True, exist_ok=True)
    addon_target_dir.mkdir(parents=True, exist_ok=True)
    dns_target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Execute the full 6-stage Grandpa-Simple setup into ha_config_dir
    cfg = OrchestraConfig(ha_config_dir=ha_config_dir)
    engine = OrchestraEngine(cfg)
    agent = GrandpaCommissioningAgent(engine)
    commissioning_result = asyncio.run(agent.run_autonomous_grandpa_setup())

    # 2. Copy `custom_components/orchestra` and `orchestra` package into `/config`
    shutil.copytree(
        REPO_ROOT / "custom_components" / "orchestra",
        ha_config_dir / "custom_components" / "orchestra",
        dirs_exist_ok=True,
    )
    shutil.copytree(
        REPO_ROOT / "orchestra",
        ha_config_dir / "orchestra",
        dirs_exist_ok=True,
    )

    # 3. Populate the local Supervisor Add-on (`/mnt/data/supervisor/addons/local/orchestra`)
    addon_src = REPO_ROOT / "haos_image" / "addon_orchestra"
    for item in ("config.yaml", "Dockerfile", "run.sh"):
        shutil.copy2(addon_src / item, addon_target_dir / item)
    shutil.copytree(REPO_ROOT / "orchestra", addon_target_dir / "orchestra", dirs_exist_ok=True)
    shutil.copytree(
        REPO_ROOT / "custom_components",
        addon_target_dir / "custom_components",
        dirs_exist_ok=True,
    )
    shutil.copy2(REPO_ROOT / "pyproject.toml", addon_target_dir / "pyproject.toml")
    shutil.copy2(REPO_ROOT / "README.md", addon_target_dir / "README.md")

    # 4. Copy DNS liberation rules to `/mnt/data/supervisor/dns`
    shutil.copy2(
        ha_config_dir / "orchestra_dnsmasq_liberation.conf",
        dns_target_dir / "orchestra_dnsmasq_liberation.conf",
    )

    # 5. Create `dist/orchestra-preload.tar.gz`
    dist_dir.mkdir(parents=True, exist_ok=True)
    preload_tar_path = dist_dir / "orchestra-preload.tar.gz"
    with tarfile.open(preload_tar_path, "w:gz") as tar:
        for child in staging_supervisor.iterdir():
            tar.add(child, arcname=child.name)

    return {
        "staging_supervisor": str(staging_supervisor),
        "preload_tar_path": str(preload_tar_path),
        "preload_tar_size_bytes": preload_tar_path.stat().st_size,
        "devices_onboarded": commissioning_result.total_devices_onboarded,
        "visible_entities": commissioning_result.total_visible_entities,
        "circuits_identified": len(commissioning_result.circuit_probe_report.identified_circuits),
    }


def stage_boot_and_config_overlay(
    build_dir: Path,
    preload_tar_path: Path,
    board: str = "rpi5-64",
    wifi_ssid: str | None = None,
    wifi_psk: str | None = None,
) -> Path:
    """
    Create the `boot_overlay` directory containing:
    - `orchestra-preload.tar.gz`
    - `orchestra-preload` first-boot script
    - `config.txt` tuned for the target Raspberry Pi board (`rpi5-64` BCM2712/RP1/PCIe or `rpi3-64`)
    - `cmdline.txt`
    - `CONFIG/network/orchestra-ethernet.nmconnection` (+ optional Wi-Fi nmconnection)
    - `CONFIG/udev/99-orchestra-preload.rules`
    """
    boot_dir = build_dir / "boot_overlay"
    if boot_dir.exists():
        shutil.rmtree(boot_dir)
    boot_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copy preload archive and first-boot script
    shutil.copy2(preload_tar_path, boot_dir / "orchestra-preload.tar.gz")
    preload_script_src = (
        REPO_ROOT
        / "haos_image"
        / "buildroot-external-orchestra"
        / "rootfs-overlay"
        / "usr"
        / "sbin"
        / "orchestra-preload"
    )
    shutil.copy2(preload_script_src, boot_dir / "orchestra-preload")

    # 2. Board-specific `config.txt`
    if board == "rpi5-64":
        board_config_txt = """# Home Assistant OS (rpi5-64) + Zenith Orchestra Hardware Tuning
# Optimized for Raspberry Pi 5 (BCM2712 Cortex-A76 + RP1 I/O Controller)

arm_64bit=1
kernel=u-boot.bin

# Enable RP1 UART0, I2C, SPI, and PCIe 2.0 x1 (for NVMe SSD / RS485 Modbus HATs)
dtparam=uart0=on
dtparam=i2c_arm=on
dtparam=spi=on
dtparam=pciex1=on
"""
    else:
        board_config_txt = """# Home Assistant OS (rpi3-64) + Zenith Orchestra Hardware Tuning
arm_64bit=1
kernel=u-boot.bin
gpu_mem=16
enable_uart=1
dtoverlay=miniuart-bt
dtparam=i2c_arm=on
dtparam=spi=on
"""
    (boot_dir / "config.txt").write_text(board_config_txt, encoding="utf-8")

    # 3. `cmdline.txt`
    cmdline = "console=tty1 console=ttyAMA0,115200 root=/dev/mmcblk0p3 ro rootwait"
    (boot_dir / "cmdline.txt").write_text(cmdline + "\n", encoding="utf-8")

    # 4. HAOS `CONFIG` directory (imported automatically on boot by `hassos-config.service`)
    config_net_dir = boot_dir / "CONFIG" / "network"
    config_udev_dir = boot_dir / "CONFIG" / "udev"
    config_net_dir.mkdir(parents=True, exist_ok=True)
    config_udev_dir.mkdir(parents=True, exist_ok=True)

    eth_nm = f"""[connection]
id=Orchestra-LAN-Ethernet
uuid={uuid.uuid4()}
type=ethernet
interface-name=end0
autoconnect=true

[ipv4]
method=auto
mdns=2

[ipv6]
addr-gen-mode=stable-privacy
method=auto
"""
    (config_net_dir / "orchestra-ethernet.nmconnection").write_text(eth_nm, encoding="utf-8")

    if wifi_ssid and wifi_psk:
        wifi_nm = f"""[connection]
id=Orchestra-Home-WiFi
uuid={uuid.uuid4()}
type=wifi
interface-name=wlan0
autoconnect=true

[wifi]
mode=infrastructure
ssid={wifi_ssid}

[wifi-security]
auth-alg=open
key-mgmt=wpa-psk
psk={wifi_psk}

[ipv4]
method=auto
mdns=2

[ipv6]
method=auto
"""
        (config_net_dir / "orchestra-wifi.nmconnection").write_text(wifi_nm, encoding="utf-8")

    # Udev hook imported by `hassos-config.service` into `/etc/udev/rules.d/99-orchestra-preload.rules`
    udev_rule = (
        'ACTION=="add|change", SUBSYSTEM=="block", ENV{ID_FS_LABEL}=="hassos-data", '
        'RUN+="/bin/sh /mnt/boot/orchestra-preload"\n'
    )
    (config_udev_dir / "99-orchestra-preload.rules").write_text(udev_rule, encoding="utf-8")

    return boot_dir


def parse_gpt_partitions(img_path: Path) -> list[dict[str, Any]]:
    """Parse the GUID Partition Table (GPT) from a raw disk image file."""
    partitions: list[dict[str, Any]] = []
    with img_path.open("rb") as f:
        f.seek(512)
        header = f.read(92)
        if len(header) < 92 or header[:8] != b"EFI PART":
            return partitions
        part_entry_lba = struct.unpack("<Q", header[72:80])[0]
        num_part_entries = struct.unpack("<I", header[80:84])[0]
        size_part_entry = struct.unpack("<I", header[84:88])[0]

        f.seek(part_entry_lba * 512)
        for idx in range(num_part_entries):
            entry = f.read(size_part_entry)
            if len(entry) < 128:
                break
            type_guid = entry[:16]
            if type_guid == b"\x00" * 16:
                continue
            first_lba, last_lba = struct.unpack("<QQ", entry[32:48])
            name_utf16 = entry[56:128].decode("utf-16le", errors="ignore").rstrip("\x00")
            partitions.append(
                {
                    "index": idx + 1,
                    "name": name_utf16,
                    "first_lba": first_lba,
                    "last_lba": last_lba,
                    "offset_bytes": first_lba * 512,
                    "size_bytes": (last_lba - first_lba + 1) * 512,
                }
            )
    return partitions


def inject_boot_overlay_with_mtools(
    img_path: Path,
    partition_offset_bytes: int,
    boot_overlay_dir: Path,
    preserve_official_boot_configs: bool = False,
    board: str = "rpi5-64",
) -> None:
    """
    Use `mtools` (`mmd`, `mcopy`, `mtype`) to inject `orchestra-preload.tar.gz`,
    `orchestra-preload`, `config.txt` tuning, and `CONFIG/` directly into a FAT32
    partition at `partition_offset_bytes` without requiring root `mount` on macOS.
    """
    target_spec = f"{img_path}@@{partition_offset_bytes}"
    mmd = str(MTOOLS_DIR / "mmd")
    mcopy = str(MTOOLS_DIR / "mcopy")
    mtype = str(MTOOLS_DIR / "mtype")

    for subdir in ("::CONFIG", "::CONFIG/network", "::CONFIG/udev"):
        subprocess.run([mmd, "-D", "s", "-i", target_spec, subdir], check=False, capture_output=True)

    for fname in ("orchestra-preload.tar.gz", "orchestra-preload"):
        src = boot_overlay_dir / fname
        if src.exists():
            subprocess.run(
                [mcopy, "-o", "-i", target_spec, str(src), f"::{fname}"],
                check=True,
                capture_output=True,
            )

    if preserve_official_boot_configs:
        res = subprocess.run([mtype, "-i", target_spec, "::config.txt"], check=False, capture_output=True, text=True)
        if res.returncode == 0 and "Orchestra Hardware Tuning" not in res.stdout:
            if board == "rpi5-64":
                tuning_snippet = (
                    "\n\n# Zenith Orchestra Hardware Tuning (Raspberry Pi 5 BCM2712 / RP1 / PCIe)\n"
                    "dtparam=uart0=on\n"
                    "dtparam=i2c_arm=on\n"
                    "dtparam=spi=on\n"
                    "dtparam=pciex1=on\n"
                )
            else:
                tuning_snippet = (
                    "\n\n# Zenith Orchestra Hardware Tuning (RPi3 1GB RAM & RS485/UART)\n"
                    "gpu_mem=16\n"
                    "enable_uart=1\n"
                    "dtparam=i2c_arm=on\n"
                )
            merged_cfg = res.stdout.rstrip() + tuning_snippet
            merged_path = boot_overlay_dir / "config.merged.txt"
            merged_path.write_text(merged_cfg, encoding="utf-8")
            subprocess.run(
                [mcopy, "-o", "-i", target_spec, str(merged_path), "::config.txt"],
                check=True,
                capture_output=True,
            )
    else:
        for fname in ("config.txt", "cmdline.txt"):
            src = boot_overlay_dir / fname
            if src.exists():
                subprocess.run(
                    [mcopy, "-o", "-i", target_spec, str(src), f"::{fname}"],
                    check=True,
                    capture_output=True,
                )

    for net_file in (boot_overlay_dir / "CONFIG" / "network").glob("*"):
        subprocess.run(
            [mcopy, "-o", "-i", target_spec, str(net_file), f"::CONFIG/network/{net_file.name}"],
            check=True,
            capture_output=True,
        )
    for udev_file in (boot_overlay_dir / "CONFIG" / "udev").glob("*"):
        subprocess.run(
            [mcopy, "-o", "-i", target_spec, str(udev_file), f"::CONFIG/udev/{udev_file.name}"],
            check=True,
            capture_output=True,
        )


def _build_gpt_disk_with_fat32_boot(
    output_img_path: Path,
    boot_overlay_dir: Path,
    preload_tar_path: Path,
    total_size_mb: int = 64,
) -> dict[str, Any]:
    """Construct a spec-compliant GPT disk image with `hassos-boot`, `CONFIG`, and `hassos-data`."""
    sector_size = 512
    total_sectors = (total_size_mb * 1024 * 1024) // sector_size

    with output_img_path.open("wb") as f:
        f.seek(total_sectors * sector_size - 1)
        f.write(b"\x00")

    p1_first_lba = 2048
    p1_sectors = 65536
    p1_last_lba = p1_first_lba + p1_sectors - 1
    p1_offset = p1_first_lba * sector_size

    p2_first_lba = p1_last_lba + 1
    p2_sectors = 16384
    p2_last_lba = p2_first_lba + p2_sectors - 1
    p2_offset = p2_first_lba * sector_size

    p3_first_lba = p2_last_lba + 1
    p3_last_lba = total_sectors - 34
    p3_offset = p3_first_lba * sector_size

    _write_gpt_headers(
        output_img_path,
        total_sectors,
        [
            ("hassos-boot", p1_first_lba, p1_last_lba),
            ("CONFIG", p2_first_lba, p2_last_lba),
            ("hassos-data", p3_first_lba, p3_last_lba),
        ],
    )

    mformat = str(MTOOLS_DIR / "mformat")
    mcopy = str(MTOOLS_DIR / "mcopy")
    mmd = str(MTOOLS_DIR / "mmd")

    subprocess.run(
        [
            mformat,
            "-i",
            f"{output_img_path}@@{p1_offset}",
            "-F",
            "-v",
            "hassos-boot",
            "-T",
            str(p1_sectors),
            "-h",
            "64",
            "-s",
            "32",
            "::",
        ],
        check=True,
        capture_output=True,
    )
    inject_boot_overlay_with_mtools(output_img_path, p1_offset, boot_overlay_dir)

    subprocess.run(
        [
            mformat,
            "-i",
            f"{output_img_path}@@{p2_offset}",
            "-v",
            "CONFIG",
            "-T",
            str(p2_sectors),
            "-h",
            "64",
            "-s",
            "32",
            "::",
        ],
        check=True,
        capture_output=True,
    )
    p2_spec = f"{output_img_path}@@{p2_offset}"
    for sub in ("::network", "::udev"):
        subprocess.run([mmd, "-D", "s", "-i", p2_spec, sub], check=False, capture_output=True)
    for net_file in (boot_overlay_dir / "CONFIG" / "network").glob("*"):
        subprocess.run(
            [mcopy, "-o", "-i", p2_spec, str(net_file), f"::network/{net_file.name}"],
            check=True,
            capture_output=True,
        )
    for udev_file in (boot_overlay_dir / "CONFIG" / "udev").glob("*"):
        subprocess.run(
            [mcopy, "-o", "-i", p2_spec, str(udev_file), f"::udev/{udev_file.name}"],
            check=True,
            capture_output=True,
        )

    with output_img_path.open("r+b") as f:
        f.seek(p3_offset)
        f.write(preload_tar_path.read_bytes())

    return {
        "gpt_partitions": parse_gpt_partitions(output_img_path),
        "boot_partition_offset": p1_offset,
        "config_partition_offset": p2_offset,
        "data_partition_offset": p3_offset,
    }


def _write_gpt_headers(
    img_path: Path,
    total_sectors: int,
    part_specs: list[tuple[str, int, int]],
) -> None:
    """Write Protective MBR and CRC32-signed Primary & Backup GPT headers."""
    basic_data_guid = uuid.UUID("EBD0A0A2-B9E5-4433-87C0-68B6B72699C7").bytes_le
    disk_guid = uuid.uuid4().bytes_le

    entries_buf = bytearray(128 * 128)
    for idx, (name, first_lba, last_lba) in enumerate(part_specs):
        part_guid = uuid.uuid4().bytes_le
        name_bytes = name.encode("utf-16le")[:72].ljust(72, b"\x00")
        entry = struct.pack("<16s16sQQQ72s", basic_data_guid, part_guid, first_lba, last_lba, 0, name_bytes)
        entries_buf[idx * 128 : (idx + 1) * 128] = entry

    entries_crc = binascii.crc32(entries_buf) & 0xFFFFFFFF

    def make_header(my_lba: int, alt_lba: int, entries_lba: int) -> bytes:
        hdr = bytearray(92)
        struct.pack_into(
            "<8sIIIIQQQQ16sQIII",
            hdr,
            0,
            b"EFI PART",
            0x00010000,
            92,
            0,
            0,
            my_lba,
            alt_lba,
            34,
            total_sectors - 34,
            disk_guid,
            entries_lba,
            128,
            128,
            entries_crc,
        )
        hdr_crc = binascii.crc32(hdr) & 0xFFFFFFFF
        struct.pack_into("<I", hdr, 16, hdr_crc)
        return bytes(hdr).ljust(512, b"\x00")

    primary_hdr = make_header(1, total_sectors - 1, 2)
    backup_hdr = make_header(total_sectors - 1, 1, total_sectors - 33)

    mbr = bytearray(512)
    struct.pack_into(
        "<BBBBBBBBII",
        mbr,
        446,
        0x00,
        0x00,
        0x02,
        0x00,
        0xEE,
        0xFF,
        0xFF,
        0xFF,
        1,
        min(total_sectors - 1, 0xFFFFFFFF),
    )
    mbr[510:512] = b"\x55\xaa"

    with img_path.open("r+b") as f:
        f.seek(0)
        f.write(mbr)
        f.seek(512)
        f.write(primary_hdr)
        f.seek(1024)
        f.write(entries_buf)
        f.seek((total_sectors - 33) * 512)
        f.write(entries_buf)
        f.seek((total_sectors - 1) * 512)
        f.write(backup_hdr)


def patch_official_haos_image(
    base_xz_or_img: Path,
    output_img_xz: Path,
    boot_overlay_dir: Path,
    build_dir: Path,
    board: str = "rpi5-64",
) -> dict[str, Any]:
    """
    Decompress an official `haos_rpi5-64-*.img.xz` (or rpi3/rpi4), parse its GPT table
    to find `hassos-boot`, inject the Orchestra preload archive + `CONFIG` rules +
    board-specific `config.txt` tuning via `mtools`, and compress to `output_img_xz`.
    """
    work_img = build_dir / f"haos_{board}_working.img"
    if base_xz_or_img.suffix == ".xz":
        with work_img.open("wb") as out_f:
            subprocess.run([str(XZ_BIN), "-dc", str(base_xz_or_img)], stdout=out_f, check=True)
    else:
        shutil.copy2(base_xz_or_img, work_img)

    partitions = parse_gpt_partitions(work_img)
    boot_part = next((p for p in partitions if "boot" in p["name"].lower()), partitions[0])
    inject_boot_overlay_with_mtools(
        work_img,
        boot_part["offset_bytes"],
        boot_overlay_dir,
        preserve_official_boot_configs=True,
        board=board,
    )

    raw_dist_img = output_img_xz.with_suffix("")
    shutil.copy2(work_img, raw_dist_img)

    subprocess.run([str(XZ_BIN), "-z", "-f", "-T0", "-1", str(work_img)], check=True)
    shutil.move(str(work_img) + ".xz", output_img_xz)

    return {
        "patched_official_image_xz": str(output_img_xz),
        "patched_official_image_raw": str(raw_dist_img),
        "boot_partition_injected": boot_part,
        "total_gpt_partitions": len(partitions),
        "all_gpt_partitions": partitions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build flashable Raspberry Pi 5 (rpi5-64) HAOS image pre-loaded with Zenith Orchestra."
    )
    parser.add_argument(
        "--board",
        choices=["rpi5-64", "rpi4-64", "rpi3-64"],
        default="rpi5-64",
        help="Target Raspberry Pi architecture (default: rpi5-64)",
    )
    parser.add_argument(
        "--base-image",
        type=Path,
        default=None,
        help="Optional path to an official haos_rpi*-64-*.img.xz or .img to patch",
    )
    parser.add_argument(
        "--download-official",
        action="store_true",
        help="Download the official HAOS release for --board and patch it",
    )
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="Only build dist/orchestra-preload.tar.gz without building .img.xz",
    )
    parser.add_argument("--wifi-ssid", type=str, default=None, help="Optional pre-configured Home Wi-Fi SSID")
    parser.add_argument("--wifi-psk", type=str, default=None, help="Optional pre-configured Home Wi-Fi Password")
    args = parser.parse_args(argv)

    build_dir = REPO_ROOT / "build"
    dist_dir = REPO_ROOT / "dist"
    build_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Stage and pack the pre-hijacked Home Assistant + Grandpa Agent bundle
    stage_info = stage_orchestra_supervisor_payload(build_dir, dist_dir)
    preload_tar = Path(stage_info["preload_tar_path"])

    if args.stage_only:
        print(json.dumps(stage_info, indent=2))
        return 0

    # Step 2: Prepare the `hassos-boot` & `CONFIG` overlay directory
    boot_overlay_dir = stage_boot_and_config_overlay(
        build_dir,
        preload_tar,
        board=args.board,
        wifi_ssid=args.wifi_ssid,
        wifi_psk=args.wifi_psk,
    )

    base_image_path = args.base_image
    release_url = BOARD_RELEASE_URLS[args.board]
    if args.download_official and base_image_path is None:
        dl_target = build_dir / f"haos_{args.board}-{OFFICIAL_HAOS_VERSION}.img.xz"
        if not dl_target.exists():
            print(f"[HAOS Builder] Downloading {release_url}...")
            urllib.request.urlretrieve(release_url, dl_target)
        base_image_path = dl_target

    output_xz = dist_dir / f"haos_{args.board}_orchestra_preloaded.img.xz"
    output_raw = dist_dir / f"haos_{args.board}_orchestra_preloaded.img"

    if base_image_path is not None and base_image_path.exists():
        img_info = patch_official_haos_image(
            base_image_path,
            output_xz,
            boot_overlay_dir,
            build_dir,
            board=args.board,
        )
    else:
        img_info = _build_gpt_disk_with_fat32_boot(
            output_raw,
            boot_overlay_dir,
            preload_tar,
            total_size_mb=64,
        )
        subprocess.run([str(XZ_BIN), "-z", "-f", "-k", "-1", str(output_raw)], check=True)

    sha256_hex = hashlib.sha256(output_xz.read_bytes()).hexdigest()
    sha256_path = dist_dir / f"haos_{args.board}_orchestra_preloaded.img.xz.sha256"
    sha256_path.write_text(f"{sha256_hex}  {output_xz.name}\n", encoding="utf-8")

    summary = {
        "board_target": f"{args.board} (Raspberry Pi 5 64-bit)" if args.board == "rpi5-64" else args.board,
        "flashable_image_xz": str(output_xz),
        "flashable_image_raw": str(output_raw),
        "flashable_image_size_bytes": output_xz.stat().st_size,
        "sha256": sha256_hex,
        "preload_archive": str(preload_tar),
        "staging_summary": stage_info,
        "image_details": img_info,
    }
    print("=" * 88)
    print(f" ZENITH ORCHESTRA: RASPBERRY PI ({args.board}) HAOS IMAGE BUILD COMPLETE")
    print("=" * 88)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
