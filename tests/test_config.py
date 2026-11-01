"""配置读写：目录 / Java 列表去重、实例设置回落全局。"""

from __future__ import annotations

import json
from pathlib import Path

from gelauncher.config import Config


def test_game_dir_dedupe_ignores_slash_style(tmp_path: Path):
    config = Config()
    assert config.add_game_dir(str(tmp_path / "a")) is True
    # 同一个目录，另一种分隔符（tkinter 的 askdirectory 返回的就是正斜杠）
    assert config.add_game_dir(str(tmp_path / "a").replace("\\", "/")) is False
    assert len(config.game_dirs) == 1
    assert config.game_dir == str(tmp_path / "a")


def test_java_paths_dedupe_and_case(tmp_path: Path):
    config = Config()
    path = str(tmp_path / "jdk" / "bin" / "java.exe")
    assert config.add_java_path(path) is True
    assert config.add_java_path(path.upper()) is False
    assert path in config.java_choices()


def test_accounts_add_select_remove():
    config = Config()
    assert config.add_account("Steve") is True
    assert config.selected_account == "Steve"
    assert config.add_account("Steve") is False
    assert config.select_account("Player") is True
    assert config.remove_account("Steve") is True
    assert config.selected_account == "Player"


def test_msa_account_management():
    config = Config()
    record = {"name": "Steve", "uuid": "uuid-1", "refresh_token": "R"}
    assert config.add_msa_account(record) is True
    assert config.selected_account == "Steve"
    assert config.selected_account_type == "msa"
    assert config.current_account() == {"kind": "msa", "name": "Steve", "uuid": "uuid-1"}

    kinds = {entry["kind"] for entry in config.account_entries()}
    assert kinds == {"offline", "msa"}

    # 同名再登录一次是"更新"，不是重复添加
    assert config.add_msa_account(dict(record, refresh_token="R2")) is True
    assert len(config.msa_accounts) == 1
    assert config.msa_account("Steve")["refresh_token"] == "R2"

    assert config.remove_msa_account("Steve") is True
    assert config.selected_account_type == "offline"
    assert config.selected_account in config.accounts


def test_instance_settings_fall_back_to_global():
    config = Config(max_memory_mb=3072, java_path=r"C:\jdk\java.exe", show_log_window=False)
    merged = config.instance("whatever")
    assert merged["max_memory_mb"] == 3072
    assert merged["java_path"] == r"C:\jdk\java.exe"
    assert merged["show_log_window"] is False

    config.update_instance("whatever", max_memory_mb=8192, show_log_window=True)
    merged = config.instance("whatever")
    assert merged["max_memory_mb"] == 8192
    assert merged["show_log_window"] is True
    # 没覆盖的字段仍然回落
    assert merged["java_path"] == r"C:\jdk\java.exe"


def test_load_migrates_legacy_username(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"username": "OldTimer", "max_memory_mb": 4096}), encoding="utf-8"
    )
    config = Config.load(path)
    assert config.accounts == ["OldTimer"]
    assert config.selected_account == "OldTimer"
    assert config.max_memory_mb == 4096
    assert config.game_dir  # 没写游戏目录时有默认值


def test_save_and_reload_roundtrip(tmp_path: Path):
    path = tmp_path / "config.json"
    config = Config(game_dir=str(tmp_path / "mc"))
    config.add_account("Alex")
    config.add_java_path(str(tmp_path / "jdk" / "bin" / "java.exe"))
    config.update_instance("1.21.8", max_memory_mb=6144)
    config.save(path)

    reloaded = Config.load(path)
    assert reloaded.selected_account == "Alex"
    assert reloaded.instance("1.21.8")["max_memory_mb"] == 6144
    assert reloaded.java_choices()

def test_instance_settings_keyed_by_full_path(tmp_path):
    from gelauncher.config import Config

    cfg = Config(game_dir=str(tmp_path / "games" / "a"))
    cfg.update_instance("1.21.8", root=tmp_path / "games" / "a", max_memory_mb=4096)
    assert cfg.instance("1.21.8", root=tmp_path / "games" / "a")["max_memory_mb"] == 4096
    # 同名实例、另一个完整路径：互不影响，回落到全局默认
    assert cfg.instance("1.21.8", root=tmp_path / "games" / "b")["max_memory_mb"] == cfg.max_memory_mb
    # 只有目录名相同、父目录不同，也必须区分
    cfg.update_instance("1.21.8", root=tmp_path / "one" / "mc", max_memory_mb=8192)
    assert cfg.instance("1.21.8", root=tmp_path / "two" / "mc")["max_memory_mb"] == cfg.max_memory_mb

def test_instance_falls_back_to_global_defaults(tmp_path):
    from gelauncher.config import Config

    cfg = Config(game_dir=str(tmp_path / "mc"))
    cfg.width = 1280
    cfg.height = 720
    cfg.jvm_args = "-Xms1G"
    cfg.process_priority = "above"
    settings = cfg.instance("whatever", root=tmp_path / "mc")
    assert settings["width"] == 1280
    assert settings["height"] == 720
    assert settings["jvm_args"] == "-Xms1G"
    assert settings["process_priority"] == "above"
