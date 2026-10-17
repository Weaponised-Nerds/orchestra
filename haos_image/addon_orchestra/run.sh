#!/usr/bin/env sh
set -e

echo "[Orchestra Add-on] Starting Zenith Orchestra Cloud-Liberation & Grandpa Agent Daemon..."

# 1. Ensure in-process custom_components/orchestra and Python package are synced into /config
mkdir -p /config/custom_components
cp -rf /usr/src/orchestra/custom_components/orchestra /config/custom_components/
cp -rf /usr/src/orchestra/orchestra /config/

# 2. Run live iStore 180L/270L Heat Pump UDP 48899 / Modbus 8899 / Tuya 6668 LAN probe
echo "[Orchestra Add-on] Probing LAN for iStore Heat Pump Wi-Fi & Modbus module..."
python3 -m orchestra.cli probe-istore || true

# 3. Execute the 6-stage Grandpa-Simple Autonomous Setup if not already initialized
if [ ! -f /config/.storage/conductor.entity_bindings ]; then
    echo "[Orchestra Add-on] Running 6-Stage Grandpa-Simple Autonomous Setup on /config..."
    python3 -m orchestra.cli grandpa-setup --config-dir /config
fi

echo "[Orchestra Add-on] Commissioning complete. Entering continuous local discovery & OCPP/Conductor loop..."
while true; do
    sleep 300
    python3 -m orchestra.cli probe-istore >/dev/null 2>&1 || true
done
