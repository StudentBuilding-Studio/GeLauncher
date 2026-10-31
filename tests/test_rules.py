"""规则求值（方案 §6.5 里点名要的三个用例）。"""

from __future__ import annotations

from gelauncher.core.models import Rule
from gelauncher.core.rules import Environment, allow, current_arch, current_os

WINDOWS_ONLY = (Rule(action="allow", os_name="windows"),)


def test_no_rules_allows_everything():
    assert allow((), Environment(os_name="linux", arch="x86_64", os_version="")) is True
    assert allow(None) is True


def test_windows_rule_denied_on_linux():
    env = Environment(os_name="linux", arch="x86_64", os_version="")
    assert allow(WINDOWS_ONLY, env) is False
    assert allow(WINDOWS_ONLY, Environment(os_name="windows", arch="x86_64", os_version="")) is True


def test_features_are_evaluated():
    rule = (Rule(action="allow", features={"has_custom_resolution": True}),)
    assert allow(rule, Environment(os_name="windows", arch="x86_64", os_version="", features={})) is False
    assert (
        allow(
            rule,
            Environment(
                os_name="windows",
                arch="x86_64",
                os_version="",
                features={"has_custom_resolution": True},
            ),
        )
        is True
    )


def test_arch_and_version_rules():
    rule = (Rule(action="allow", os_arch="x86"),)
    assert allow(rule, Environment(os_name="windows", arch="x86_64", os_version="")) is False
    assert allow(rule, Environment(os_name="windows", arch="x86", os_version="")) is True

    # os.version 是正则，匹配 platform.version() 那样的长串
    version_rule = (Rule(action="allow", os_version=r"^10\."),)
    assert allow(version_rule, Environment(os_name="windows", arch="x86_64", os_version="10.0.22631")) is True
    assert allow(version_rule, Environment(os_name="windows", arch="x86_64", os_version="6.1.7601")) is False


def test_deny_rules_win_when_last_match():
    rules = (
        Rule(action="allow", os_name="windows"),
        Rule(action="deny", os_name="windows", os_arch="x86"),
    )
    assert allow(rules, Environment(os_name="windows", arch="x86", os_version="")) is False
    assert allow(rules, Environment(os_name="windows", arch="x86_64", os_version="")) is True


def test_current_environment_is_plausible():
    assert current_os() in {"windows", "linux", "osx"}
    assert current_arch()
