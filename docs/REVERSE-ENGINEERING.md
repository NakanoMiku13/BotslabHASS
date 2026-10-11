# Reverse-engineering the Botslab S8 cloud API — full write-up

This is the story of how the read path was reconstructed, including the dead ends,
because the dead ends are the useful part. All account/device/secret values are
redacted.

## 1. The target

- Robot: **Botslab S8** (360 / Qihoo OEM). Cloud-shadow device: no useful inbound
  LAN ports; all real control goes through the vendor cloud.
- App: **`com.visualworld.cloudsmart`** — a **Flutter** app. The interesting logic
  (device commands, state, the TSL layer) is compiled into Dart AOT (`libapp.so`),
  not Java/Kotlin, so `jadx` alone only reveals the pairing SDK.

## 2. Static analysis

- **Blutter** on `libapp.so` (Dart 3.6.x) produced readable Dart. Findings:
  - Commands use **TSL (Thing Specification Language)**, dispatched by **service
    name** (`StartDefaultClean`, `ReturnChargeBase`, …), over a `dio` HTTP client
    in the "cloudspirit" package → `*-sapp-api.botslab.com`.
  - Reads: `/v1/iot/device/get_desired_property`, `get_property`, `device/list`.
  - The request **signer** (okhttp side) was recovered from the decompiled Java:
    `sign = md5(appkey + m2 + sign_ts + sign_no + secret)`, with constants for
    `appkey` and `secret`. Validated by reproducing real captured signatures.

The signer and endpoints were known from static analysis. **What static analysis
did not give**: the exact `authorization` token contents (built in Dart), and the
precise request shape that the device gateway accepts.

## 3. Getting the app's TLS in clear

Plain proxying fails: the Flutter app pins TLS (ignores user CAs) and the device
calls go through BoringSSL embedded in `libflutter.so`. The working recipe
(detailed in [`SESSION-CAPTURE.md`](SESSION-CAPTURE.md)):

- Android emulator + genuine app.
- mitm CA installed as a **system** cert (tmpfs over the conscrypt apex, injected
  into the `zygote64` namespace) — enables okhttp/config decryption.
- **Frida + NViso `disable-flutter-tls-verification`** — pattern-scans
  `libflutter.so` for `ssl_verify_peer_cert` and patches it → enables dio/device
  decryption.
- **PCAPdroid** (VPN capture + mitm addon) with a per-app decryption rule.

## 4. The `code 1015` wall

With the session captured, a standalone client reproduced the request but the
device endpoints returned `code 1015 "Bad request"` — while `/v1/app/config`
returned `code 0` from the *same* client. A long isolation effort followed.

What was **ruled out** by sending the request from an off-device client and
reading the error code:

- **Params**: replicated the real capture exactly — `appch=fwq`, `ci_brand=google`
  (lowercase), **`skillflag=online`**, `bind_type=0`, UUID-v1 `sign_no`. Still 1015.
- **Sign**: a garbage sign returns `1001 "Sign error"`; ours returned `1015`, so
  the sign was **accepted** (the `md5(appkey+m2+ts+no+secret)` formula is correct).
- **Time**: a stale `sign_ts` returns `1016`; a milliseconds `sign_ts` also `1016`
  → `sign_ts` is unix **seconds** and ours passed.
- **Replay**: reused `sign_no` returns `1000`; ours was unique.
- **Transport / fingerprint**: tried HTTP/1.1 and HTTP/2, `curl_cffi` browser
  impersonation, and even the app's **exact JA3** — still `1015`. And crucially
  `/v1/app/config` worked from the identical client, so TLS/H2 fingerprint was not
  the gate.

This produced the full **error-code ladder** (token → sign → time → replay → ???)
and pinned `1015` as the *last* gate, reached only once everything else is valid.

## 5. The missing field: `sid`

The only way left was to see a *byte-exact* successful request. Re-running the
capture and **stopping PCAPdroid from its UI** wrote a `.keylog`; feeding that to
`tshark` decrypted the whole HTTP/2 stream. Decoding the `authorization` header
(base64) revealed it was **not** `{"Q","T"}` as assumed, but:

```json
{"Q": "...", "T": "...", "sid": "botslab_<qid>_<login_ts>_<m2>_<hash>-<nodeid>"}
```

The client had been omitting **`sid`**. Adding it → **`code 0`**. The entire
"fingerprint" theory was a red herring; `1015` was simply the gateway rejecting a
token without a session id on the cloudspirit endpoints.

## 6. Result

A pure-stdlib client reads the device shadow off-device:
`device/list`, `get_info`, `get_property` (richest live state) and
`get_desired_property` all return `code 0`. See [`PROTOCOL.md`](PROTOCOL.md).

## 7. Lessons

- **Capture the ground truth early.** Days of off-device guessing were collapsed
  by one decrypted request. The `.keylog` + `tshark` path (vs reading a proxy UI)
  was what finally showed *all* fields without loss.
- **Error codes are a map.** The 360 gateway returns a distinct code per
  validation stage; walking the ladder told us exactly what was and wasn't wrong.
- **`sid` is central to 360 auth.** Independently, `juetthei/ha-360-vacuum` (a
  different 360 cloud, `q.smart.360.cn`) also authenticates with `qid`+`sid` — and
  also captures them manually. Automating the 360 login remains the open problem
  for hands-off operation.

## 8. Tooling used

Android emulator, Frida (+ NViso Flutter TLS bypass), PCAPdroid (+ mitm addon),
Wireshark/tshark, Blutter, objdump, Python (stdlib + optional `curl_cffi` for the
fingerprint tests).
