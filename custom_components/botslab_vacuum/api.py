"""Async Botslab cloud API client (read path; control added once mapped).

Mirrors the proven request logic from the standalone ``botslab_client.py``:
signed query params + ``authorization: jws base64({Q,T,sid})``.

No secrets here — all config is passed in by the caller (config entry).
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass

import aiohttp


class BotslabError(Exception):
    """Generic API error."""


class BotslabAuthError(BotslabError):
    """Session invalid/expired/evicted -> triggers automatic re-login."""


# Cloud response codes that mean "the session is no longer usable" and should
# trigger a re-login: 100003 login-verification failed, 1015 missing/invalid
# session (sid), 102003 "logged in on another device" (session evicted).
AUTH_FAIL_CODES = {100003, 1015, 102003}


@dataclass
class BotslabSession:
    """Everything needed to sign + authenticate a request."""

    q: str
    t: str
    sid: str
    m2: str
    product_key: str
    device_name: str
    sign_secret: str
    region: str = "na1"
    appkey: str = "botslabadr"
    appver: str = "2.31.2"

    @property
    def host(self) -> str:
        return f"{self.region}-sapp-api.botslab.com"


# Client-info params for the device/dio layer.
_CI = {
    "ci_net": "WIFI",
    "ci_model": "sdk_gphone64_arm64",
    "ci_lang": "en-US",
    "ci_cy": "US",
    "ci_brand": "google",
    "ci_osver": "35",
    "ci_tz": "UTC-6",
    "appch": "fwq",
    "app_type_id": "1",
    "appflag": "online",
    "skillflag": "online",
}


class BotslabApi:
    def __init__(self, session: aiohttp.ClientSession, cfg: BotslabSession) -> None:
        self._session = session
        self._cfg = cfg

    # -- signing / auth ---------------------------------------------------- #
    def _sign_fields(self) -> dict:
        ts = str(int(time.time()))
        no = uuid.uuid1().hex
        raw = self._cfg.appkey + self._cfg.m2 + ts + no + self._cfg.sign_secret
        return {
            "appkey": self._cfg.appkey,
            "m2": self._cfg.m2,
            "sign_ts": ts,
            "sign_no": no,
            "sign": hashlib.md5(raw.encode()).hexdigest().lower(),
        }

    def _params(self, extra: dict | None = None) -> dict:
        p = dict(_CI)
        p["appver"] = self._cfg.appver
        p.update(self._sign_fields())
        if extra:
            p.update(extra)
        return p

    def _auth(self) -> str:
        payload = {"Q": self._cfg.q, "T": self._cfg.t, "sid": self._cfg.sid}
        return "jws " + base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()

    # -- transport --------------------------------------------------------- #
    async def _get(self, path: str, extra: dict | None = None) -> dict:
        url = f"https://{self._cfg.host}{path}"
        headers = {"authorization": self._auth(), "User-Agent": "okhttp/4.2.0"}
        try:
            async with self._session.get(
                url, params=self._params(extra), headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise BotslabError(f"network error on {path}: {exc}") from exc
        code = data.get("code")
        if code == 0:
            return data.get("data", {})
        if code in AUTH_FAIL_CODES:
            raise BotslabAuthError(f"{path}: code {code} {data.get('msg')}")
        raise BotslabError(f"{path}: code {code} {data.get('msg')}")

    # -- reads ------------------------------------------------------------- #
    async def device_list(self) -> list[dict]:
        data = await self._get("/v1/iot/device/list", {"bind_type": "0"})
        return data.get("devices", [])

    async def get_info(self) -> dict:
        return await self._get(
            "/v1/iot/device/get_info",
            {"product_key": self._cfg.product_key, "device_name": self._cfg.device_name},
        )

    async def get_property(self) -> dict:
        """Live reported state (data.result is a JSON string of identifier->value)."""
        data = await self._get(
            "/v1/iot/device/get_property",
            {"product_key": self._cfg.product_key, "device_name": self._cfg.device_name},
        )
        result = data.get("result")
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return {}
        return result or {}

    async def get_desired_property(self) -> dict:
        data = await self._get(
            "/v1/iot/device/get_desired_property",
            {"product_key": self._cfg.product_key, "device_name": self._cfg.device_name},
        )
        out: dict = {}
        for item in data.get("report", []):
            out[item.get("identifier")] = item.get("value")
        return out

    async def get_dsl(self) -> dict:
        """Product TSL model (services/Attributes/Events). Needs device_name."""
        return await self._get(
            "/v1/iot/product/get_dsl",
            {"product_key": self._cfg.product_key, "device_name": self._cfg.device_name},
        )

    # -- map --------------------------------------------------------------- #
    async def get_property_full(self) -> dict:
        """Full get_property data: {result: "<json>", extend: {s3_signs}, ...}."""
        return await self._get(
            "/v1/iot/device/get_property",
            {"product_key": self._cfg.product_key, "device_name": self._cfg.device_name},
        )

    async def get_map_bin(self) -> tuple[str | None, str | None]:
        """Resolve + download the current map package. Returns (bin_text, tag).

        The robot uploads the map to Aliyun OSS; `get_property` gives the
        aliyun:// URI (in MapPkgOssInfo / RecordedMapList) and `extend.s3_signs`
        maps it to a time-limited signed HTTPS URL we can fetch directly.
        """
        data = await self.get_property_full()
        try:
            props = json.loads(data.get("result") or "{}")
        except (json.JSONDecodeError, TypeError):
            props = {}
        s3 = (data.get("extend") or {}).get("s3_signs") or {}
        if not s3:
            return None, None

        def _obj(v):
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except json.JSONDecodeError:
                    return {}
            return v or {}

        uri = tag = None
        mi = _obj(props.get("MapPkgOssInfo"))
        if mi.get("Url") and mi["Url"] in s3:
            uri, tag = mi["Url"], mi.get("UploadID") or mi.get("CleanID")
        if uri is None:  # fall back to a permanent saved map
            rl = _obj(props.get("RecordedMapList"))
            for r in rl.get("RecordedMaps", []):
                u = r.get("PermBinSaveUrl")
                if u and u in s3:
                    uri, tag = u, r.get("FileID") or r.get("BinFileMd5")
                    break
        signed = s3.get(uri) if uri else None
        if not signed:
            return None, None
        try:
            async with self._session.get(
                signed, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                text = await resp.text()
        except aiohttp.ClientError as exc:
            raise BotslabError(f"map download failed: {exc}") from exc
        return text, tag

    # -- control ----------------------------------------------------------- #
    async def _post(self, path: str, body: dict) -> dict:
        url = f"https://{self._cfg.host}{path}"
        headers = {
            "authorization": self._auth(),
            "User-Agent": "okhttp/4.2.0",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            async with self._session.post(
                url, params=self._params(), data=body, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise BotslabError(f"network error on {path}: {exc}") from exc
        code = data.get("code")
        if code == 0:
            return data.get("data", {})
        if code in AUTH_FAIL_CODES:
            raise BotslabAuthError(f"{path}: code {code} {data.get('msg')}")
        raise BotslabError(f"{path}: code {code} {data.get('msg')}")

    async def invoke_service(self, identifier: str, input_args: dict | None = None) -> dict:
        """POST /invoke_service — identifier = TSL service name, input = JSON string."""
        return await self._post(
            "/v1/iot/device/invoke_service",
            {
                "product_key": self._cfg.product_key,
                "device_name": self._cfg.device_name,
                "identifier": identifier,
                "input": json.dumps(input_args or {}, separators=(",", ":")),
            },
        )

    # -- scheduling + zones ------------------------------------------------ #
    async def get_clean_policy(self) -> dict:
        """Return the scheduled-clean policy: {timeZone, value: [entries]}."""
        data = await self.invoke_service("GetCleanPolicyList")
        outer = json.loads(data.get("result") or "{}")
        cpl = outer.get("CleanPolicyList")
        if isinstance(cpl, str):
            return json.loads(cpl) if cpl else {}
        return cpl or {}

    async def set_clean_policy(self, policy: dict) -> dict:
        """Write the full scheduled-clean policy (SetCleanPolicyList)."""
        return await self.invoke_service(
            "SetCleanPolicyList",
            {"CleanPolicyList": json.dumps(policy, separators=(",", ":"))},
        )

    async def set_zones(self, zones: list[dict], map_id: int | None = None) -> dict:
        args = {"ZonesList": json.dumps({"zones": zones}, separators=(",", ":"))}
        if map_id is not None:
            args["MapID"] = int(map_id)
        return await self.invoke_service("SetZones", args)

    async def start_clean_in_zones(self) -> dict:
        return await self.invoke_service("StartCleanInZones")

    async def clean_zones(self, zones: list[dict], map_id: int | None = None) -> dict:
        """Configure one-off cleaning zones then start cleaning them."""
        await self.set_zones(zones, map_id)
        return await self.start_clean_in_zones()

    async def set_fbd_places(self, map_id: int, places: list[dict]) -> dict:
        """Set/replace the forbidden (no-go) zones (inline, no OSS upload).

        FbdPlacesList wire shape mirrors the map's ``area[]`` items; confirm
        against a live capture before relying on it.
        """
        return await self.invoke_service(
            "SetFbdPlaces",
            {"MapID": int(map_id),
             "FbdPlacesList": json.dumps(places, separators=(",", ":"))},
        )

    async def get_clean_track(self, start_idx: int = 0) -> dict:
        """One page of the live cleaning track (keys are lowercase: posArray...)."""
        data = await self.invoke_service(
            "GetCleanTrackWithPosIdx", {"StartPosIndex": int(start_idx)}
        )
        raw = data.get("result")
        outer = json.loads(raw) if isinstance(raw, str) else (raw or {})
        tk = outer.get("Track", outer)
        return json.loads(tk) if isinstance(tk, str) else (tk or {})

    async def get_clean_track_all(self, max_points: int = 20000) -> list[list[int]]:
        """Paginate the whole cleaning track -> concatenated [[x,y], ...] (mm)."""
        pts: list[list[int]] = []
        idx = 0
        while len(pts) < max_points:
            tk = await self.get_clean_track(idx)
            seg = tk.get("posArray") or tk.get("PosArray") or []
            if not seg:
                break
            pts.extend(seg)
            total = int(tk.get("totalPoints") or tk.get("TotalPoints") or len(pts))
            idx = len(pts)
            if idx >= total:
                break
        return pts

    async def set_desired_property(self, identifier: str, value) -> dict:
        """POST /set_desired_property — items = JSON string {identifier: value}."""
        return await self._post(
            "/v1/iot/device/set_desired_property",
            {
                "product_key": self._cfg.product_key,
                "device_name": self._cfg.device_name,
                "items": json.dumps({identifier: value}, separators=(",", ":")),
            },
        )
