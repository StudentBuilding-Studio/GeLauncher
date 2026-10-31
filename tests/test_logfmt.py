"""把 log4j 的 XML 事件转成可读日志。"""

from __future__ import annotations

from gelauncher.core.logfmt import GameOutputFormatter


def test_plain_lines_pass_through():
    formatter = GameOutputFormatter()
    assert formatter.feed("hello world") == "hello world"
    assert formatter.feed("[12:00:00] [main/INFO]: x") == "[12:00:00] [main/INFO]: x"


def test_single_line_event():
    formatter = GameOutputFormatter()
    line = (
        '<log4j:Event logger="cpw.mods.modlauncher.Launcher" timestamp="1793463480673" '
        'level="INFO" thread="main"><log4j:Message><![CDATA[ModLauncher starting]]>'
        "</log4j:Message></log4j:Event>"
    )
    result = formatter.feed(line)
    assert result is not None
    assert "[main/INFO]: ModLauncher starting" in result


def test_multi_line_event_with_cdata():
    formatter = GameOutputFormatter()
    lines = [
        '<log4j:Event logger="x" timestamp="0" level="WARN" thread="Render thread">',
        "  <log4j:Message><![CDATA[first line",
        "second line]]></log4j:Message>",
        "</log4j:Event>",
    ]
    out = [formatter.feed(line) for line in lines]
    assert out[:3] == [None, None, None]
    assert out[3] is not None
    assert "first line\nsecond line" in out[3]
    assert "[Render thread/WARN]" in out[3]


def test_unterminated_event_can_be_flushed():
    formatter = GameOutputFormatter()
    formatter.feed('<log4j:Event logger="x" level="INFO" thread="main">')
    formatter.feed("<log4j:Message><![CDATA[oops]]></log4j:Message>")
    leftover = formatter.flush()
    assert len(leftover) == 1
    assert "oops" in leftover[0]
    assert formatter.flush() == []
