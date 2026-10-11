# Control reference (authoritative — from the product DSL)

Fetched via `GET /v1/iot/product/get_dsl?product_key=…&device_name=…`. The DSL has
`services` (invoke_service targets), `Attributes` (properties), and `Events`.

## Transport

- **invoke_service** — `POST /v1/iot/device/invoke_service`, form-urlencoded body:
  `product_key`, `device_name`, `identifier` (service name), `input` = `json.dumps(args)`.
  Response: `{code:0, data:{message_id, result}}` where `result` is a JSON string.
- **set a property** — `POST /v1/iot/device/set_desired_property`, body:
  `product_key`, `device_name`, `items` = `json.dumps({Identifier: value})`.
  (`/set_property` exists too; the app uses `set_desired_property` for user settings.)
- Both carry the same signed query params + `authorization: jws{Q,T,sid}` as reads.

## Command services (verified signatures)

| Action | identifier | input |
|---|---|---|
| Start (full clean) | `StartDefaultClean` | `{"CleanTimes": 1}` (1–2) |
| Pause | `PauseRobot` | `{"TargetAction": 0}` (0=Cleaning,1=RetCharge,2=Others) |
| Resume | `ResumeWork` | `{}` |
| Return to dock | `ReturnChargeBase` | `{}` |
| Locate (beeps!) | `SeekRobot` | `{}` |
| Fan / suction | `SetSuctionPowLevelWithMode` | `{"Mode":0,"Level":L}` |
| Reboot | `Reboot` | `{}` |
| Zone clean | `StartCleanInZones` | (set zones first) |
| Area/room clean | `StartCleanInAreas` | `{"Url","MapID","CleanID"}` |

> There is **no `StopClean` service**. HA "stop" maps to `PauseRobot {TargetAction:2}`.

`SetSuctionPowLevelWithMode.Level`: `0=Quiet, 1=Normal, 2=Strong, 3=MAX`.
`.Mode`: `0=OnDefaultPage, 1=OnZonePage, 2=OnAreaPage, 3=OnCronTask`.

Useful **query** services (no device action — handy for testing the POST path):
`GetNetworkInfo`, `GetCleanPolicyList`.

## Key attributes (state + settings via set_desired_property)

| Identifier | Type | Notes |
|---|---|---|
| `WorkingStatus` / `TEST_WorkingStatus` | enum | 0 StandBy · 1 DefaultClean · 2 CleanInAreas · 3 CleanInZones · 4 Paused · 5 GotoChargeBase · 6 Charging · 7 ChargeDone · 8 Dormant · 9 InTrouble · 10 InRemoteControl · 11 InDustCollecting |
| `BatteryLevel` | int 0–100 | |
| `SuctionPowLevel` | int | current fan level (maps to the Level enum) |
| `ErrorCode` | int ≤ 0 | 0..-999999; negative is the normal range |
| `AutoBoost` | bool | carpet auto-boost (settable; silent — good test) |
| `MopModeSwitching` | enum | 0 DrySweep · 1 MixedMode · 2 MopMode |
| `PadWetness` | enum | 1 Low · 2 Medium · 3 High |
| `VolumeLevel` | int 0–100 | |
| `MoppingPadInstalled` / `WaterTankInstalled` | bool | |
| `CleaningArea` / `TotalCleaningArea` | int | |

## HA ↔ TSL mapping (as implemented in `vacuum.py`)

- `start` → `ResumeWork` if paused, else `StartDefaultClean {CleanTimes:1}`
- `pause` → `PauseRobot {TargetAction:0}`
- `stop` → `PauseRobot {TargetAction:2}`
- `return_to_base` → `ReturnChargeBase`
- `locate` → `SeekRobot`
- `set_fan_speed(Quiet|Normal|Strong|MAX)` → `SetSuctionPowLevelWithMode {Mode:0, Level:idx}`

All validated live via the standalone client (query services + a reversible
`AutoBoost` toggle with before/after verification). Motion/noise commands were
mapped but intentionally not fired during overnight testing.
