/* Botslab Routines Card — create/edit the vacuum's native cleaning routines.
 * Reads sensor.<obj>_schedules (schedules + rooms) and calls the
 * botslab_vacuum.set_schedule / delete_schedule services. No build step. */

const DAY_LABELS = ["D", "L", "M", "X", "J", "V", "S"];       // 0=Sun .. 6=Sat
const SUCTIONS = ["quiet", "normal", "strong", "max", "auto"];
const SUCTION_ES = { quiet: "Silencio", normal: "Normal", strong: "Fuerte", max: "MAX", auto: "Auto" };

class BotslabRoutinesCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) throw new Error("Se requiere 'entity' (vacuum.*)");
    this._config = config;
    this._editing = null;
    this._built = false;
    this._lastKey = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    if (!this._editing) {
      const key = JSON.stringify(this._data());
      if (key !== this._lastKey) { this._lastKey = key; this._renderList(); }
    }
  }

  getCardSize() { return 8; }

  _schedEntity() {
    if (this._config.schedules_entity) return this._config.schedules_entity;
    return "sensor." + this._config.entity.split(".")[1] + "_schedules";
  }

  _data() {
    const st = this._hass && this._hass.states[this._schedEntity()];
    const a = st ? st.attributes : {};
    return { schedules: a.schedules || [], rooms: a.rooms || {} };
  }

  _call(service, data) {
    return this._hass.callService("botslab_vacuum", service, data,
      { entity_id: this._config.entity });
  }

  _build() {
    this.innerHTML = `
      <ha-card header="Rutinas de limpieza">
        <style>
          .wrap{padding:8px 16px 16px}
          .row{display:flex;align-items:center;gap:10px;padding:10px 0;border-top:1px solid var(--divider-color)}
          .row:first-child{border-top:none}
          .time{font-size:1.5em;font-weight:600;min-width:64px}
          .meta{flex:1;display:flex;flex-direction:column;gap:4px;font-size:.9em;color:var(--secondary-text-color)}
          .days{display:flex;gap:3px}
          .d{width:20px;height:20px;line-height:20px;text-align:center;border-radius:50%;font-size:.72em;background:var(--divider-color);color:var(--secondary-text-color)}
          .d.on{background:var(--primary-color);color:var(--text-primary-color,#fff)}
          .rooms{font-weight:500;color:var(--primary-text-color)}
          .iconbtn{cursor:pointer;color:var(--secondary-text-color);background:none;border:none;font-size:1.1em;padding:4px}
          button.main{background:var(--primary-color);color:var(--text-primary-color,#fff);border:none;border-radius:8px;padding:8px 14px;cursor:pointer;font-size:.95em}
          button.ghost{background:none;border:1px solid var(--divider-color);border-radius:8px;padding:8px 14px;cursor:pointer;color:var(--primary-text-color)}
          .add{padding-top:12px}
          .ed{border-top:1px solid var(--divider-color);margin-top:8px;padding-top:12px;display:flex;flex-direction:column;gap:14px}
          .fld{display:flex;flex-direction:column;gap:6px}
          .fld>label{font-size:.8em;color:var(--secondary-text-color)}
          .chips{display:flex;flex-wrap:wrap;gap:6px}
          .chip{border:1px solid var(--divider-color);border-radius:16px;padding:5px 11px;cursor:pointer;user-select:none;font-size:.9em;position:relative}
          .chip.sel{background:var(--primary-color);color:var(--text-primary-color,#fff);border-color:var(--primary-color)}
          .chip .ord{font-size:.7em;margin-left:5px;opacity:.85}
          select,input[type=time]{padding:7px;border-radius:8px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);font-size:1em}
          .acts{display:flex;gap:10px;justify-content:flex-end}
          .muted{color:var(--secondary-text-color);font-style:italic}
          .inline{display:flex;align-items:center;gap:8px}
        </style>
        <div class="wrap">
          <div id="list"></div>
          <div class="add"><button class="main" id="addbtn">＋ Nueva rutina</button></div>
          <div id="editor"></div>
        </div>
      </ha-card>`;
    this.querySelector("#addbtn").addEventListener("click", () => this._openEditor(null));
    this._built = true;
  }

  _roomName(rooms, id) { return rooms[id] != null ? rooms[id] : ("Room " + id); }

  _renderList() {
    const { schedules, rooms } = this._data();
    const list = this.querySelector("#list");
    if (!schedules.length) {
      list.innerHTML = `<div class="muted" style="padding:8px 0">Sin rutinas. Crea una con “＋ Nueva rutina”.</div>`;
      return;
    }
    list.innerHTML = schedules.map((s) => {
      const days = DAY_LABELS.map((d, i) =>
        `<span class="d ${s.days.includes(i) ? "on" : ""}">${d}</span>`).join("");
      const roomsTxt = (s.room_ids && s.room_ids.length)
        ? s.room_ids.map((id) => this._roomName(rooms, id)).join(" → ")
        : "Casa completa";
      const sx = SUCTION_ES[s.suction] || s.suction || "Auto";
      return `
        <div class="row">
          <div class="time">${s.time}</div>
          <div class="meta">
            <div class="days">${days}</div>
            <div class="rooms">${roomsTxt}</div>
            <div>${sx} · ${s.passes === 2 ? "2 pasadas" : "1 pasada"}</div>
          </div>
          <label class="inline"><input type="checkbox" data-act="toggle" data-slot="${s.slot}" ${s.active ? "checked" : ""}></label>
          <button class="iconbtn" title="Ejecutar ahora" data-act="run" data-slot="${s.slot}">▶</button>
          <button class="iconbtn" data-act="edit" data-slot="${s.slot}">✎</button>
          <button class="iconbtn" data-act="del" data-slot="${s.slot}">🗑</button>
        </div>`;
    }).join("");
    list.querySelectorAll("[data-act]").forEach((el) => {
      const slot = parseInt(el.dataset.slot, 10);
      const act = el.dataset.act;
      if (act === "toggle") el.addEventListener("change", (e) => this._toggle(slot, e.target.checked));
      else if (act === "run") el.addEventListener("click", () => this._run(slot));
      else el.addEventListener("click", () => act === "edit" ? this._openEditor(slot) : this._delete(slot));
    });
  }

  async _run(slot) {
    if (!confirm("¿Ejecutar esta rutina ahora?")) return;
    await this._call("run_schedule", { slot });
  }

  _openEditor(slot) {
    const { schedules } = this._data();
    if (slot != null && schedules[slot]) {
      const s = schedules[slot];
      this._editing = {
        slot, time: s.time || "09:00", days: new Set(s.days || []),
        rooms: [...(s.room_ids || [])],
        suction: s.suction || "auto", passes: s.passes === 2 ? 2 : 1, active: !!s.active,
      };
    } else {
      this._editing = { slot: -1, time: "09:00", days: new Set(), rooms: [],
        suction: "auto", passes: 1, active: true };
    }
    this.querySelector("#addbtn").style.display = "none";
    this._renderEditor();
  }

  _closeEditor() {
    this._editing = null;
    this.querySelector("#editor").innerHTML = "";
    this.querySelector("#addbtn").style.display = "";
    this._lastKey = null;
    this._renderList();
  }

  _renderEditor() {
    const e = this._editing;
    const { rooms } = this._data();
    const roomIds = Object.keys(rooms).map((k) => parseInt(k, 10)).sort((a, b) => a - b);
    const ed = this.querySelector("#editor");
    const dayChips = DAY_LABELS.map((d, i) =>
      `<div class="chip ${e.days.has(i) ? "sel" : ""}" data-day="${i}">${d}</div>`).join("");
    const roomChips = roomIds.map((id) => {
      const pos = e.rooms.indexOf(id);
      const ord = pos >= 0 ? `<span class="ord">${pos + 1}</span>` : "";
      return `<div class="chip ${pos >= 0 ? "sel" : ""}" data-room="${id}">${this._roomName(rooms, id)}${ord}</div>`;
    }).join("");
    const sucOpts = SUCTIONS.map((s) =>
      `<option value="${s}" ${e.suction === s ? "selected" : ""}>${SUCTION_ES[s]}</option>`).join("");
    ed.innerHTML = `
      <div class="ed">
        <div class="fld"><label>Hora</label><input type="time" id="f-time" value="${e.time}"></div>
        <div class="fld"><label>Días</label><div class="chips" id="f-days">${dayChips}</div></div>
        <div class="fld"><label>Habitaciones (toca en orden; vacío = casa completa)</label>
          <div class="chips" id="f-rooms">${roomChips || '<span class="muted">Mapa no cargado</span>'}</div></div>
        <div class="fld"><label>Succión</label><select id="f-suc">${sucOpts}</select></div>
        <div class="fld"><label>Pasadas</label>
          <select id="f-pass"><option value="1" ${e.passes === 1 ? "selected" : ""}>1</option>
          <option value="2" ${e.passes === 2 ? "selected" : ""}>2</option></select></div>
        <label class="inline"><input type="checkbox" id="f-active" ${e.active ? "checked" : ""}> Activa</label>
        <div class="acts">
          <button class="ghost" id="f-cancel">Cancelar</button>
          <button class="main" id="f-save">${e.slot >= 0 ? "Guardar" : "Crear"}</button>
        </div>
      </div>`;
    ed.querySelectorAll("#f-days .chip").forEach((c) => c.addEventListener("click", () => {
      const d = parseInt(c.dataset.day, 10);
      e.days.has(d) ? e.days.delete(d) : e.days.add(d);
      this._syncEditFields(); this._renderEditor();
    }));
    ed.querySelectorAll("#f-rooms .chip").forEach((c) => c.addEventListener("click", () => {
      const id = parseInt(c.dataset.room, 10);
      const pos = e.rooms.indexOf(id);
      pos >= 0 ? e.rooms.splice(pos, 1) : e.rooms.push(id);
      this._syncEditFields(); this._renderEditor();
    }));
    ed.querySelector("#f-cancel").addEventListener("click", () => this._closeEditor());
    ed.querySelector("#f-save").addEventListener("click", () => this._save());
  }

  _syncEditFields() {
    // preserve time/suction/passes/active typed before a chip-triggered re-render
    const e = this._editing, ed = this.querySelector("#editor");
    const t = ed.querySelector("#f-time"); if (t) e.time = t.value;
    const s = ed.querySelector("#f-suc"); if (s) e.suction = s.value;
    const p = ed.querySelector("#f-pass"); if (p) e.passes = parseInt(p.value, 10);
    const a = ed.querySelector("#f-active"); if (a) e.active = a.checked;
  }

  async _save() {
    this._syncEditFields();
    const e = this._editing;
    if (!e.days.size) { alert("Elige al menos un día."); return; }
    await this._call("set_schedule", {
      time: e.time, days: [...e.days].sort((a, b) => a - b),
      room_ids: e.rooms, suction: e.suction, passes: e.passes,
      active: e.active, order: true, slot: e.slot,
    });
    this._closeEditor();
  }

  async _delete(slot) {
    if (!confirm("¿Borrar esta rutina?")) return;
    await this._call("delete_schedule", { slot });
    this._lastKey = null;
  }

  async _toggle(slot, active) {
    const s = this._data().schedules[slot];
    if (!s) return;
    await this._call("set_schedule", {
      time: s.time, days: s.days, room_ids: s.room_ids, suction: s.suction || "auto",
      passes: s.passes === 2 ? 2 : 1, active, order: !!s.order, slot,
    });
    this._lastKey = null;
  }
}

if (!customElements.get("botslab-routines-card")) {
  customElements.define("botslab-routines-card", BotslabRoutinesCard);
}
window.customCards = window.customCards || [];
window.customCards.push({
  type: "botslab-routines-card",
  name: "Botslab Routines",
  description: "Crear/editar rutinas de limpieza del robot",
});
