"""把配置里的"当前账号"变成启动用的 :class:`Account`。

CLI 和 UI 都走这里，避免两边各写一套（之前 CLI 只认离线账号就是这个原因）。
微软账号会在 token 快过期时自动刷新，刷新结果写回配置。
"""

from __future__ import annotations

from . import microsoft
from .command import Account


def resolve_account(config) -> Account:
    """返回本次启动要用的账号。

    - 离线：直接用名字，UUID 由名字算；
    - 微软：优先复用配置里未过期的会话，否则用 refresh token 换新的。
    失败时抛 :class:`microsoft.MSAuthError`，由调用方转成人话。
    """
    kind = getattr(config, "selected_account_type", "offline")
    name = getattr(config, "selected_account", "") or "Player"
    if kind != "msa":
        return Account.offline(name)

    record = config.msa_account(name)
    if record is None:
        raise microsoft.MSAuthError(
            f"找不到微软账号 {name}，去「账号」页重新登录一次"
        )

    session = microsoft.session_from_record(record)
    if session is None:
        client_id = getattr(config, "msa_client_id", "") or ""
        refresh_token = str(record.get("refresh_token") or "")
        if not client_id:
            raise microsoft.MSAuthError(
                "微软账号需要 client id 才能刷新登录（设置页填一下，见 docs/微软登录准备.md）"
            )
        if not refresh_token:
            raise microsoft.MSAuthError("这个微软账号没有保存登录凭据，需要重新登录")
        token = microsoft.refresh_msa_token(
            client_id,
            refresh_token,
            authority=str(record.get("authority") or getattr(config, "msa_authority", "consumers")),
        )
        session = microsoft.login_with_msa(token)
        record.update(
            access_token=session.access_token,
            expires_at=session.expires_at,
            name=session.name,
            uuid=session.uuid,
            refresh_token=token.refresh_token,
        )
        config.add_msa_account(record)
        config.save()

    return Account.microsoft(
        name=session.name,
        uuid=session.uuid,
        access_token=session.access_token,
        xuid=str(record.get("xuid") or ""),
        client_id=getattr(config, "msa_client_id", "") or "",
    )
