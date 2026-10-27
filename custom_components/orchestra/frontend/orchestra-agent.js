// Orchestra setup assistant for the Home Assistant frontend (also inside the Companion app).
//
// Loaded on every page by the Orchestra integration. It adds a "Setup" tab on the right edge
// that opens a chat drawer, and defines <orchestra-agent-panel> for the full-page sidebar
// panel. Both talk to the agent over Home Assistant's own websocket (orchestra/agent/*), so
// the chat runs as the signed-in person and needs no other login. Admins only.

const MAX_SIDE = 1568; // the longest edge the model uses; bigger photos only cost upload time
const STYLE = `
  :host { all: initial; font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
  .chat { display: flex; flex-direction: column; height: 100%; background: var(--card-background-color, #fff);
    color: var(--primary-text-color, #212121); box-sizing: border-box; }
  header { display: flex; align-items: center; gap: 8px; padding: 12px 16px; border-bottom: 1px solid var(--divider-color, #e0e0e0); }
  header h2 { flex: 1; margin: 0; font-size: 18px; font-weight: 500; }
  header button { background: none; border: 0; color: var(--secondary-text-color, #727272); font-size: 14px; cursor: pointer; padding: 6px; }
  .log { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 85%; padding: 10px 12px; border-radius: 12px; line-height: 1.45; font-size: 15px; white-space: pre-wrap; word-wrap: break-word; }
  .user { align-self: flex-end; background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
  .assistant { align-self: flex-start; background: var(--secondary-background-color, #f2f2f2); }
  .note { align-self: center; font-size: 13px; color: var(--secondary-text-color, #727272); }
  .error { align-self: center; font-size: 14px; color: var(--error-color, #db4437); text-align: center; }
  .start { align-self: center; margin-top: 4px; }
  form { display: flex; gap: 8px; align-items: flex-end; padding: 12px; border-top: 1px solid var(--divider-color, #e0e0e0);
    padding-bottom: calc(12px + env(safe-area-inset-bottom, 0px)); }
  textarea { flex: 1; resize: none; min-height: 40px; max-height: 120px; padding: 10px; border-radius: 10px; font: inherit; font-size: 15px;
    border: 1px solid var(--divider-color, #ccc); background: var(--card-background-color, #fff); color: inherit; box-sizing: border-box; }
  .btn { height: 40px; min-width: 40px; border-radius: 20px; border: 0; padding: 0 14px; font: inherit; font-size: 15px; cursor: pointer;
    background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
  .btn.ghost { background: var(--secondary-background-color, #eee); color: var(--primary-text-color, #212121); padding: 0 12px; }
  .btn:disabled { opacity: 0.5; cursor: default; }
  .thumbs { display: flex; gap: 6px; padding: 0 12px; }
  .thumbs img { width: 48px; height: 48px; object-fit: cover; border-radius: 6px; }
  input[type=file] { display: none; }
  form[hidden], .error[hidden] { display: none; }
  .claim { flex-direction: column; align-items: stretch; gap: 10px; padding: 4px 2px; border-top: 0; }
  .claim h3 { margin: 0; font-size: 17px; font-weight: 500; }
  .claim p { margin: 0; font-size: 14px; line-height: 1.45; color: var(--secondary-text-color, #727272); }
  .claim label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
  .claim input { height: 40px; padding: 0 10px; border-radius: 8px; font: inherit; font-size: 15px; box-sizing: border-box;
    border: 1px solid var(--divider-color, #ccc); background: var(--card-background-color, #fff); color: inherit; }
  .claim .error { text-align: left; }
`;

function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function formatReply(text) {
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
}

async function shrinkPhoto(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
  return { media_type: "image/jpeg", data: dataUrl.split(",")[1], preview: dataUrl };
}

class OrchestraAgentChat extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._photos = [];
    this._busy = false;
    this._loaded = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded && hass) {
      this._loaded = true;
      this._render();
      this._loadHistory();
    }
  }

  get hass() {
    return this._hass;
  }

  _render() {
    const closable = this.hasAttribute("closable");
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <div class="chat">
        <header>
          <h2>Setup assistant</h2>
          <button class="reset" title="Start a new conversation">New chat</button>
          ${closable ? '<button class="close" title="Close">Close</button>' : ""}
        </header>
        <div class="log"></div>
        <div class="thumbs"></div>
        <form>
          <input type="file" accept="image/*" capture="environment" multiple>
          <button type="button" class="btn ghost photo" title="Add a photo, for example of a label">Photo</button>
          <textarea rows="1" placeholder="Ask or answer here"></textarea>
          <button type="submit" class="btn send">Send</button>
        </form>
      </div>`;
    const root = this.shadowRoot;
    root.querySelector("form").classList.add("ask");
    this._log = root.querySelector(".log");
    this._thumbs = root.querySelector(".thumbs");
    this._input = root.querySelector("textarea");
    this._file = root.querySelector("input[type=file]");
    root.querySelector("form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      this._send(this._input.value.trim());
    });
    this._input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        this._send(this._input.value.trim());
      }
    });
    root.querySelector(".photo").addEventListener("click", () => this._file.click());
    this._file.addEventListener("change", () => this._addPhotos());
    root.querySelector(".reset").addEventListener("click", () => this._reset());
    if (closable) {
      root.querySelector(".close").addEventListener("click", () => this.dispatchEvent(new CustomEvent("close")));
    }
  }

  async _loadHistory() {
    let history = [];
    let status = { open_items: 0, waiting_devices: 0 };
    try {
      [{ messages: history }, status] = await Promise.all([
        this._hass.callWS({ type: "orchestra/agent/history" }),
        this._hass.callWS({ type: "orchestra/agent/status" }),
      ]);
    } catch (err) {
      this._add("error", "The setup assistant isn't available. Restart Home Assistant after updating Orchestra.");
      return;
    }
    if (status.claim_needed) {
      this._showClaim();
      return;
    }
    if (history.length) {
      for (const m of history) {
        this._add(m.role, m.text + (m.photos ? `  [${m.photos} photo${m.photos > 1 ? "s" : ""}]` : ""));
      }
      return;
    }
    const waiting = status.open_items + status.waiting_devices;
    this._add(
      "assistant",
      waiting
        ? `Hi! I can help finish setting up your home. ${waiting} thing${waiting > 1 ? "s are" : " is"} waiting for you. ` +
            "I'll take you through them one at a time."
        : "Hi! Everything I know about is set up. Ask me about any device you'd like to add."
    );
    const start = document.createElement("button");
    start.className = "btn start";
    start.textContent = waiting ? "Let's start" : "Look for devices";
    start.addEventListener("click", () => {
      start.remove();
      this._send(waiting ? "Let's start. What needs me?" : "Can you check whether anything is waiting to be set up?");
    });
    this._log.appendChild(start);
  }

  // "Make this home yours": the homeowner's own login replaces the shared Orchestra-Agent one.
  // This form goes straight to Home Assistant's login store, never to the assistant.
  _showClaim() {
    const chatForm = this.shadowRoot.querySelector("form.ask");
    chatForm.hidden = true;
    const box = document.createElement("form");
    box.className = "claim";
    box.innerHTML = `
      <h3>Make this home yours</h3>
      <p>This box is still signed in with the shared Orchestra setup account. Create your own login now.
        After that, the shared account is locked and you'll sign in as yourself. Your password stays in
        Home Assistant: the assistant never sees it.</p>
      <label>Your name<input name="name" autocomplete="name" required></label>
      <label>Username<input name="username" autocomplete="username" autocapitalize="none" required></label>
      <label>Password (at least 8 characters)<input name="password" type="password" autocomplete="new-password" minlength="8" required></label>
      <label>Password again<input name="confirm" type="password" autocomplete="new-password" required></label>
      <div class="error" hidden></div>
      <button type="submit" class="btn">Create my login</button>`;
    const error = box.querySelector(".error");
    const fail = (text) => {
      error.textContent = text;
      error.hidden = false;
    };
    box.querySelector("[name=name]").addEventListener("input", (ev) => {
      const user = box.querySelector("[name=username]");
      if (!user.dataset.touched) user.value = ev.target.value.trim().split(/\s+/)[0].toLowerCase().replace(/[^a-z0-9._-]/g, "");
    });
    box.querySelector("[name=username]").addEventListener("input", (ev) => (ev.target.dataset.touched = "1"));
    box.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const data = Object.fromEntries(new FormData(box));
      if (data.password !== data.confirm) return fail("The two passwords don't match.");
      const button = box.querySelector("button");
      button.disabled = true;
      error.hidden = true;
      try {
        const { username } = await this._hass.callWS({
          type: "orchestra/agent/claim",
          name: data.name,
          username: data.username,
          password: data.password,
        });
        box.innerHTML = `<h3>Welcome home</h3><p>Your login <b>${escapeHtml(username)}</b> is ready and the shared
          setup account is locked. Signing you out now: sign back in as <b>${escapeHtml(username)}</b>
          and I'll help with the rest of the setup.</p>`;
        setTimeout(() => location.reload(), 5000);
      } catch (err) {
        button.disabled = false;
        fail(err?.message || "That didn't work. Try again.");
      }
    });
    this._log.appendChild(box);
  }

  _add(role, text) {
    const div = document.createElement("div");
    div.className = `msg ${role === "error" || role === "note" ? role : role === "user" ? "user" : "assistant"}`;
    if (role === "assistant") div.innerHTML = formatReply(text);
    else div.textContent = text;
    this._log.appendChild(div);
    this._log.scrollTop = this._log.scrollHeight;
    return div;
  }

  async _addPhotos() {
    for (const file of Array.from(this._file.files || []).slice(0, 4 - this._photos.length)) {
      try {
        const photo = await shrinkPhoto(file);
        this._photos.push(photo);
        const img = document.createElement("img");
        img.src = photo.preview;
        this._thumbs.appendChild(img);
      } catch (err) {
        this._add("error", "That photo couldn't be read. Try another one.");
      }
    }
    this._file.value = "";
  }

  async _send(text) {
    if (this._busy || (!text && !this._photos.length)) return;
    this._busy = true;
    const photos = this._photos;
    this._photos = [];
    this._thumbs.innerHTML = "";
    this._input.value = "";
    this._add("user", text + (photos.length ? `  [${photos.length} photo${photos.length > 1 ? "s" : ""}]` : ""));
    const working = this._add("note", "Working on it…");
    this.shadowRoot.querySelector(".send").disabled = true;
    let unsub;
    const finish = () => {
      working.remove();
      this._busy = false;
      this.shadowRoot.querySelector(".send").disabled = false;
      if (unsub) unsub();
    };
    try {
      unsub = await this._hass.connection.subscribeMessage(
        (event) => {
          if (event.type === "text") this._log.insertBefore(this._bubble("assistant", event.text), working);
          else if (event.type === "tool") working.textContent = "Checking Home Assistant…";
          else if (event.type === "error") this._log.insertBefore(this._bubble("error", event.text), working);
          else if (event.type === "done") finish();
          this._log.scrollTop = this._log.scrollHeight;
        },
        { type: "orchestra/agent/send", text, images: photos.map(({ media_type, data }) => ({ media_type, data })) }
      );
    } catch (err) {
      finish();
      this._add("error", "Couldn't reach the setup assistant. Try again.");
    }
  }

  _bubble(role, text) {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    if (role === "assistant") div.innerHTML = formatReply(text);
    else div.textContent = text;
    return div;
  }

  async _reset() {
    if (this._busy) return;
    await this._hass.callWS({ type: "orchestra/agent/reset" });
    this._log.innerHTML = "";
    this._loadHistory();
  }
}
customElements.get("orchestra-agent-chat") || customElements.define("orchestra-agent-chat", OrchestraAgentChat);

// Full-page sidebar panel ("Setup assistant")
class OrchestraAgentPanel extends HTMLElement {
  set hass(hass) {
    if (!this._chat) {
      this.style.cssText = "display:block;height:100%;";
      this._chat = document.createElement("orchestra-agent-chat");
      this._chat.style.cssText = "display:block;height:100%;max-width:760px;margin:0 auto;";
      this.appendChild(this._chat);
    }
    this._chat.hass = hass;
  }
  set narrow(_value) {}
  set panel(_value) {}
  set route(_value) {}
}
customElements.get("orchestra-agent-panel") || customElements.define("orchestra-agent-panel", OrchestraAgentPanel);

// The "Setup" tab and drawer on every page
(function addDrawer() {
  if (window.__orchestraAgentDrawer) return;
  window.__orchestraAgentDrawer = true;

  const getHass = () => document.querySelector("home-assistant")?.hass;

  const start = () => {
    const hass = getHass();
    if (!hass || !hass.user) return setTimeout(start, 1000);
    if (!hass.user.is_admin) return;

    const host = document.createElement("div");
    host.id = "orchestra-agent";
    const shadow = host.attachShadow({ mode: "open" });
    shadow.innerHTML = `
      <style>
        .tab { position: fixed; right: 0; top: 50%; transform: translateY(-50%); z-index: 7; border: 0; cursor: pointer;
          writing-mode: vertical-rl; padding: 14px 8px; border-radius: 10px 0 0 10px; font: 500 14px Roboto, sans-serif;
          background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); box-shadow: 0 2px 6px rgba(0,0,0,.25); }
        .badge { display: inline-block; writing-mode: horizontal-tb; margin-top: 6px; min-width: 18px; padding: 1px 4px; border-radius: 9px;
          background: var(--accent-color, #ff9800); color: #fff; font-size: 12px; }
        .badge[hidden] { display: none; }
        .drawer { position: fixed; top: 0; right: 0; bottom: 0; width: min(420px, 100vw); z-index: 8; transform: translateX(100%);
          transition: transform .2s ease; box-shadow: -2px 0 12px rgba(0,0,0,.3); padding-top: env(safe-area-inset-top, 0px);
          background: var(--card-background-color, #fff); }
        .drawer.open { transform: none; }
        orchestra-agent-chat { display: block; height: 100%; }
      </style>
      <button class="tab" title="Orchestra setup assistant">Setup <span class="badge" hidden></span></button>
      <div class="drawer"><orchestra-agent-chat closable></orchestra-agent-chat></div>`;
    document.body.appendChild(host);

    const tab = shadow.querySelector(".tab");
    const badge = shadow.querySelector(".badge");
    const drawer = shadow.querySelector(".drawer");
    const chat = shadow.querySelector("orchestra-agent-chat");
    const onPanel = () => location.pathname.startsWith("/orchestra-setup");
    const open = () => {
      chat.hass = getHass();
      drawer.classList.add("open");
      tab.hidden = true;
    };
    const close = () => {
      drawer.classList.remove("open");
      tab.hidden = onPanel();
    };
    tab.addEventListener("click", open);
    chat.addEventListener("close", close);

    let claimShown = false;
    const refresh = async () => {
      tab.hidden = onPanel() || drawer.classList.contains("open");
      try {
        const status = await getHass().callWS({ type: "orchestra/agent/status" });
        const waiting = status.open_items + status.waiting_devices;
        badge.hidden = !waiting && !status.claim_needed;
        badge.textContent = status.claim_needed ? "!" : waiting;
        // Open once by itself on a fresh Orchestra box, so nobody has to find the tab
        let greeted = false;
        try { greeted = localStorage.getItem("orchestra-agent-greeted") === "1"; } catch (err) {}
        // and once per page load until someone has made the home theirs
        const claimDue = status.claim_needed && !claimShown;
        if ((claimDue || (status.open_items && !greeted)) && !onPanel() && !drawer.classList.contains("open")) {
          claimShown = claimShown || status.claim_needed;
          if (!status.claim_needed) {
            try { localStorage.setItem("orchestra-agent-greeted", "1"); } catch (err) {}
          }
          open();
        }
      } catch (err) {
        tab.hidden = true; // the integration isn't running
      }
    };
    refresh();
    setInterval(refresh, 60000);
    window.addEventListener("location-changed", () => (tab.hidden = onPanel() || drawer.classList.contains("open")));
  };
  start();
})();
