"""在界面里改配置：只改 config.toml 里要改的那几行，保留注释和其他内容。

不依赖额外的库：按行找到 ``[providers.video_gen.wan]`` 这样的小节，替换或补上 ``key = "value"``。
写之前用 tomllib 解析一遍改好的内容，确认值都对了才落盘；出错就不写，原文件保持不变。
"""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from pathlib import Path

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def toml_string(value: str) -> str:
    """TOML 基本字符串：转义反斜杠、引号和控制字符。"""
    out = ['"']
    for char in value:
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _header(table: list[str]) -> re.Pattern:
    joined = r"\s*\.\s*".join(re.escape(part) for part in table)
    return re.compile(rf"^\s*\[\s*{joined}\s*\]\s*(#.*)?$")


def _is_header(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("[") and not stripped.startswith("#")


_VALUE = r'("(?:[^"\\]|\\.)*"|\'[^\']*\'|[^#\s][^#]*?)'


def _set_in_lines(lines: list[str], table: list[str], key: str, value: str | None) -> list[str]:
    header = _header(table)
    start = next((i for i, line in enumerate(lines) if header.match(line)), None)
    if start is None:
        if value is None:
            return lines
        tail = [] if not lines or not lines[-1].strip() else [""]
        return [*lines, *tail, f"[{'.'.join(table)}]", f"{key} = {toml_string(value)}"]
    end = next((i for i in range(start + 1, len(lines)) if _is_header(lines[i])), len(lines))
    active = re.compile(rf"^(\s*){re.escape(key)}\s*=\s*{_VALUE}\s*(#.*)?$")
    commented = re.compile(rf"^(\s*)#\s*{re.escape(key)}\s*=\s*{_VALUE}?\s*(#.*)?$")
    for i in range(start + 1, end):
        match = active.match(lines[i])
        if match:
            if value is None:
                return lines[:i] + lines[i + 1 :]
            comment = f"   {match.group(3).strip()}" if match.group(3) else ""
            lines = list(lines)
            lines[i] = f"{match.group(1)}{key} = {toml_string(value)}{comment}"
            return lines
    if value is None:
        return lines
    for i in range(start + 1, end):
        match = commented.match(lines[i])
        if match:
            comment = f"   {match.group(3).strip()}" if match.group(3) else ""
            lines = list(lines)
            lines[i] = f"{match.group(1)}{key} = {toml_string(value)}{comment}"
            return lines
    return [*lines[: start + 1], f"{key} = {toml_string(value)}", *lines[start + 1 :]]


def _lookup(data: dict, table: list[str]) -> dict:
    node: object = data
    for part in table:
        node = node.get(part, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def update(path: Path, table: list[str], values: dict[str, str | None]) -> None:
    """把 ``values`` 写进 ``[table]`` 小节：字符串设置，``None`` 删除这一项。

    文件不存在时新建。改完解析不通过或值不对时抛 ``ValueError``，原文件不变。
    """
    for part in [*table, *values]:
        if not _BARE_KEY.match(part):
            raise ValueError(f"配置项名称不合法：{part}")
    raw = path.read_bytes().decode("utf-8-sig") if path.is_file() else ""
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    for key, value in values.items():
        lines = _set_in_lines(lines, table, key, value)
    text = "\n".join(lines) + "\n"
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"改完的配置无法解析，已放弃保存：{exc}") from exc
    section = _lookup(parsed, table)
    for key, value in values.items():
        if (section.get(key) if value is not None else None) != value and not (value is None and key not in section):
            raise ValueError(f"配置项 {key} 没有写成预期的值，已放弃保存")
    data = text.replace("\n", newline).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(temp, path)
    except OSError as exc:
        Path(temp).unlink(missing_ok=True)
        raise ValueError(f"无法写入配置文件 {path}（可能被其他程序占用或没有权限）：{exc}") from exc
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
