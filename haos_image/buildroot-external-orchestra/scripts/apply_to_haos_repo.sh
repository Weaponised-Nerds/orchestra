#!/usr/bin/env bash
# ==============================================================================
# Applies Zenith Orchestra modifications to a cloned
# https://github.com/home-assistant/operating-system repository and builds the
# rpi3_64 (Raspberry Pi 3 64-bit) HAOS image via Buildroot.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
HAOS_SRC_DIR="${1:-${REPO_ROOT}/build/operating-system}"
BOARD_TARGET="${2:-rpi3_64}"

echo "[Orchestra HAOS Builder] Preparing pre-hijacked supervisor bundle..."
/opt/homebrew/bin/python3 "${REPO_ROOT}/haos_image/build_rpi_image.py" --stage-only

if [ ! -d "${HAOS_SRC_DIR}" ]; then
    echo "[Orchestra HAOS Builder] Cloning https://github.com/home-assistant/operating-system..."
    git clone --depth 1 --branch rel-13 https://github.com/home-assistant/operating-system.git "${HAOS_SRC_DIR}"
fi

OVERLAY_TARGET="${HAOS_SRC_DIR}/buildroot-external/rootfs-overlay"
echo "[Orchestra HAOS Builder] Injecting Orchestra rootfs-overlay into ${OVERLAY_TARGET}..."

cp -rf "${SCRIPT_DIR}/../rootfs-overlay/"* "${OVERLAY_TARGET}/"
chmod +x "${OVERLAY_TARGET}/usr/sbin/orchestra-preload"

mkdir -p "${OVERLAY_TARGET}/etc/systemd/system/multi-user.target.wants"
ln -sf "/usr/lib/systemd/system/orchestra-preload.service" \
    "${OVERLAY_TARGET}/etc/systemd/system/multi-user.target.wants/orchestra-preload.service"

mkdir -p "${OVERLAY_TARGET}/usr/share/orchestra"
cp -f "${REPO_ROOT}/dist/orchestra-preload.tar.gz" "${OVERLAY_TARGET}/usr/share/orchestra/orchestra-preload.tar.gz"

echo "[Orchestra HAOS Builder] Overlay applied. Launching Buildroot for ${BOARD_TARGET}..."
cd "${HAOS_SRC_DIR}"
./scripts/enter.sh make "${BOARD_TARGET}"
