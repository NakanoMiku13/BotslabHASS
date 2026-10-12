# Botslab Vacuum (360 / Qihoo) — Home Assistant integration

Unofficial Home Assistant integration for **Botslab / 360 (Qihoo) robot
vacuums**, reverse-engineered from the official Android app
(`com.visualworld.cloudsmart`). Developed and validated on a **Botslab S8**.

It gives you an **app-like experience in Home Assistant**: an interactive live
map, room / zone / no-go cleaning, a routines (schedules) editor, full controls,
and **autonomous login** (email + password — no manual token capture).

> ⚠️ **Unofficial.** Not affiliated with or endorsed by 360 / Qihoo / Botslab.
> It talks to the vendor cloud with your own account, for a device **you own**,
> as a personal interoperability project. Use at your own risk.

---

## Features

- 🔐 **Autonomous login** — sign in with your Botslab/360 **email + password**;
  the integration fetches and **self-refreshes** the session (Q/T/sid) on its
  own, including recovering from "logged in on another device". No token capture.
- 🗺️ **Live interactive map** — a rendered map (rooms, walls, dock, no-go,
  **live robot position + heading**, cleaning path) shown through the
  [xiaomi-vacuum-map-card] overlay (bundled & auto-loaded):
  - **draw cleaning zones** and clean them,
  - **tap rooms (in order)** to clean selected rooms,
  - **draw no-go zones**.
- 🧭 **Routines editor** — a custom card to create/edit/enable/delete the robot's
  **native cleaning routines** (the same schedules the app shows): time, days,
  rooms-in-order, suction, passes.
- 🧹 **Controls** — `vacuum.*` entity (start / pause / stop / return / locate /
  fan speed) + switches/selects/number for Auto-boost, mop mode, pad wetness,
  volume.
- 📊 **Sensors** — battery, status, error, suction, cleaning area, volume,
  schedules.
- 🧩 **Zero-config frontend** — the map and routines cards are **served and
  auto-registered by the integration**; you don't add any Lovelace resources.

---

## Installation

### Via HACS (recommended)
1. HACS → ⋮ → **Custom repositories** → add this repo's URL, category **Integration**.
2. Install **Botslab Vacuum (360/Qihoo)**, then restart Home Assistant.
   *(HACS custom repositories must be hosted on GitHub.)*

### Manual
Copy `custom_components/botslab_vacuum/` into your HA `config/custom_components/`
and restart Home Assistant.

> Requirement `pycryptodome` is installed automatically. The frontend cards are
> bundled in the integration and auto-loaded — **no HACS frontend install and no
> Lovelace resources are needed**.

---

## Configuration

**Settings → Devices & Services → Add Integration → Botslab Vacuum**, then enter:

- **Account email** and **password** (your Botslab / 360 account).
- **Region** (default `na1`; others: `ap1`, `eu1`, `eu2`).

The device is auto-discovered. On session expiry the integration re-logs-in
automatically; if your password changes it raises a reauth prompt.

> Your password is stored in Home Assistant's config entry (`.storage`), the
> same as any other cloud integration.

---

## Dashboard

Add a dashboard (or view) with the two cards — see
[`ha/francisca.example.yaml`](ha/francisca.example.yaml) for a full example
(map view + routines view). Both cards auto-load; just reference them:

```yaml
- type: custom:xiaomi-vacuum-map-card
  entity: vacuum.<your_robot>
  map_source: { camera: camera.<your_robot>_map }
  calibration_source: { camera: true }
  # map_modes: zone draw / room select / no-go  (see the example)

- type: custom:botslab-routines-card
  entity: vacuum.<your_robot>
```

**Room selection (map card ROOM mode)** needs per-room outlines in its
`predefined_selections`. Copy them from the camera's `rooms` attribute
(Developer Tools → States → `camera.<robot>_map` → `rooms`: a list of
`{id, name, outline}` in vacuum coordinates). Zone and no-go drawing work
without this. Re-copy if the robot re-saves/edits the map.

---

## Services

| Service | What it does |
|---|---|
| `botslab_vacuum.clean_rooms` | clean rooms by id, in order (`room_ids`, `passes`, `suction`) |
| `botslab_vacuum.clean_zones` | clean drawn rectangles (`zones` = `[[x1,y1,x2,y2],…]`, `repeats`, `suction`) |
| `botslab_vacuum.set_no_go_zones` | create/replace/clear no-go zones (`zones`, `mode`) |
| `botslab_vacuum.set_schedule` | create/replace a routine (`time`, `days`, `room_ids`, `suction`, `passes`, `active`, `slot`) |
| `botslab_vacuum.delete_schedule` | remove a routine by `slot` |

Standard `vacuum.*` services (start/pause/stop/return_to_base/locate/set_fan_speed)
also work.

---

## How it works

The vacuum is a cloud-shadow device; the app talks to `*-sapp-api.botslab.com`.
Each device request needs a signed query (`sign = md5(appkey + m2 + sign_ts +
sign_no + secret)`) and an `authorization: jws base64({Q, T, sid})` header. The
session is bootstrapped headlessly: `UserIntf.login` (360 account crypto:
`parad` = DES-CBC, key = RSA-1024, sig = md5) → `POST /v1/app/login` → `sid`.
The map is downloaded from OSS (base64 + LZ4 occupancy grid + overlays) and
rendered server-side (Pillow) into the camera image. Deep dive in
[`docs/`](docs/) (`PROTOCOL.md`, `CONTROL.md`, `REVERSE-ENGINEERING.md`).

A standalone, dependency-free CLI client (`botslab_client.py`) is also included
for testing reads/control from the shell.

---

## Known issues / notes

- **Robot heading** arrow uses a best-guess angle convention (`angle_conv` in
  `map_render.py`); if it points the wrong way, adjust it (one line).
- **No-go write** uses `SetFbdPlaces` (inline); the exact payload shape is not
  fully confirmed — test with one zone first.
- **Rename rooms / edit room partitions** is not implemented yet (needs an OSS
  upload flow that isn't resolved).
- **Map card ROOM outlines** are static config — regenerate after the robot
  re-saves the map.

---

## Credits

- Map overlay: **[xiaomi-vacuum-map-card]** by Piotr Machowski (MIT), bundled
  (v2.4.1) for out-of-the-box setup — install via HACS for updates.
- Structural inspiration: [`juetthei/ha-360-vacuum`] (different, legacy
  `q.smart.360.cn` backend / 360 S6 — not compatible with the S8, but a good
  template; independently confirms `sid` is the key credential).

[xiaomi-vacuum-map-card]: https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card
[`juetthei/ha-360-vacuum`]: https://github.com/juetthei/ha-360-vacuum

## Security / privacy

- Don't commit captures (`*.pcap`/`*.keylog`) or a real map fixture (your floor
  plan) — covered by `.gitignore`.
- Your account credentials live in HA's config entry; rotate your password if it
  leaks.

## License

MIT — see [`LICENSE`](LICENSE).
