# botslab-cloud

Unofficial, read-only **cloud client for Botslab (360 / Qihoo) robot vacuums** —
reverse-engineered from the official Android app, aimed at a Home Assistant
integration. Validated against a **Botslab S8** ("Francisca").

> ⚠️ **Status: private / work-in-progress.** Reading works end-to-end. Control
> (start / dock / suction) is mapped but not yet implemented. This is an
> interoperability/personal-automation project for a device **you own**. Not
> affiliated with or endorsed by 360 / Qihoo / Botslab.

---

## What it does

A pure-standard-library Python client that reads the device shadow from the
Botslab cloud using a captured 360 account session:

| Endpoint | What you get |
|---|---|
| `device/list` | your bound devices (name, model, online status, roles) |
| `device/get_info` | product / device metadata |
| `device/get_property` | **live reported state** (battery, status, suction, mop, location, consumables…) — richest |
| `device/get_desired_property` | desired + reported shadow (`report[]`) |

```console
$ python3 botslab_client.py state
== Device <DEVICE_NAME> (<PRODUCT_KEY>) ==
  BatteryLevel          : 100
  TEST_WorkingStatus    : 7
  AutoBoost             : False
  MoppingPadInstalled   : False
  VolumeLevel           : 30
  CurrentLocation       : [122,-2525,80]
  ...
  total properties reported: 40
```

---

## Quick start

```bash
git clone <this repo>
cd botslab-cloud
cp .env.example .env         # then fill it in — see docs/SESSION-CAPTURE.md
python3 botslab_client.py state
```

No dependencies required (Python 3.9+). `certifi` is used automatically if present.

All configuration — secrets, device ids and the 360 session — comes from the
environment / `.env`. **Nothing sensitive is committed to this repo.**

---

## How it works (short version)

The Botslab S8 is a cloud-shadow device. The app (a Flutter app,
`com.visualworld.cloudsmart`) talks to `*-sapp-api.botslab.com` over HTTPS.
Device endpoints live under `/v1/iot/device/*` and are served by the
"cloudspirit" layer. Each request needs three things:

1. **Signed query params** — `sign = md5(appkey + m2 + sign_ts + sign_no + secret)`,
   with `sign_ts` in unix seconds and `sign_no` a time-based UUID (v1) hex.
2. **An authorization header** — `authorization: jws <base64(JSON{Q, T, sid})>`,
   where `Q`/`T` are 360 account cookies and **`sid`** is the cloudspirit
   session id. The **`sid` is mandatory** — with only `Q`/`T` the gateway
   returns `code 1015 "Bad request"`.
3. **Client-info params** — `ci_*`, `appch=fwq`, `ci_brand=google`,
   `skillflag=online` (the dio/device layer's values).

Full details, the error-code ladder, and the whole reverse-engineering journey
are in [`docs/`](docs/):

- [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — endpoints, signing, auth, TSL vocabulary.
- [`docs/SESSION-CAPTURE.md`](docs/SESSION-CAPTURE.md) — how to obtain `Q` / `T` / `sid`.
- [`docs/REVERSE-ENGINEERING.md`](docs/REVERSE-ENGINEERING.md) — the full write-up.

---

## Roadmap

- [x] Sign algorithm + full param set
- [x] Auth token `jws{Q, T, sid}`
- [x] Read: `device/list`, `get_info`, `get_property`, `get_desired_property`
- [ ] `product/get_dsl` (TSL model) — needs different params (returns `1004` today)
- [ ] Control: `invoke_service` / `set_property` (named TSL services)
- [ ] Session refresh (automate the 360 login, or a Home-Assistant-style reauth prompt)
- [ ] Home Assistant custom component (coordinator + vacuum entity)

---

## Related work

[`juetthei/ha-360-vacuum`](https://github.com/juetthei/ha-360-vacuum) (May 2026)
is a Home Assistant component for **360 AI CleanRobot S6** that talks to the
**legacy** `q.smart.360.cn` cloud with cookie auth (`q`/`qid`/`sid`) and numeric
`infoType` commands. The **Botslab S8 uses a different backend** —
`*-sapp-api.botslab.com` with the `jws{Q,T,sid}` header, MD5 `sign`, and
**named TSL services** — so that integration does not work for the S8 as-is.
It is, however, an excellent structural template (coordinator, config flow,
reauth-on-expiry), and it independently confirms that the 360 `sid` is the key
credential and that there is no public automated-login path (both projects
capture the session manually).

---

## Security / privacy

- Never commit your `.env`, captures (`*.pcap`/`*.pcapng`/`*.keylog`) or app
  binaries — all are covered by `.gitignore`.
- The 360 session (`Q`/`T`/`sid`) grants access to your account; treat it like a
  password and rotate it (log out / re-capture) if it leaks.

## License

TBD (private for now).
