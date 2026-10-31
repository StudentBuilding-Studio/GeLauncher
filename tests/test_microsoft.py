"""微软登录链路：用假的 _request 跑完整流程，不打真实网络。"""

from __future__ import annotations

import json

import pytest

from gelauncher.core import microsoft


class FakeTransport:
    """按 URL 关键字返回预置响应，并记录调用。"""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None, dict | None]] = []
        self.responses: list[tuple[int, dict]] = []

    def push(self, status: int, payload: dict) -> None:
        self.responses.append((status, payload))

    def __call__(self, method, url, *, form=None, json_body=None, headers=None, timeout=30.0):
        self.calls.append((method, url, form, json_body))
        if not self.responses:
            raise AssertionError(f"没有预置响应了：{url}")
        return self.responses.pop(0)


@pytest.fixture
def transport(monkeypatch):
    fake = FakeTransport()
    monkeypatch.setattr(microsoft, "_request", fake)
    return fake


def test_authority_goes_into_the_url(transport):
    transport.push(
        200,
        {
            "device_code": "DEV",
            "user_code": "CODE",
            "verification_uri": "https://x",
            "expires_in": 900,
            "interval": 5,
        },
    )
    device = microsoft.start_device_flow("cid", "common")
    assert device.authority == "common"
    assert "/common/oauth2/v2.0/devicecode" in transport.calls[-1][1]

    # 轮询要用同一个 authority
    transport.push(200, {"access_token": "A", "refresh_token": "R", "expires_in": 10})
    microsoft.poll_device_token("cid", device, sleep=lambda _s: None)
    assert "/common/oauth2/v2.0/token" in transport.calls[-1][1]


def test_effective_client_id_precedence(monkeypatch):
    monkeypatch.delenv(microsoft.CLIENT_ID_ENV, raising=False)
    monkeypatch.setattr(microsoft, "DEFAULT_CLIENT_ID", "builtin-id")

    # 用户配置最优先
    assert microsoft.effective_client_id("user-id") == "user-id"
    # 没配置时用打包内置的
    assert microsoft.effective_client_id("") == "builtin-id"
    # 环境变量可以覆盖内置（打包/调试用）
    monkeypatch.setenv(microsoft.CLIENT_ID_ENV, "env-id")
    assert microsoft.effective_client_id("") == "env-id"
    assert microsoft.effective_client_id("user-id") == "user-id"


def test_device_flow_and_poll(transport):
    transport.push(
        200,
        {
            "device_code": "DEV",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://microsoft.com/link",
            "expires_in": 900,
            "interval": 5,
        },
    )
    device = microsoft.start_device_flow("client-id")
    assert device.user_code == "ABCD-EFGH"
    assert device.interval == 5

    # 第一次 pending，第二次成功
    transport.push(400, {"error": "authorization_pending"})
    transport.push(
        200,
        {"access_token": "MSA", "refresh_token": "REFRESH", "expires_in": 3600},
    )
    statuses: list[str] = []
    token = microsoft.poll_device_token(
        "client-id", device, sleep=lambda _s: None, on_status=statuses.append
    )
    assert token.access_token == "MSA"
    assert token.refresh_token == "REFRESH"
    assert statuses  # 有"等待登录"的提示


def test_poll_handles_declined(transport):
    device = microsoft.DeviceCode("DEV", "CODE", "https://x", 900, 5)
    transport.push(400, {"error": "authorization_declined"})
    with pytest.raises(microsoft.MSAuthError) as excinfo:
        microsoft.poll_device_token("cid", device, sleep=lambda _s: None)
    assert "拒绝" in str(excinfo.value)


def test_login_chain_requires_reponses(transport):
    # login_with_msa 会依次请求 4 个接口
    transport.push(200, {"Token": "XBL", "DisplayClaims": {"xui": [{"uhs": "UH"}]}})
    transport.push(200, {"Token": "XSTS", "DisplayClaims": {"xui": [{"uhs": "UH"}]}})
    transport.push(200, {"access_token": "MC", "expires_in": 86400})
    transport.push(200, {"name": "Alex", "id": "uuid-2"})
    session = microsoft.login_with_msa(microsoft.MsaToken("MSA", "R", 0))
    assert session.name == "Alex"
    assert session.uuid == "uuid-2"


def test_xsts_error_is_translated(transport):
    # user token 成功，xsts 返回已知错误码
    transport.push(200, {"Token": "XBL", "DisplayClaims": {"xui": [{"uhs": "UH"}]}})
    transport.push(400, {"XErr": 2148916233})
    with pytest.raises(microsoft.MSAuthError) as excinfo:
        microsoft.login_with_msa(microsoft.MsaToken("MSA", "R", 0))
    assert "Xbox 档案" in str(excinfo.value)


def test_minecraft_login_403_means_app_not_approved(transport):
    """Xbox 全通、Minecraft 返回 403 = client id 没获批使用 Minecraft API。"""
    transport.push(200, {"Token": "XBL", "DisplayClaims": {"xui": [{"uhs": "UH"}]}})
    transport.push(200, {"Token": "XSTS", "DisplayClaims": {"xui": [{"uhs": "UH"}]}})
    transport.push(403, {})
    with pytest.raises(microsoft.MinecraftNotApproved) as excinfo:
        microsoft.login_with_msa(microsoft.MsaToken("MSA", "R", 0))
    message = str(excinfo.value)
    assert "mce-reviewappid" in message
    assert "审批" in message


def test_missing_minecraft_entitlement(transport):
    transport.push(200, {"Token": "XBL", "DisplayClaims": {"xui": [{"uhs": "UH"}]}})
    transport.push(200, {"Token": "XSTS", "DisplayClaims": {"xui": [{"uhs": "UH"}]}})
    transport.push(200, {"access_token": "MC", "expires_in": 86400})
    transport.push(404, {})
    with pytest.raises(microsoft.MinecraftNotFound) as excinfo:
        microsoft.login_with_msa(microsoft.MsaToken("MSA", "R", 0))
    assert "没有 Java 版" in str(excinfo.value)


def test_refresh_token(transport):
    transport.push(200, {"access_token": "NEW", "expires_in": 3600})
    token = microsoft.refresh_msa_token("cid", "OLD-REFRESH")
    assert token.access_token == "NEW"
    # 微软有时不返回新的 refresh_token，要沿用旧的
    assert token.refresh_token == "OLD-REFRESH"


def test_oauth_error_hint_for_public_client(transport):
    transport.push(400, {"error": "unauthorized_client", "error_description": "bad"})
    with pytest.raises(microsoft.MSAuthError) as excinfo:
        microsoft.start_device_flow("cid")
    assert "允许公共客户端流" in str(excinfo.value)


def test_session_record_roundtrip_and_expiry():
    record = microsoft.account_record(
        name="Steve",
        uuid="uuid-1",
        refresh_token="R",
        access_token="A",
        expires_at=9_999_999_999,
    )
    assert record["type"] == "msa"
    session = microsoft.session_from_record(record)
    assert session is not None and session.name == "Steve"

    expired = dict(record, expires_at=1)
    assert microsoft.session_from_record(expired) is None
