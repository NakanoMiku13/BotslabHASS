# Botslab cloud protocol (reverse-engineered)

All values shown as `<PLACEHOLDER>` are account/device specific or secret and are
**not** included in this repo. See [`SESSION-CAPTURE.md`](SESSION-CAPTURE.md).

## Hosts

| Host | Purpose |
|---|---|
| `na1-sapp-api.botslab.com` | main API + **device API** (`/v1/iot/*`). Regions: `na1`, `ap1`, `eu1`, `eu2`. |
| `na1-sapp-login.botslab.com` | 360 account login (`/request.php`) |
| `na1-fastconn-api.botslab.com` | device fast-connect / pairing |
| `na1-video-iotext.botslab.com` | camera / video SDK (separate app-layer crypto) |

The app is Flutter (`com.visualworld.cloudsmart`). Device endpoints are driven by
the **"cloudspirit"** Dart layer over `dio`; config/ads use an okhttp layer.
Both speak to the same host. HTTP/1.1 and HTTP/2 both work for the client.

## Request signing

Every signed request carries these query params:

```
appkey   = botslabadr                 # public app id
m2       = <M2>                        # app installation UUID
sign_ts  = <unix seconds>
sign_no  = <UUID v1 hex, 32 chars>    # time-based UUID, hyphens stripped
sign     = md5(appkey + m2 + sign_ts + sign_no + secret).hexdigest().lower()
```

- `secret` is a **constant hard-coded in the APK** (next to the MD5 signer; in the
  decompiled app it is a string constant referenced by the signing class). It is
  app-global (same for every install) but is **not shipped here** — extract it
  yourself and put it in `BOTSLAB_SIGN_SECRET`.
- `sign_no` must be a UUID **v1** hex (the server validates/accepts this form).
  A random hex string is rejected.
- `sign_ts` is **unix seconds**. Milliseconds → `code 1016 "Phone time invalid"`.

### Client-info params (`ci_*`)

Sent on every request; describe the phone. Not secret. The **device/dio** layer uses:

```
ci_net=WIFI  ci_model=sdk_gphone64_arm64  ci_lang=en-US  ci_cy=US
ci_brand=google  ci_osver=35  ci_tz=UTC-6  appver=2.31.2
appch=fwq  app_type_id=1  appflag=online  skillflag=online
```

(The okhttp/config layer differs slightly: `appch=null`, `ci_brand=Google`, no `skillflag`.)

## Authorization header

```
authorization: jws <base64( JSON{"Q": <Q>, "T": <T>, "sid": <SID>} )>
```

- `Q`, `T` — 360 account session cookies, **URL-encoded** exactly as the app stores
  them. `Q` carries `qid`, `T` carries `sk` (session key), timestamps, etc.
- **`sid`** — cloudspirit session id. **Mandatory** for `/v1/iot/*` and `/v1/user/*`.
  Format: `botslab_<qid>_<login_ts>_<m2>_<hash32>-<nodeid>`.
- JSON is compact (`separators=(",",":")`), then base64 (standard, with padding),
  prefixed by `jws ` (lowercase, trailing space).

## Error-code ladder (device gateway)

The gateway validates in this order; the first failure wins:

| code | meaning | cause |
|---|---|---|
| `100003` | Login verification failed | malformed / missing `Q`/`T` token |
| `1001` | Sign error | bad or missing `sign` |
| `1016` | Phone time invalid | `sign_ts` stale or wrong unit |
| `1000` | Replay request | `sign_no` reused within the replay window |
| `1015` | **Bad request** | **missing `sid`** in the authorization token |
| `0` | Success | all good |

> The whole saga of chasing `1015` (and wrongly suspecting a TLS/HTTP-2
> fingerprint before discovering the missing `sid`) is in
> [`REVERSE-ENGINEERING.md`](REVERSE-ENGINEERING.md).

## Read endpoints (confirmed `code 0`)

```
GET /v1/iot/device/list?bind_type=0&<ci_*>&<sign fields>
GET /v1/iot/device/get_info?product_key=<PK>&device_name=<DN>&...
GET /v1/iot/device/get_property?product_key=<PK>&device_name=<DN>&...
GET /v1/iot/device/get_desired_property?product_key=<PK>&device_name=<DN>&...
```

- `get_property` → `data.result` is a **JSON string** with every live property:
  `BatteryLevel`, `AutoBoost`, `CurrentLocation`, `CleaningArea`, `ErrorCode`,
  `FilterLifeTime`, `EdgeBrushLifeTime`, `MapPkgOssInfo`, `PadWetness`, … (map
  tiles are Aliyun OSS signed URLs under `data.extend.s3_signs`).
- `get_desired_property` → `data.report[]` = `[{identifier, timestamp, value}, …]`.
- `product/get_dsl` currently returns `1004 "Illegal request"` (needs other
  params — TODO).

Other endpoints seen in captures (same host/auth): `/v1/iot/scene/gets`,
`/v1/config/products`, `/v1/push_assist/get_message`.

## Control (not yet implemented)

Commands use the **TSL (Thing Specification Language)** of Qihoo 360, dispatched
by **service name** via:

```
POST /v1/iot/device/invoke_service        (+ _async / _reply)
POST /v1/iot/device/set_property / set_desired_property
```

Known TSL service names (from the decompiled Dart): `StartDefaultClean`,
`StopClean`, `PauseRobot`, `ResumeWork`, `ReturnChargeBase`, `SeekRobot`,
`SetSuctionPowLevelWithMode`, `MopModeSwitching`, `PadWetness`,
`StartCleanInAreas`, `StartCleanInZones`, `BackToWashMop`, `Reboot`, `Mute`,
`VolumeLevel`, `ChildLock`, `AutoBoost`, `BreakClean`, `saveMap`/`deleteMap`, …

## Local control (LAN) — movement only

The robot also exposes a **local movement protocol** (UDP `8790`, plaintext JSON,
`infoType:21020`, `ctrlCode 3013`=move / `4000`=keepalive), but it must first be
**armed from the cloud** (`LANStartRemoteControl`). It only drives movement — all
real cleaning commands go through the cloud. Full local/MQTT recon lives in the
project notes (not in this repo).
