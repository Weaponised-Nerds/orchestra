"""
Home Assistant OS (HAOS) Raspberry Pi 5 (`rpi5-64`) & Pi 3/4 Image Builder & Repacker.

Builds a flashable Raspberry Pi 5 image (`.img.xz` & `.img`) pre-loaded with:
1. Home Assistant OS (`rpi5-64` architecture for BCM2712 + RP1 Southbridge, with PCIe NVMe,
   RP1 UART0, and I2C enabled).
2. Onboarding already finished: the Orchestra-Agent owner account and its long-lived token,
   Perth location, AUD and metric units (`.storage/onboarding`, `auth`, `core.config`).
3. `configuration.yaml` with `default_config:`, so Home Assistant's own discovery runs.
4. The Orchestra integration and HACS in `custom_components/`.
5. A setup plan (`.storage/orchestra.setup_plan`) holding the human logins. The Orchestra
   setup agent reads it on first boot, finishes setup after discovery, creates the logins,
   rotates the agent's password and leaves a punch list for what needs a person.
"""

from __future__ import annotations

import argparse
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
import secrets
import uuid
import zipfile

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestra.config import OrchestraConfig
from orchestra.hijacker.bootstrap import HAProcessHijacker

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

HACS_RELEASE_URL = "https://github.com/hacs/integration/releases/{path}/hacs.zip"
SETUP_PLAN_KEY = "orchestra.setup_plan"

# Homebrew paths on the Mac, else whatever is on PATH (Linux, CI)
MTOOLS_DIR = Path("/opt/homebrew/bin")
if not (MTOOLS_DIR / "mformat").exists() and shutil.which("mformat"):
    MTOOLS_DIR = Path(shutil.which("mformat")).parent  # type: ignore[arg-type]
XZ_BIN = Path("/opt/homebrew/bin/xz")
if not XZ_BIN.exists() and shutil.which("xz"):
    XZ_BIN = Path(shutil.which("xz"))  # type: ignore[arg-type]
E2FSPROGS_DIR = Path("/opt/homebrew/opt/e2fsprogs/sbin")


def _e2fs_tool(name: str) -> Path:
    """An e2fsprogs tool (debugfs, e2fsck, resize2fs) from Homebrew on the Mac, else from PATH."""
    if (E2FSPROGS_DIR / name).exists():
        return E2FSPROGS_DIR / name
    for found in (shutil.which(name), shutil.which(name, path="/usr/sbin:/sbin")):
        if found:
            return Path(found)
    raise RuntimeError(f"{name} (from e2fsprogs) is required to build the image. Install with: brew install e2fsprogs")


DEBUGFS_BIN = E2FSPROGS_DIR / "debugfs"
if not DEBUGFS_BIN.exists():
    sh_debugfs = shutil.which("debugfs")
    if sh_debugfs:
        DEBUGFS_BIN = Path(sh_debugfs)

# The official images ship `hassos-data` with almost no free space (HAOS grows it to fill the
# card on first boot), so it is grown by this much before the payload is copied in
DATA_PARTITION_GROWTH_BYTES = 512 * 1024 * 1024


def parse_user_spec(spec: str) -> dict[str, Any]:
    """`"Full Name:username[:password]"` → a setup plan user. A missing password is generated."""
    parts = spec.split(":", 2)
    if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
        raise ValueError(f"--user needs 'Full Name:username[:password]', got {spec!r}")
    password = parts[2] if len(parts) == 3 and parts[2] else secrets.token_urlsafe(12)
    return {"name": parts[0].strip(), "username": parts[1].strip().lower(), "password": password, "admin": True}


def stage_hacs(target_dir: Path, build_dir: Path, hacs_zip: Path | None = None, version: str = "latest") -> str:
    """Unpack HACS into `custom_components/hacs`, downloading its release zip unless one is given."""
    if hacs_zip is None:
        hacs_zip = build_dir / f"hacs-{version}.zip"
        if not hacs_zip.exists():
            path = "latest/download" if version == "latest" else f"download/{version}"
            url = HACS_RELEASE_URL.format(path=path)
            print(f"[HAOS Builder] Downloading HACS from {url}...")
            urllib.request.urlretrieve(url, hacs_zip)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)
    with zipfile.ZipFile(hacs_zip) as zf:
        for member in zf.namelist():
            if member.startswith("/") or ".." in Path(member).parts:
                raise ValueError(f"Unsafe path in HACS zip: {member}")
        zf.extractall(target_dir)
    manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("domain") != "hacs":
        raise ValueError(f"{hacs_zip} is not a HACS release")
    return str(manifest.get("version", version))


def stage_orchestra_supervisor_payload(
    build_dir: Path,
    dist_dir: Path,
    users: list[dict[str, Any]] | None = None,
    include_hacs: bool = True,
    hacs_zip: Path | None = None,
    hacs_version: str = "latest",
) -> dict[str, Any]:
    """
    Stage the `/mnt/data/supervisor` tree and pack it as `dist/orchestra-preload.tar.gz`.

    Onboarding is finished for the Orchestra-Agent account, but nothing about the home is
    pre-filled: Home Assistant discovers the equipment itself on first boot and the setup
    agent in the Orchestra integration finishes from there.
    """
    staging_supervisor = build_dir / "staging_supervisor"
    if staging_supervisor.exists():
        shutil.rmtree(staging_supervisor)

    ha_config_dir = staging_supervisor / "homeassistant"
    ha_config_dir.mkdir(parents=True, exist_ok=True)

    # 1. Onboarding done, agent owner + long-lived token, Perth/AUD/metric, configuration.yaml
    HAProcessHijacker(OrchestraConfig(ha_config_dir=ha_config_dir)).bypass_onboarding()

    # 2. The Orchestra integration and HACS
    custom_dir = ha_config_dir / "custom_components"
    shutil.copytree(
        REPO_ROOT / "custom_components" / "orchestra",
        custom_dir / "orchestra",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    hacs_installed = stage_hacs(custom_dir / "hacs", build_dir, hacs_zip, hacs_version) if include_hacs else None

    # 3. The setup plan the agent works from (Home Assistant storage format, private to root)
    plan_users = users or []
    plan = {
        "version": 1,
        "minor_version": 1,
        "key": SETUP_PLAN_KEY,
        "data": {"created_by": "orchestra image builder", "users": plan_users},
    }
    plan_path = ha_config_dir / ".storage" / SETUP_PLAN_KEY
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    plan_path.chmod(0o600)

    # 4. Handover files for the installer: the agent token and the human logins
    dist_dir.mkdir(parents=True, exist_ok=True)
    token_src = ha_config_dir / "orchestra_agent_token.txt"
    if token_src.exists():
        shutil.copy2(token_src, dist_dir / "orchestra_agent_token.txt")
    handover = dist_dir / "orchestra_handover.txt"
    lines = ["Orchestra logins (hand these to the homeowner, then delete this file)", ""]
    lines += [f"{u['name']}: username {u['username']}, password {u['password']}" for u in plan_users]
    if plan_users:
        lines += [
            "",
            "These work about a minute after Home Assistant's first start (the first boot downloads",
            "Home Assistant itself, which can take 10-20 minutes before the login page appears).",
            "",
            "Orchestra-Agent: username orchestra-agent, password OrchestraAgent2026!",
            "This login and orchestra_agent_token.txt work until the setup agent finishes (up to 15 minutes",
            "after Home Assistant starts). It then sets a random password and writes a new token to",
            "/config/orchestra_agent_token.txt on the Pi; the token from this build stops working.",
        ]
    else:
        lines += [
            "Generic image: no homeowner logins are baked in.",
            "",
            "Sign in with username orchestra-agent, password OrchestraAgent2026! (the first boot",
            "downloads Home Assistant, which can take 10-20 minutes before the login page appears).",
            "The Setup drawer then asks the homeowner to 'Make this home yours': they create their",
            "own login, and only then does the agent get a random password and a new token",
            "(/config/orchestra_agent_token.txt on the Pi). Until the box is claimed, this shared",
            "login and orchestra_agent_token.txt keep working, so claim it straight away.",
        ]
    handover.write_text("\n".join(lines) + "\n", encoding="utf-8")
    handover.chmod(0o600)

    # 5. Create `dist/orchestra-preload.tar.gz`
    preload_tar_path = dist_dir / "orchestra-preload.tar.gz"
    with tarfile.open(preload_tar_path, "w:gz") as tar:
        for child in staging_supervisor.iterdir():
            tar.add(child, arcname=child.name)

    return {
        "staging_supervisor": str(staging_supervisor),
        "preload_tar_path": str(preload_tar_path),
        "preload_tar_size_bytes": preload_tar_path.stat().st_size,
        "hacs_version": hacs_installed,
        "users": [u["username"] for u in plan_users],
        "token_path": str(dist_dir / "orchestra_agent_token.txt"),
        "handover_path": str(handover),
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
                    "\n\n# Zenith Orchestra Hardware Tuning (Raspberry Pi 5 BCM2712 / RP1)\n"
                    "dtparam=i2c_arm=on\n"
                    "dtparam=spi=on\n"
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


def grow_last_gpt_partition(img_path: Path, extra_bytes: int) -> dict[str, Any]:
    """
    Make the image `extra_bytes` larger and give the space to its last partition (`hassos-data`).

    Rewrites the protective MBR and both GPT headers and entry arrays, keeping every GUID, so
    HAOS still finds its partitions and still grows `hassos-data` to fill the card on first boot.
    """
    extra_sectors = (extra_bytes // (1024 * 1024)) * 2048  # whole MiB, so the partition stays aligned
    with img_path.open("r+b") as f:
        f.seek(512)
        header = bytearray(f.read(92))
        if header[:8] != b"EFI PART":
            raise RuntimeError(f"{img_path} has no GPT")
        last_usable = struct.unpack_from("<Q", header, 48)[0]
        entries_lba, num_entries, entry_size = struct.unpack_from("<QII", header, 72)
        entries_sectors = (num_entries * entry_size + 511) // 512
        f.seek(entries_lba * 512)
        entries = bytearray(f.read(num_entries * entry_size))

        used = [
            (struct.unpack_from("<Q", entries, i * entry_size + 40)[0], i)
            for i in range(num_entries)
            if entries[i * entry_size : i * entry_size + 16] != b"\x00" * 16
        ]
        last_lba, last_idx = max(used)
        old_total = img_path.stat().st_size // 512
        new_total = old_total + extra_sectors
        new_last_lba = last_lba + extra_sectors
        struct.pack_into("<Q", entries, last_idx * entry_size + 40, new_last_lba)
        entries_crc = binascii.crc32(entries) & 0xFFFFFFFF

        def make_header(my_lba: int, alt_lba: int, my_entries_lba: int) -> bytes:
            hdr = bytearray(header)
            struct.pack_into("<QQ", hdr, 24, my_lba, alt_lba)
            struct.pack_into("<Q", hdr, 48, last_usable + extra_sectors)
            struct.pack_into("<Q", hdr, 72, my_entries_lba)
            struct.pack_into("<I", hdr, 88, entries_crc)
            struct.pack_into("<I", hdr, 16, 0)
            struct.pack_into("<I", hdr, 16, binascii.crc32(hdr) & 0xFFFFFFFF)
            return bytes(hdr).ljust(512, b"\x00")

        f.seek((old_total - 1) * 512)  # the old backup header now sits inside hassos-data
        f.write(b"\x00" * 512)
        f.truncate(new_total * 512)

        f.seek(450)  # protective MBR: partition type 0xEE should cover the whole disk
        if f.read(1) == b"\xee":
            f.seek(458)
            f.write(struct.pack("<I", min(new_total - 1, 0xFFFFFFFF)))
        f.seek(512)
        f.write(make_header(1, new_total - 1, entries_lba))
        f.seek(entries_lba * 512)
        f.write(entries)
        backup_entries_lba = new_total - 1 - entries_sectors
        f.seek(backup_entries_lba * 512)
        f.write(entries)
        f.seek((new_total - 1) * 512)
        f.write(make_header(new_total - 1, 1, backup_entries_lba))

    return {"partition_index": last_idx + 1, "old_last_lba": last_lba, "new_last_lba": new_last_lba}


def _verify_injected_tree(debugfs_bin: Path, p8_img: Path, staging_supervisor_dir: Path, build_dir: Path) -> None:
    """Read the payload back out of the partition and fail the build if any file differs."""
    check_dir = build_dir / "inject_check"
    if check_dir.exists():
        shutil.rmtree(check_dir)
    check_dir.mkdir()
    subprocess.run(
        [str(debugfs_bin), "-R", f"rdump /supervisor {check_dir}", str(p8_img)],
        capture_output=True,
        check=True,
    )
    bad = [
        str(p.relative_to(staging_supervisor_dir))
        for p in staging_supervisor_dir.rglob("*")
        if p.is_file()
        and (
            not (check_dir / "supervisor" / p.relative_to(staging_supervisor_dir)).is_file()
            or (check_dir / "supervisor" / p.relative_to(staging_supervisor_dir)).read_bytes() != p.read_bytes()
        )
    ]
    shutil.rmtree(check_dir)
    if bad:
        raise RuntimeError(f"{len(bad)} files did not copy into hassos-data intact, e.g. {bad[:5]}")


def inject_supervisor_payload_into_ext4_partition(
    work_img: Path,
    data_partition_offset_bytes: int,
    data_partition_size_bytes: int,
    staging_supervisor_dir: Path,
    build_dir: Path,
) -> dict[str, Any]:
    """
    Directly inject `/supervisor` directory tree (including `homeassistant/.storage`,
    `homeassistant/custom_components/orchestra` and `hacs`)
    into the `hassos-data` ext4 partition using `debugfs` without requiring root or mounting.
    """
    debugfs_bin = DEBUGFS_BIN if DEBUGFS_BIN.exists() else Path(shutil.which("debugfs") or "debugfs")
    if not debugfs_bin.exists() and not shutil.which(str(debugfs_bin)):
        raise RuntimeError(
            "debugfs (from e2fsprogs) is required to inject into ext4 hassos-data partition. "
            "Install with: brew install e2fsprogs"
        )

    p8_img = build_dir / "p8_hassos_data_working.img"
    # 1. Slice out the data partition
    with work_img.open("rb") as f_src, p8_img.open("wb") as f_dst:
        f_src.seek(data_partition_offset_bytes)
        remaining = data_partition_size_bytes
        while remaining > 0:
            chunk = f_src.read(min(remaining, 64 * 1024 * 1024))
            if not chunk:
                break
            f_dst.write(chunk)
            remaining -= len(chunk)

    # Grow the ext4 filesystem into the space `grow_last_gpt_partition` gave the partition
    fsck = subprocess.run([str(_e2fs_tool("e2fsck")), "-fy", str(p8_img)], capture_output=True, text=True)
    if fsck.returncode > 1:  # 1 means it fixed something, which is fine
        raise RuntimeError(f"e2fsck failed with exit code {fsck.returncode}: {fsck.stdout}{fsck.stderr}")
    subprocess.run([str(_e2fs_tool("resize2fs")), str(p8_img)], capture_output=True, text=True, check=True)

    # 2. Collect all directories and files relative to staging_supervisor_dir
    commands: list[str] = []
    all_dirs: list[str] = []
    all_files: list[tuple[Path, str]] = []

    for p in staging_supervisor_dir.rglob("*"):
        rel = p.relative_to(staging_supervisor_dir)
        target = "/supervisor/" + str(rel).replace("\\", "/")
        if p.is_dir():
            all_dirs.append(target)
        elif p.is_file():
            all_files.append((p.resolve(), target))

    all_dirs.sort(key=lambda d: len(d.split("/")))

    # A `mkdir` of a directory that already exists (the stock image has `/supervisor`) leaves
    # debugfs handing the next directory a block that is already in use, so skip those
    for d in ["/supervisor"] + all_dirs:
        exists = subprocess.run([str(debugfs_bin), "-R", f"stat {d}", str(p8_img)], capture_output=True, text=True)
        if "Inode:" not in exists.stdout:
            commands.append(f"mkdir {d}")

    for src_file, target_path in all_files:
        commands.append(f"write {src_file} {target_path}")

    # `write` keeps the builder's own uid/gid (501 on a Mac); Home Assistant's files belong to root
    for target_path in all_dirs + [t for _, t in all_files]:
        commands.append(f"sif {target_path} uid 0")
        commands.append(f"sif {target_path} gid 0")

    script_path = build_dir / "inject_supervisor.debugfs"
    script_path.write_text("\n".join(commands) + "\n", encoding="utf-8")

    # 3. Execute debugfs in batch mode
    res = subprocess.run(
        [str(debugfs_bin), "-w", "-f", str(script_path), str(p8_img)],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"debugfs failed with exit code {res.returncode}: {res.stderr}")
    # debugfs exits 0 even when a write runs out of space, so read everything back to be sure
    _verify_injected_tree(debugfs_bin, p8_img, staging_supervisor_dir, build_dir)
    check = subprocess.run([str(_e2fs_tool("e2fsck")), "-fn", str(p8_img)], capture_output=True, text=True)
    if check.returncode != 0:
        raise RuntimeError(f"hassos-data has filesystem errors after the copy: {check.stdout[-2000:]}")

    # 4. Write modified partition back into work_img
    with p8_img.open("rb") as f_src, work_img.open("r+b") as f_dst:
        f_dst.seek(data_partition_offset_bytes)
        remaining = data_partition_size_bytes
        while remaining > 0:
            chunk = f_src.read(min(remaining, 64 * 1024 * 1024))
            if not chunk:
                break
            f_dst.write(chunk)
            remaining -= len(chunk)

    if p8_img.exists():
        p8_img.unlink()

    return {
        "status": "injected",
        "total_commands": len(commands),
        "directories_created": len(all_dirs) + 1,
        "files_written": len(all_files),
    }


def patch_official_haos_image(
    base_xz_or_img: Path,
    output_img_xz: Path,
    boot_overlay_dir: Path,
    build_dir: Path,
    board: str = "rpi5-64",
    staging_supervisor_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Decompress an official `haos_rpi5-64-*.img.xz` (or rpi3/rpi4), parse its GPT table
    to find `hassos-boot`, inject the Orchestra preload archive + `CONFIG` rules +
    board-specific `config.txt` tuning via `mtools`, inject the supervisor payload
    directly into `hassos-data` ext4 partition via `debugfs`, and compress to `output_img_xz`.
    """
    work_img = build_dir / f"haos_{board}_working.img"
    if base_xz_or_img.suffix == ".xz":
        with work_img.open("wb") as out_f:
            subprocess.run([str(XZ_BIN), "-dc", str(base_xz_or_img)], stdout=out_f, check=True)
    else:
        shutil.copy2(base_xz_or_img, work_img)

    partitions = parse_gpt_partitions(work_img)
    data_part = next((p for p in partitions if "data" in p["name"].lower()), None)
    grown = None
    if data_part and staging_supervisor_dir and staging_supervisor_dir.exists():
        if data_part["last_lba"] != max(p["last_lba"] for p in partitions):
            raise RuntimeError("hassos-data is not the last partition, so it cannot be grown")
        grown = grow_last_gpt_partition(work_img, DATA_PARTITION_GROWTH_BYTES)
        partitions = parse_gpt_partitions(work_img)
    boot_part = next((p for p in partitions if "boot" in p["name"].lower()), partitions[0])
    inject_boot_overlay_with_mtools(
        work_img,
        boot_part["offset_bytes"],
        boot_overlay_dir,
        preserve_official_boot_configs=True,
        board=board,
    )

    data_part = next((p for p in partitions if "data" in p["name"].lower()), None)
    ext4_result = None
    if data_part and staging_supervisor_dir and staging_supervisor_dir.exists():
        ext4_result = inject_supervisor_payload_into_ext4_partition(
            work_img=work_img,
            data_partition_offset_bytes=data_part["offset_bytes"],
            data_partition_size_bytes=data_part["size_bytes"],
            staging_supervisor_dir=staging_supervisor_dir,
            build_dir=build_dir,
        )

    raw_dist_img = output_img_xz.with_suffix("")
    shutil.copy2(work_img, raw_dist_img)

    subprocess.run([str(XZ_BIN), "-z", "-f", "-T0", "-1", str(work_img)], check=True)
    shutil.move(str(work_img) + ".xz", output_img_xz)

    return {
        "patched_official_image_xz": str(output_img_xz),
        "patched_official_image_raw": str(raw_dist_img),
        "boot_partition_injected": boot_part,
        "data_partition_injected": ext4_result,
        "data_partition_grown": grown,
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
        "--user",
        action="append",
        default=[],
        metavar="'Full Name:username[:password]'",
        help="A human login the setup agent creates on first boot (repeatable). "
        "Passwords left out are generated and written to dist/orchestra_handover.txt.",
    )
    parser.add_argument("--no-hacs", action="store_true", help="Leave HACS out of the image")
    parser.add_argument("--hacs-zip", type=Path, default=None, help="Use this HACS release zip instead of downloading")
    parser.add_argument("--hacs-version", default="latest", help="HACS release to download (default: latest)")
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

    # Step 1: Stage and pack the Home Assistant config, Orchestra, HACS and the setup plan
    stage_info = stage_orchestra_supervisor_payload(
        build_dir,
        dist_dir,
        users=[parse_user_spec(spec) for spec in args.user],
        include_hacs=not args.no_hacs,
        hacs_zip=args.hacs_zip,
        hacs_version=args.hacs_version,
    )
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
            staging_supervisor_dir=Path(stage_info["staging_supervisor"]),
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
