"""把游戏的 stdout 变成人能读的日志。

Mojang 官方的 ``assets/log_configs/client-*.xml`` 给 Console appender 用的是
``<LegacyXMLLayout />``，也就是说游戏往 stdout 打的其实是 log4j 的 XML 事件
（官方启动器自己解析它）。我们照做：把 ``<log4j:Event>…</log4j:Event>`` 块
转成 ``[HH:mm:ss] [thread/level]: msg``，其他输出原样透传。
"""

from __future__ import annotations

import re

_EVENT_START = re.compile(r'<log4j:Event\b([^>]*)>')
_EVENT_END = "</log4j:Event>"
_MESSAGE = re.compile(r"<log4j:Message><!\[CDATA\[(.*?)\]\]></log4j:Message>", re.S)
_ATTR = re.compile(r'(\w+)="([^"]*)"')


class GameOutputFormatter:
    """有状态的逐行转换器：喂一行，吐 0..1 行可读日志。"""

    def __init__(self) -> None:
        self._buffer: list[str] = []
        self._inside = False

    def feed(self, line: str) -> str | None:
        stripped = line.strip()
        if stripped.startswith("<log4j:Event"):
            self._inside = True
            self._buffer = [line]
            if stripped.endswith(_EVENT_END):
                return self._finish()
            return None
        if self._inside:
            self._buffer.append(line)
            if _EVENT_END in stripped:
                return self._finish()
            return None
        return line

    def flush(self) -> list[str]:
        """进程退出时把没闭合的块吐出来，别丢日志。"""
        if self._buffer:
            text = self._finish()
            return [text] if text else []
        return []

    # ------------------------------------------------------------------

    def _finish(self) -> str:
        block = "\n".join(self._buffer)
        self._buffer = []
        self._inside = False
        match = _EVENT_START.search(block)
        attributes = dict(_ATTR.findall(match.group(1))) if match else {}
        message_match = _MESSAGE.search(block)
        message = message_match.group(1) if message_match else block.strip()
        level = attributes.get("level", "INFO")
        thread = attributes.get("thread", "")
        timestamp = _hhmmss(attributes.get("timestamp", ""))
        prefix = f"[{timestamp}] " if timestamp else ""
        if thread:
            return f"{prefix}[{thread}/{level}]: {message}"
        return f"{prefix}[{level}]: {message}"


def _hhmmss(raw: str) -> str:
    """log4j 的 timestamp 是毫秒时间戳，转成本地 HH:mm:ss。"""
    try:
        milliseconds = int(raw)
    except (TypeError, ValueError):
        return ""
    if milliseconds <= 0:
        return ""
    import datetime

    moment = datetime.datetime.fromtimestamp(milliseconds / 1000)
    return moment.strftime("%H:%M:%S")
