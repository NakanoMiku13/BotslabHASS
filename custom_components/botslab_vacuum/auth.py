"""Headless 360 -> Botslab session bootstrap (email + password -> Q/T/sid).

Pure reimplementation of the Qihoo 360 account request crypto
(``com.qihoo360.accounts.api.BasicParamsTools``) + the Botslab
``POST /v1/app/login`` session call. Reversed and validated live; see the
repo's docs/REVERSE-ENGINEERING.md.

Chain:
  1. ``UserIntf.login(email, MD5(pwd))`` at ``<region>-sapp-login.botslab.com``
     -> Q, T   (360 account crypto: parad=DES-CBC, key=RSA-1024, sig=md5)
  2. ``POST /v1/app/login`` at ``<region>-sapp-api.botslab.com`` with
     ``authorization: jws base64({Q,T})`` + Botslab sign  -> sid

No secrets are hard-coded except app-wide constants (sign key, RSA pub key,
sign secret) that are identical for every install of the Botslab app.
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import string
import time
import uuid
from urllib.parse import quote_plus

import aiohttp

# ---- 360 account layer (request.php) constants ---------------------------- #
_FROM = "mpl_cloudsmartoem_and"
_SIGKEY = "73e5dba4"
_UA_ACCT = "360accounts andv3.2.7 " + _FROM
_RSA_PUB_B64 = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC9oNZHDXyGxNNfBhfk/+WAtjVE"
    "T1sLWQraDBLHd0821Ow4yp6p+zvHB6yXSUEt2/lLVW7Q0/RVHuxnwtg6cKYdDInq"
    "MznSLIKjXPkd6Dfft8nz8vkOdSUlzQtE3T4dvaagbH76lBGB2wuLNOV0D2UcUyvR"
    "u2puKtYjgDNm/O0apQIDAQAB"
)
_RSA_CACHE = None


def _rsa():
    """Lazily import pycryptodome + build the RSA key (so the component imports
    cleanly even before the manifest requirement finishes installing)."""
    global _RSA_CACHE
    if _RSA_CACHE is None:
        from Crypto.PublicKey import RSA
        _RSA_CACHE = RSA.import_key(base64.b64decode(_RSA_PUB_B64))
    return _RSA_CACHE


# A plausible, fixed Android device snapshot for the 360 login common params.
# The server does not device-check these for this app; values only need to be
# internally consistent and signed.
_DEVICE = {
    "os_sdk_version": "android_35",
    "quc_sdk_version": "v3.2.7",
    "mname": "",
    "ua": "Dalvik/2.1.0 (Linux; U; Android 15; sdk_gphone64_arm64 Build/AE3A.240806.043)",
    "os_manufacturer": "Google",
    "mSystemVersion": "android 15",
    "os_board": "goldfish_arm64",
    "os_model": "sdk_gphone64_arm64",
    "quc_lang": "zh_CN",
    "sh": "2400.0",
    "from": _FROM,
    "oaid": "",
    "app": "Botslab",
    "res_mode": "1",
    "sw": "1080.0",
    "format": "json",
    "qh_id": "",
    "device_os": "android",
    "device_lang": "zh-CN",
    "v": "2.31.2",
    "sdpi": "2.625",
}
_OUTER = ("device_lang", "trace_id", "quc_lang", "method", "from")


class LoginError(Exception):
    """Login/session bootstrap failed (bad credentials, captcha, etc.)."""


class CaptchaRequired(LoginError):
    """Server demands a captcha / device-check (errno 5010/5011/108)."""


def _jurl(v) -> str:
    return quote_plus(str(v), safe="*-_.")


def _des(key8: str):
    from Crypto.Cipher import DES
    k = key8.encode()
    return DES.new(k, DES.MODE_CBC, k)


def _pkcs5(b: bytes) -> bytes:
    pad = 8 - (len(b) % 8)
    return b + bytes([pad]) * pad


def _acct_sign(params: dict) -> str:
    base = "".join(f"{k}={params[k]}" for k in sorted(params)) + _SIGKEY
    return hashlib.md5(base.encode()).hexdigest()


def _build_acct_body(method: str, mid: str, androidid: str, extra: dict) -> tuple[dict, str]:
    ms = int(time.time() * 1000)
    p = dict(_DEVICE)
    p["mid"] = mid
    p["androidid"] = androidid
    p["method"] = method
    p["trace_id"] = f"src_and_1365_{ms}"
    p["vt_guid"] = str(ms + 72)
    p.update(extra)
    p["sig"] = _acct_sign(p)

    keyfull = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(117))
    key8 = keyfull[-8:]
    from Crypto.Cipher import PKCS1_v1_5
    qs = "&".join(f"{k}={_jurl(v)}" for k, v in p.items())
    parad = base64.b64encode(_des(key8).encrypt(_pkcs5(qs.encode()))).decode()
    keyfield = base64.b64encode(PKCS1_v1_5.new(_rsa()).encrypt(keyfull.encode())).decode()
    body = {k: p[k] for k in _OUTER}
    body["parad"] = parad
    body["key"] = keyfield
    return body, key8


async def _account_call(
    session: aiohttp.ClientSession, host: str, method: str, mid: str,
    androidid: str, extra: dict,
) -> dict:
    body, key8 = _build_acct_body(method, mid, androidid, extra)
    url = f"https://{host}/request.php"
    headers = {"User-Agent": _UA_ACCT, "Content-Type": "application/x-www-form-urlencoded"}
    async with session.post(
        url, data=body, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
    ) as resp:
        env = await resp.json(content_type=None)
    ret_b64 = env.get("ret")
    plain = None
    if ret_b64:
        dec = _des(key8).decrypt(base64.b64decode(ret_b64 + "=" * (-len(ret_b64) % 4)))
        plain = dec[: -dec[-1]].decode("utf-8", "replace")
    return {"env": env, "plain": plain}


def _stable_ids(m2: str) -> tuple[str, str]:
    """Derive stable mid/androidid from m2 so a given entry is consistent."""
    h = hashlib.md5(m2.encode()).hexdigest()
    return h, h[:16]


async def async_login(
    session: aiohttp.ClientSession, email: str, password: str, m2: str,
    region: str, sign_secret: str, appkey: str = "botslabadr", appver: str = "2.31.2",
) -> dict:
    """email + password -> {"q","t","sid"}. Raises LoginError/CaptchaRequired."""
    login_host = f"{region}-sapp-login.botslab.com"
    api_host = f"{region}-sapp-api.botslab.com"
    mid, androidid = _stable_ids(m2)

    # 1) UserIntf.login -> Q/T
    r = await _account_call(
        session, login_host, "UserIntf.login", mid, androidid,
        {
            "username": email,
            "password": hashlib.md5(password.encode()).hexdigest(),
            "loginType": "801",
            "fields": "qid,username,nickname,loginemail,head_pic,mobile",
            "sec_type": "bool",
            "head_type": "s",
            "is_keep_alive": "1",
        },
    )
    errno = (r["env"] or {}).get("errno")
    if errno in (5010, 5011, 108):
        raise CaptchaRequired(f"captcha/device-check required (errno {errno})")
    if errno != 0:
        raise LoginError(f"360 login failed (errno {errno}: {(r['env'] or {}).get('errmsg')})")
    try:
        user = json.loads(r["plain"]).get("user", {})
        q, t = user["q"], user["t"]
    except Exception as exc:  # noqa: BLE001
        raise LoginError(f"login ok but could not parse Q/T: {exc}") from exc

    # 2) POST /v1/app/login (jws{Q,T} + Botslab sign) -> sid
    auth = "jws " + base64.b64encode(
        json.dumps({"Q": q, "T": t}, separators=(",", ":")).encode()
    ).decode()
    ts = str(int(time.time()))
    no = uuid.uuid1().hex
    sign = hashlib.md5((appkey + m2 + ts + no + sign_secret).encode()).hexdigest().lower()
    params = {
        "ci_net": "WIFI", "ci_model": "sdk_gphone64_arm64", "ci_lang": "en-US",
        "ci_cy": "US", "ci_brand": "google", "ci_osver": "35", "ci_tz": "UTC-6",
        "appch": "fwq", "app_type_id": "1", "appflag": "online", "skillflag": "online",
        "appver": appver, "appkey": appkey, "m2": m2, "sign_ts": ts, "sign_no": no, "sign": sign,
    }
    async with session.post(
        f"https://{api_host}/v1/app/login", params=params, data=b"",
        headers={"User-Agent": "okhttp/4.2.0", "authorization": auth,
                 "Content-Type": "application/x-www-form-urlencoded"},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        data = await resp.json(content_type=None)
    if data.get("code") != 0:
        raise LoginError(f"/v1/app/login failed (code {data.get('code')}: {data.get('msg')})")
    sid = data["data"]["sid"]
    return {"q": q, "t": t, "sid": sid}
