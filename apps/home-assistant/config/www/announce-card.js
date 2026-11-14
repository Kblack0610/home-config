// custom:announce-card: speak a typed message or a recorded voice clip on the
// rooms you pick. Loaded on every HA page via `frontend: extra_module_url`.
//
// Rooms are the members of group.announce_rooms (packages/voice_targets.yaml),
// named by their friendly_name. No room chip selected means All.
// Text goes to script.tts_announce as `message`. A recording is uploaded to
// local media (/media, the container's own disk, so a restart clears it) and
// sent to the same script as `media`; older clips are removed on each upload.
// Upload and remove are admin-only in HA, so recording needs an admin login.
//
// Options: rooms (group entity), language (input_select entity), max_seconds.

const UPLOAD_URL = "/api/media_source/local_source/upload";
const MEDIA_ROOT = "media-source://media_source/local";
const CLIP_PREFIX = "announce-";
const MIME_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"];

class AnnounceCard extends HTMLElement {
  setConfig(config) {
    this._config = {
      rooms: "group.announce_rooms",
      language: "input_select.voice_announce_language",
      max_seconds: 30,
      ...config,
    };
    this._selected = new Set();
    this._roomsKey = "";
    if (!this.shadowRoot) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    this._renderRooms();
    this._renderLanguage();
  }

  getCardSize() {
    return 3;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto" };
  }

  _build() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        ha-card { padding: 12px; display: flex; flex-direction: column; gap: 8px; }
        .head { display: flex; align-items: center; gap: 8px; }
        .title { flex: 1; font-size: 16px; font-weight: 500; }
        select { font: inherit; padding: 4px 8px; border-radius: 8px;
          border: 1px solid var(--divider-color); background: var(--card-background-color);
          color: var(--primary-text-color); }
        .chips { display: flex; flex-wrap: wrap; gap: 8px; }
        .chip { font: inherit; padding: 8px 14px; border-radius: 18px; cursor: pointer;
          border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); }
        .chip.on { background: var(--primary-color); border-color: var(--primary-color);
          color: var(--text-primary-color, #fff); }
        .chip:disabled { opacity: 0.4; cursor: default; }
        .row { display: flex; gap: 8px; align-items: center; }
        input { flex: 1; min-width: 0; font: inherit; padding: 10px 12px; border-radius: 8px;
          border: 1px solid var(--divider-color); background: var(--card-background-color);
          color: var(--primary-text-color); }
        .btn { flex: none; display: flex; align-items: center; justify-content: center; gap: 6px;
          font: inherit; height: 44px; min-width: 44px; padding: 0 12px; border-radius: 8px;
          cursor: pointer; border: 1px solid var(--divider-color); background: none;
          color: var(--primary-text-color); }
        .btn.mic.rec { background: var(--error-color); border-color: var(--error-color); color: #fff; }
        .btn[hidden] { display: none; }
        .status { font-size: 13px; color: var(--secondary-text-color); min-height: 1em; }
        .status.err { color: var(--error-color); }
      </style>
      <ha-card>
        <div class="head">
          <ha-icon icon="mdi:bullhorn-variant"></ha-icon>
          <span class="title">Announce</span>
          <select class="lang" aria-label="Language"></select>
        </div>
        <div class="chips"></div>
        <div class="row">
          <input class="msg" type="text" maxlength="240" placeholder="Type a message" />
          <button class="btn send" title="Speak the message"><ha-icon icon="mdi:send"></ha-icon></button>
          <button class="btn cancel" title="Cancel the recording" hidden><ha-icon icon="mdi:close"></ha-icon></button>
          <button class="btn mic" title="Record a message"><ha-icon icon="mdi:microphone"></ha-icon><span class="secs"></span></button>
        </div>
        <div class="status"></div>
      </ha-card>`;
    this._el = (sel) => root.querySelector(sel);
    this._el(".send").addEventListener("click", () => this._sendText());
    this._el(".msg").addEventListener("keydown", (e) => {
      if (e.key === "Enter") this._sendText();
    });
    this._el(".mic").addEventListener("click", () => (this._recorder ? this._stop(true) : this._record()));
    this._el(".cancel").addEventListener("click", () => this._stop(false));
    this._el(".lang").addEventListener("change", (e) =>
      this._hass.callService("input_select", "select_option", {
        entity_id: this._config.language,
        option: e.target.value,
      }),
    );
  }

  // [{id, name, ok}] for each member of the rooms group.
  _rooms() {
    const group = this._hass.states[this._config.rooms];
    const ids = (group && group.attributes.entity_id) || [];
    return ids.map((id) => {
      const st = this._hass.states[id];
      return {
        id,
        name: (st && st.attributes.friendly_name) || id,
        ok: !!st && !["unavailable", "unknown"].includes(st.state),
      };
    });
  }

  _renderRooms() {
    const rooms = this._rooms();
    const key = JSON.stringify(rooms) + [...this._selected].join();
    if (key === this._roomsKey) return;
    this._roomsKey = key;
    const box = this._el(".chips");
    box.replaceChildren();
    const chip = (label, on, disabled, onClick) => {
      const b = document.createElement("button");
      b.className = "chip" + (on ? " on" : "");
      b.textContent = label;
      b.disabled = disabled;
      b.addEventListener("click", onClick);
      box.append(b);
    };
    chip("All", this._selected.size === 0, false, () => {
      this._selected.clear();
      this._renderRooms();
    });
    for (const r of rooms) {
      chip(r.name, this._selected.has(r.id), !r.ok, () => {
        if (this._selected.has(r.id)) this._selected.delete(r.id);
        else this._selected.add(r.id);
        this._renderRooms();
      });
    }
  }

  _renderLanguage() {
    const st = this._hass.states[this._config.language];
    const sel = this._el(".lang");
    sel.hidden = !st;
    if (!st) return;
    const opts = st.attributes.options || [];
    if (sel.options.length !== opts.length) {
      sel.replaceChildren(...opts.map((o) => new Option(o, o)));
    }
    sel.value = st.state;
  }

  // The rooms to speak on: the picked ones, or every available room for All.
  _targets() {
    const ok = this._rooms().filter((r) => r.ok).map((r) => r.id);
    return this._selected.size ? ok.filter((id) => this._selected.has(id)) : ok;
  }

  _targetNames(targets) {
    if (!this._selected.size) return "all rooms";
    const names = Object.fromEntries(this._rooms().map((r) => [r.id, r.name]));
    return targets.map((id) => names[id]).join(", ");
  }

  _status(text, err = false) {
    const s = this._el(".status");
    s.textContent = text;
    s.classList.toggle("err", err);
  }

  async _announce(data) {
    const target = this._targets();
    if (!target.length) {
      this._status("No room is available to speak on.", true);
      return false;
    }
    await this._hass.callService("script", "tts_announce", { ...data, target });
    this._status(`Sent to ${this._targetNames(target)}.`);
    return true;
  }

  async _sendText() {
    const input = this._el(".msg");
    const message = input.value.trim();
    if (!message) {
      this._status("Type a message first, or tap the mic to record one.", true);
      return;
    }
    const st = this._hass.states[this._config.language];
    try {
      if (await this._announce({ message, ...(st ? { language: st.state } : {}) })) input.value = "";
    } catch (e) {
      this._status(`Could not send: ${e.message || e}`, true);
    }
  }

  async _record() {
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      this._status("This browser cannot record audio.", true);
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch (e) {
      this._status(`Microphone unavailable: ${e.name}.`, true);
      return;
    }
    const mimeType = MIME_TYPES.find((t) => MediaRecorder.isTypeSupported(t));
    const rec = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      if (rec.sendOnStop) this._sendClip(new Blob(chunks, { type: rec.mimeType }));
    };
    this._recorder = rec;
    this._started = Date.now();
    rec.start();
    this._setRecording(true);
    this._status("Recording. Tap the mic again to send.");
    this._tick = setInterval(() => {
      const secs = Math.floor((Date.now() - this._started) / 1000);
      this._el(".secs").textContent = `${secs}s`;
      if (secs >= this._config.max_seconds) this._stop(true);
    }, 250);
  }

  _stop(send) {
    const rec = this._recorder;
    if (!rec) return;
    this._recorder = null;
    clearInterval(this._tick);
    this._setRecording(false);
    rec.sendOnStop = send && Date.now() - this._started >= 700;
    if (!send) this._status("Recording discarded.");
    else if (!rec.sendOnStop) this._status("Too short to send.", true);
    rec.stop();
  }

  _setRecording(on) {
    this._el(".mic").classList.toggle("rec", on);
    this._el(".mic ha-icon").setAttribute("icon", on ? "mdi:stop" : "mdi:microphone");
    this._el(".secs").textContent = on ? "0s" : "";
    this._el(".cancel").hidden = !on;
    this._el(".send").hidden = on;
  }

  async _sendClip(blob) {
    this._status("Sending recording...");
    const ext = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
    const form = new FormData();
    form.append("media_content_id", MEDIA_ROOT);
    form.append("file", blob, `${CLIP_PREFIX}${Date.now()}.${ext}`);
    try {
      const resp = await this._hass.fetchWithAuth(UPLOAD_URL, { method: "POST", body: form });
      if (resp.status === 401) throw new Error("recording needs an admin login");
      if (!resp.ok) throw new Error(`upload failed (HTTP ${resp.status})`);
      const media = (await resp.json()).media_content_id;
      await this._announce({ media });
      this._removeOldClips(media);
    } catch (e) {
      this._status(`Could not send the recording: ${e.message || e}`, true);
    }
  }

  async _removeOldClips(keep) {
    try {
      const dir = await this._hass.callWS({ type: "media_source/browse_media", media_content_id: MEDIA_ROOT });
      for (const c of dir.children || []) {
        if (c.title.startsWith(CLIP_PREFIX) && c.media_content_id !== keep) {
          await this._hass.callWS({ type: "media_source/local_source/remove", media_content_id: c.media_content_id });
        }
      }
    } catch (e) {
      console.warn("[announce-card] could not remove old clips", e);
    }
  }
}

if (!customElements.get("announce-card")) customElements.define("announce-card", AnnounceCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "announce-card", name: "Announce", description: "Speak a message or a recording on chosen rooms." });
