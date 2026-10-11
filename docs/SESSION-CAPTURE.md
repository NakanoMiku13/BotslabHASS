# Capturing the 360 session (`Q` / `T` / `sid`) and your ids

The client needs, once:

| Variable | What it is | Where it comes from |
|---|---|---|
| `BOTSLAB_SIGN_SECRET` | app signing secret (constant) | decompiled APK |
| `BOTSLAB_M2` | app installation UUID | app traffic / storage |
| `BOTSLAB_PRODUCT_KEY` | device product key | `device/list` response |
| `BOTSLAB_DEVICE_NAME` | device name / serial | `device/list` response |
| `BOTSLAB_Q`, `BOTSLAB_T` | 360 account cookies | app TLS traffic |
| `BOTSLAB_SID` | cloudspirit session id | app TLS traffic |

Because the Flutter app **pins TLS** (ignores user CAs) and the device calls run
through the embedded BoringSSL in `libflutter.so`, a plain proxy is not enough.
You need the app's TLS decrypted. The approach that worked:

## Method: emulator + system CA + Flutter TLS bypass + PCAPdroid + tshark

1. **Android emulator** with the genuine app installed (an AVD image without Play
   Store works; use a matching ABI/density split set so it doesn't crash).
2. **Install a mitm CA as a *system* cert** (the Flutter app ignores user CAs):
   mount a tmpfs over `/apex/com.android.conscrypt/cacerts`, drop your CA in, and
   `nsenter` into the `zygote64` namespace so forked apps inherit it. (Enables the
   okhttp/config calls.)
3. **Bypass Flutter TLS verification** with Frida and the NViso script
   [`disable-flutter-tls-verification`](https://github.com/NVISOsecurity/disable-flutter-tls-verification)
   — it pattern-scans `libflutter.so` for `ssl_verify_peer_cert` and neutralizes
   it, so the device (dio) calls can be decrypted too. Launch with **spawn**
   (`-f`) and keep stdin open so the patch stays attached; the library can take
   ~15–20 s to load, so be patient (raise the script's retry count if needed).
4. **Capture with PCAPdroid** (+ its mitm addon). Enable TLS decryption and add a
   **decryption rule for the app**. Pcapng + "dump extensions" makes it emit the
   TLS keys.
5. **Open the device** in the app so it issues `device/list` /
   `get_desired_property`. **Stop the capture from the PCAPdroid UI** — this writes
   a `.keylog` sidecar next to the `.pcap`.
6. **Decrypt with tshark** and read the request:

   ```bash
   tshark -r capture.pcap -o tls.keylog_file:capture.keylog \
          -Y 'http2.header.name==":path"' \
          -T fields -e http2.header.value | grep iot/device
   ```

   The `authorization` header decodes (base64) to `{"Q":…,"T":…,"sid":…}` — copy
   those three values. The `:path` query string gives you `m2`, `product_key`,
   `device_name` (and confirms the `sign`/`sign_ts`/`sign_no` shape).

> Tip: `Q`/`T` may also be readable from the app's cookie store, and `sid`/`qid`
> from its shared-prefs, via `adb` + root — sometimes simpler than full mitm.
> (The related project `juetthei/ha-360-vacuum` extracts `qid`/`sid` purely over
> ADB for the `q.smart.360.cn` cloud.)

## Session lifetime & refresh

`Q`/`T`/`sid` come from a 360 login and **expire**. When they do, device calls
start failing (`100003` for the token, or a deeper error). Re-capture, or — the
proper long-term fix — automate the 360 login against
`na1-sapp-login.botslab.com/request.php` to mint fresh `Q`/`T`/`sid`
programmatically. Until then, treat it like `ha-360-vacuum` does: a
"re-authenticate: paste a fresh session" step.

## Filling in `.env`

```bash
cp .env.example .env
# edit .env with the six+ values above
python3 botslab_client.py state
```
