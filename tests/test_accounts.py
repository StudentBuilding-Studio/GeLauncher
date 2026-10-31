"""当前账号解析：离线直通，微软自动刷新。"""

from __future__ import annotations

import pytest

from gelauncher.config import Config
from gelauncher.core import accounts, microsoft
from gelauncher.core.command import offline_uuid


@pytest.fixture
def config(monkeypatch):
    cfg = Config()
    monkeypatch.setattr(Config, "save", lambda self, path=None: None)
    return cfg


def test_offline_account(config):
    account = accounts.resolve_account(config)
    assert account.name == "Player"
    assert account.is_microsoft is False
    assert account.resolved_uuid == offline_uuid("Player")


def test_msa_session_reused_when_valid(config):
    config.add_msa_account(
        {
            "name": "Steve",
            "uuid": "uuid-1",
            "refresh_token": "R",
            "access_token": "A",
            "expires_at": 9_999_999_999,
        }
    )
    account = accounts.resolve_account(config)
    assert account.is_microsoft is True
    assert account.name == "Steve"
    assert account.resolved_uuid == "uuid-1"
    assert account.access_token == "A"


def test_msa_refreshes_expired_session(config, monkeypatch):
    config.add_msa_account(
        {
            "name": "Steve",
            "uuid": "old",
            "refresh_token": "R",
            "access_token": "OLD",
            "expires_at": 1,
        }
    )
    config.msa_client_id = "cid"

    monkeypatch.setattr(
        microsoft,
        "refresh_msa_token",
        lambda client_id, refresh, authority="consumers": microsoft.MsaToken(
            "NEW", "R2", 9_999_999_999
        ),
    )
    monkeypatch.setattr(
        microsoft,
        "login_with_msa",
        lambda token: microsoft.MinecraftSession(
            access_token="MC-NEW", expires_at=9_999_999_999, name="Steve", uuid="uuid-new"
        ),
    )

    account = accounts.resolve_account(config)
    assert account.access_token == "MC-NEW"
    assert account.resolved_uuid == "uuid-new"
    stored = config.msa_account("Steve")
    assert stored["access_token"] == "MC-NEW"
    assert stored["refresh_token"] == "R2"
    assert stored["uuid"] == "uuid-new"


def test_authority_from_config_is_used_for_refresh(config, monkeypatch):
    config.add_msa_account(
        {"name": "Steve", "uuid": "u", "refresh_token": "R", "expires_at": 1}
    )
    config.msa_client_id = "cid"
    config.msa_authority = "common"
    captured = {}

    def fake_refresh(client_id, refresh, authority="consumers"):
        captured["authority"] = authority
        return microsoft.MsaToken("NEW", "R2", 9_999_999_999)

    monkeypatch.setattr(microsoft, "refresh_msa_token", fake_refresh)
    monkeypatch.setattr(
        microsoft,
        "login_with_msa",
        lambda token: microsoft.MinecraftSession(
            access_token="MC", expires_at=9_999_999_999, name="Steve", uuid="u"
        ),
    )
    accounts.resolve_account(config)
    assert captured["authority"] == "common"


def test_msa_without_client_id_is_actionable(config):
    config.add_msa_account({"name": "Steve", "uuid": "u", "refresh_token": "R", "expires_at": 1})
    with pytest.raises(microsoft.MSAuthError) as excinfo:
        accounts.resolve_account(config)
    assert "client id" in str(excinfo.value)


def test_missing_msa_record_is_actionable(config):
    config.selected_account_type = "msa"
    config.selected_account = "Ghost"
    with pytest.raises(microsoft.MSAuthError) as excinfo:
        accounts.resolve_account(config)
    assert "重新登录" in str(excinfo.value)
