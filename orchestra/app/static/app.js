// Orchestra Mobile App Client Runtime

let currentSetpoint = 24.0;
let currentFlowId = "simulated_apple_tv_flow";

// Modern Web Guidance: Light-dismiss fallback for browsers without native <dialog closedby> support
const pinModal = document.getElementById("pinModal");
if (pinModal && !("closedBy" in HTMLDialogElement.prototype)) {
  pinModal.addEventListener("click", (event) => {
    if (event.target !== pinModal) return;
    const rect = pinModal.getBoundingClientRect();
    const isInsideContent = (
      rect.top <= event.clientY &&
      event.clientY <= rect.top + rect.height &&
      rect.left <= event.clientX &&
      event.clientX <= rect.left + rect.width
    );
    if (!isInsideContent) {
      pinModal.close();
    }
  });
}

function openPinModal(flowTitle = "Living Room Apple TV", flowId = "simulated_apple_tv_flow") {
  currentFlowId = flowId;
  const descEl = document.getElementById("pinModalDesc");
  if (descEl) {
    descEl.innerText = `We found the Apple TV in your Living Room (${flowTitle}). Please enter the 4-digit AirPlay PIN showing on your TV screen:`;
  }
  document.getElementById("pin1").value = "";
  document.getElementById("pin2").value = "";
  document.getElementById("pin3").value = "";
  document.getElementById("pin4").value = "";
  if (pinModal) {
    pinModal.showModal();
    setTimeout(() => document.getElementById("pin1").focus(), 150);
  }
}

function closePinModal() {
  if (pinModal) pinModal.close();
}

function openSimulatedAirPlayModal() {
  openPinModal("Living Room Apple TV 4K", "demo_flow_123");
}

function movePinFocus(currentIdx, event) {
  const currentInput = document.getElementById(`pin${currentIdx}`);
  if (event.key === "Backspace" && currentIdx > 1 && !currentInput.value) {
    document.getElementById(`pin${currentIdx - 1}`).focus();
    return;
  }
  if (currentInput.value.length >= 1 && currentIdx < 4) {
    document.getElementById(`pin${currentIdx + 1}`).focus();
  }
}

async function handlePinSubmit(event) {
  event.preventDefault();
  const p1 = document.getElementById("pin1").value;
  const p2 = document.getElementById("pin2").value;
  const p3 = document.getElementById("pin3").value;
  const p4 = document.getElementById("pin4").value;
  const pin = `${p1}${p2}${p3}${p4}`;

  if (pin.length !== 4) {
    alert("Please enter the complete 4-digit PIN.");
    return;
  }

  const btn = document.getElementById("btnPairPin");
  btn.innerText = "Pairing...";
  btn.disabled = true;

  try {
    const res = await fetch("/api/flows/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ flow_id: currentFlowId, pin }),
    });
    const data = await res.json();
    closePinModal();
    appendChatMessage("concierge", `✓ Successfully paired Apple TV with PIN ${pin}! Added to Living & Bedrooms.`);
  } catch (err) {
    closePinModal();
    appendChatMessage("concierge", `Paired Apple TV with PIN ${pin} (Offline Demo Mode). Added to Living & Bedrooms.`);
  } finally {
    btn.innerText = "Pair Apple TV";
    btn.disabled = false;
  }
}

function adjustTemp(delta) {
  currentSetpoint = Math.max(18.0, Math.min(30.0, currentSetpoint + delta));
  document.getElementById("tempDisplay").innerText = `${currentSetpoint.toFixed(1)}°`;
}

function toggleDevice(deviceKey, isChecked) {
  const stateStr = isChecked ? "ON" : "OFF";
  appendChatMessage("concierge", `${deviceKey.toUpperCase()} switched ${stateStr}.`);
}

function triggerGarage() {
  appendChatMessage("concierge", "Garage roller door toggle command sent. Door is operating.");
}

function boostHotWater() {
  sendQuickPrompt("Boost hot water for a shower");
}

function enableGuestMode() {
  sendQuickPrompt("I have guests coming over");
}

function enableNightMode() {
  sendQuickPrompt("Turn off everything for the night");
}

function sendQuickPrompt(promptText) {
  document.getElementById("chatInput").value = promptText;
  sendChatMessage();
}

async function sendChatMessage() {
  const inputEl = document.getElementById("chatInput");
  const text = inputEl.value.trim();
  if (!text) return;

  appendChatMessage("user", text);
  inputEl.value = "";

  try {
    const res = await fetch("/api/concierge/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    appendChatMessage("concierge", data.reply);
  } catch (err) {
    // Client-side fallback if server offline
    let reply = "Everything is running smoothly and efficiently in your home.";
    const lower = text.toLowerCase();
    if (lower.includes("hot water") || lower.includes("shower")) {
      reply = "Right away! I've engaged solar boost mode on your hot water heat pump for the next hour.";
    } else if (lower.includes("guest") || lower.includes("visitor")) {
      reply = "Sounds great! Pre-cooled living areas to 22°C with free solar power and boosted hot water.";
    } else if (lower.includes("night") || lower.includes("sleep")) {
      reply = "Goodnight! Turned off lights, locked the garage, and set AC to quiet mode.";
    } else if (lower.includes("save") || lower.includes("money") || lower.includes("solar")) {
      reply = "Your solar roof has generated 24.5 kWh today, saving you $6.45 on power!";
    }
    appendChatMessage("concierge", reply);
  }
}

function appendChatMessage(sender, text) {
  const container = document.getElementById("chatMessages");
  const bubble = document.createElement("div");
  bubble.className = `chat-bubble ${sender}`;
  bubble.innerText = text;
  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
}

// Polling loop for live status & pending interactive flows
async function refreshStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) return;
    const data = await res.json();

    if (data.energy) {
      document.getElementById("solarVal").innerHTML = `${data.energy.solar_kw.toFixed(1)} <span style="font-size:14px">kW</span>`;
      document.getElementById("batteryVal").innerText = `${data.energy.battery_soc_pct.toFixed(0)}%`;
      document.getElementById("homeVal").innerHTML = `${data.energy.home_load_kw.toFixed(1)} <span style="font-size:14px">kW</span>`;
      document.getElementById("gridVal").innerHTML = `${data.energy.grid_kw.toFixed(1)} <span style="font-size:14px">kW</span>`;
      document.getElementById("savingsBadge").innerText = `+$${data.energy.money_saved_today_aud.toFixed(2)} Saved Today`;
      document.getElementById("tariffText").innerText = `${data.energy.grid_tariff_mode}`;
    }

    if (data.pending_prompt && data.pending_prompt.type === "apple_tv_pin") {
      openPinModal(data.pending_prompt.title, data.pending_prompt.flow_id);
    }
  } catch (err) {
    // Graceful offline fallback
  }
}

// Initial welcome message
document.addEventListener("DOMContentLoaded", () => {
  appendChatMessage(
    "concierge",
    "G'day! I'm your Orchestra Home Concierge. I take care of your solar, battery, heating, and devices automatically so your power bills stay low and your home stays comfortable."
  );
  refreshStatus();
  setInterval(refreshStatus, 8000);
});
