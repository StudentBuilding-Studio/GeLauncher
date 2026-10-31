"""微软账号登录（设备代码流）→ Xbox Live → Minecraft。

链路（每一步都是公开的官方接口）：
    MSA device code / token
      → user.auth.xboxlive.com      （拿 Xbox user token + uhs）
      → xsts.auth.xboxlive.com      （拿 XSTS token，RelyingParty 是 Minecraft）
      → api.minecraftservices.com   （login_with_xbox → Minecraft access token）
      → api.minecraftservices.com/minecraft/profile（拿名字和 uuid，顺带判断有没有买游戏）

设备代码流的好处：不需要本地端口，界面上显示一串代码，用户在浏览器里输入即可。
代价：要在 Azure 里注册一个"公共客户端"应用（见 docs/msa-setup.md）。

所有网络请求都走 :func:`_request`，测试里替换它就能完整跑通流程。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

DEVICE_CODE_PATH = "/oauth2/v2.0/devicecode"
TOKEN_PATH = "/oauth2/v2.0/token"
#: 登录端点。个人账号用 consumers；组织租户（Power Apps / 学生 / 朋友的 Azure）用 common。
AUTHORITIES = ("consumers", "common")
DEFAULT_AUTHORITY = "consumers"
SCOPE = "XboxLive.signin offline_access"

XBL_USER_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"

USER_AGENT = "gelauncher/0.1"

#: XSTS 常见错误码 → 人话
XSTS_ERRORS = {
    2148916233: "这个微软账号还没有 Xbox 档案。先打开 https://www.xbox.com 随便登录一次，再回来重试。",
    2148916235: "Xbox Live 在你所在的地区不可用。",
    2148916238: "未成年账号：需要家长在 Microsoft Family 里批准后才能登录。",
    2148916227: "这个微软账号被 Xbox Live 封禁了。",
}


class MSAuthError(RuntimeError):
    """登录失败，message 是给人看的。"""


class MinecraftNotFound(MSAuthError):
    """登录成功，但这个账号没有 Java 版 Minecraft。"""


@dataclass(frozen=True)
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int
    message: str = ""
    authority: str = DEFAULT_AUTHORITY


def _url(authority: str, path: str) -> str:
    authority = authority or DEFAULT_AUTHORITY
    return f"https://login.microsoftonline.com/{authority}{path}"


@dataclass(frozen=True)
class MsaToken:
    access_token: str
    refresh_token: str
    expires_at: float

    def expired(self, margin: float = 300) -> bool:
        return time.time() >= self.expires_at - margin


@dataclass(frozen=True)
class XboxToken:
    token: str
    user_hash: str


@dataclass(frozen=True)
class MinecraftSession:
    access_token: str
    expires_at: float
    name: str
    uuid: str


# --------------------------------------------------------------------------- #
# 传输层
# --------------------------------------------------------------------------- #


def _request(
    method: str,
    url: str,
    *,
    form: dict | None = None,
    json_body: dict | None = None,
    headers: dict | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict]:
    """返回 (status, json)。4xx/5xx 不抛异常，交给调用方看 body 里的 error。"""
    data: bytes | None = None
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif json_body is not None:
        data = json.dumps(json_body).encode()
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read()
    except urllib.error.URLError as exc:
        raise MSAuthError(f"连不上微软服务器：{exc.reason}") from exc

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except json.JSONDecodeError:
        payload = {}
    return status, payload


# --------------------------------------------------------------------------- #
# MSA
# --------------------------------------------------------------------------- #


def start_device_flow(client_id: str, authority: str = DEFAULT_AUTHORITY) -> DeviceCode:
    if not client_id:
        raise MSAuthError("还没配置微软 client id（见 docs/msa-setup.md）")
    status, payload = _request(
        "POST",
        _url(authority, DEVICE_CODE_PATH),
        form={"client_id": client_id, "scope": SCOPE},
    )
    if status >= 400 or "device_code" not in payload:
        raise MSAuthError(_describe_oauth_error(payload, status))
    return DeviceCode(
        device_code=payload["device_code"],
        user_code=payload.get("user_code", ""),
        verification_uri=payload.get("verification_uri", "https://microsoft.com/link"),
        expires_in=int(payload.get("expires_in", 900)),
        interval=int(payload.get("interval", 5)),
        message=payload.get("message", ""),
        authority=authority,
    )


def poll_device_token(
    client_id: str,
    device: DeviceCode,
    *,
    sleep: Callable[[float], None] = time.sleep,
    on_status: Callable[[str], None] | None = None,
    now: Callable[[], float] = time.time,
) -> MsaToken:
    """轮询直到用户完成登录。``authorization_pending`` 是正常状态。"""
    interval = max(1, device.interval)
    deadline = now() + device.expires_in
    token_url = _url(device.authority, TOKEN_PATH)
    while now() < deadline:
        status, payload = _request(
            "POST",
            token_url,
            form={
                "client_id": client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device.device_code,
            },
        )
        error = payload.get("error")
        if status < 400 and "access_token" in payload:
            return _token_from_payload(payload)
        if error == "authorization_pending":
            if on_status is not None:
                on_status("等待你在浏览器里完成登录…")
            sleep(interval)
            continue
        if error == "slow_down":
            interval += 5
            sleep(interval)
            continue
        if error == "expired_token":
            raise MSAuthError("设备代码过期了，重新开始登录。")
        if error == "authorization_declined":
            raise MSAuthError("你在浏览器里拒绝了登录请求。")
        raise MSAuthError(_describe_oauth_error(payload, status))
    raise MSAuthError("登录超时，重新开始登录。")


def refresh_msa_token(
    client_id: str, refresh_token: str, authority: str = DEFAULT_AUTHORITY
) -> MsaToken:
    status, payload = _request(
        "POST",
        _url(authority, TOKEN_PATH),
        form={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": SCOPE,
        },
    )
    if status >= 400 or "access_token" not in payload:
        raise MSAuthError("微软登录已过期，需要重新登录：" + _describe_oauth_error(payload, status))
    return _token_from_payload(payload, fallback_refresh=refresh_token)


def _token_from_payload(payload: dict, fallback_refresh: str = "") -> MsaToken:
    expires_in = int(payload.get("expires_in", 3600))
    now = time.time()
    return MsaToken(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token") or fallback_refresh,
        expires_at=now + expires_in,
    )


def _describe_oauth_error(payload: dict, status: int) -> str:
    error = payload.get("error") or f"HTTP {status}"
    description = payload.get("error_description") or payload.get("message") or ""
    hint = ""
    if error in ("unauthorized_client", "invalid_client") or "AADSTS700016" in description:
        hint = "（检查 client id 是否填对，以及 Azure 里有没有勾选「允许公共客户端流」）"
    elif "AADSTS50020" in description or "AADSTS50059" in description:
        hint = "（应用要注册成「仅个人 Microsoft 帐户」）"
    return f"{error}: {description}{hint}".strip()


# --------------------------------------------------------------------------- #
# Xbox Live
# --------------------------------------------------------------------------- #


def xbox_user_token(msa_access_token: str) -> XboxToken:
    status, payload = _request(
        "POST",
        XBL_USER_URL,
        json_body={
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={msa_access_token}",
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT",
        },
        headers={"x-xbl-contract-version": "1"},
    )
    if status >= 400:
        raise MSAuthError(f"Xbox Live 登录失败（HTTP {status}）")
    return _xbox_token_from(payload)


def xsts_token(user_token: XboxToken) -> XboxToken:
    status, payload = _request(
        "POST",
        XSTS_URL,
        json_body={
            "Properties": {"SandboxId": "RETAIL", "UserTokens": [user_token.token]},
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        },
        headers={"x-xbl-contract-version": "1"},
    )
    if status >= 400:
        code = payload.get("XErr")
        if code in XSTS_ERRORS:
            raise MSAuthError(XSTS_ERRORS[code])
        raise MSAuthError(f"XSTS 授权失败（HTTP {status}，XErr={code}）")
    return _xbox_token_from(payload)


def _xbox_token_from(payload: dict) -> XboxToken:
    token = payload.get("Token")
    claims = (payload.get("DisplayClaims") or {}).get("xui") or [{}]
    user_hash = claims[0].get("uhs", "")
    if not token or not user_hash:
        raise MSAuthError("Xbox 返回的数据缺少 Token/uhs")
    return XboxToken(token=token, user_hash=user_hash)


# --------------------------------------------------------------------------- #
# Minecraft
# --------------------------------------------------------------------------- #


def minecraft_session(xsts: XboxToken) -> tuple[str, float]:
    status, payload = _request(
        "POST",
        MC_LOGIN_URL,
        json_body={"identityToken": f"XBL3.0 x={xsts.user_hash};{xsts.token}"},
    )
    if status >= 400 or "access_token" not in payload:
        raise MSAuthError(f"Minecraft 登录失败（HTTP {status}）")
    expires_in = int(payload.get("expires_in", 86400))
    return payload["access_token"], time.time() + expires_in


def minecraft_profile(access_token: str) -> tuple[str, str]:
    status, payload = _request(
        "GET", MC_PROFILE_URL, headers={"Authorization": f"Bearer {access_token}"}
    )
    if status == 404:
        raise MinecraftNotFound("这个微软账号没有 Java 版 Minecraft（或者没在 Xbox 上创建档案）。")
    if status >= 400:
        raise MSAuthError(f"读取 Minecraft 档案失败（HTTP {status}）")
    name = payload.get("name")
    uuid = payload.get("id")
    if not name or not uuid:
        raise MSAuthError("Minecraft 档案缺少 name/id")
    return name, uuid


def login_with_msa(msa: MsaToken) -> MinecraftSession:
    """把 MSA token 换成可启动游戏的 Minecraft 会话。"""
    user = xbox_user_token(msa.access_token)
    xsts = xsts_token(user)
    mc_token, expires_at = minecraft_session(xsts)
    name, uuid = minecraft_profile(mc_token)
    return MinecraftSession(
        access_token=mc_token, expires_at=expires_at, name=name, uuid=uuid
    )


# --------------------------------------------------------------------------- #
# 保存到配置里的账号
# --------------------------------------------------------------------------- #


def account_record(
    *,
    name: str,
    uuid: str,
    refresh_token: str,
    access_token: str = "",
    expires_at: float = 0.0,
    xuid: str = "",
    authority: str = DEFAULT_AUTHORITY,
) -> dict:
    return {
        "type": "msa",
        "name": name,
        "uuid": uuid,
        "refresh_token": refresh_token,
        "access_token": access_token,
        "expires_at": expires_at,
        "xuid": xuid,
        "authority": authority,
    }


def session_from_record(record: dict) -> MinecraftSession | None:
    """从配置里恢复一个还能用的 Minecraft 会话（没过期的话）。"""
    access_token = record.get("access_token") or ""
    expires_at = float(record.get("expires_at") or 0)
    name = record.get("name") or ""
    uuid = record.get("uuid") or ""
    if access_token and name and uuid and time.time() < expires_at - 300:
        return MinecraftSession(
            access_token=access_token, expires_at=expires_at, name=name, uuid=uuid
        )
    return None
