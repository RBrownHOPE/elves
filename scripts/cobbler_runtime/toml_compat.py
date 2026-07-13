"""Stdlib-only TOML loader compatibility for Elves-generated ``models.toml``.

Python 3.11+ delegates to :mod:`tomllib`.  Python 3.10 uses the deliberately
small parser below, which accepts the subset emitted by ``setup.py``: dotted
tables/keys, strings, booleans, integers/floats, multiline arrays, and inline
tables.  It never evaluates code and rejects unsupported TOML constructs.
"""

from __future__ import annotations

import json
import re
from typing import Any

try:  # pragma: no cover - selected by interpreter version
    import tomllib as _tomllib
except ModuleNotFoundError:  # Python 3.10
    _tomllib = None  # type: ignore[assignment]


class TomlCompatError(ValueError):
    """Invalid or unsupported TOML in the generated models.toml subset."""


_INT_RE = re.compile(r"^[+-]?[0-9](?:_?[0-9])*$")
_FLOAT_RE = re.compile(
    r"^[+-]?(?:(?:[0-9](?:_?[0-9])*)?\.[0-9](?:_?[0-9])*"
    r"(?:[eE][+-]?[0-9](?:_?[0-9])*)?|"
    r"[0-9](?:_?[0-9])*[eE][+-]?[0-9](?:_?[0-9])*)$"
)
_SPECIAL_FLOAT_RE = re.compile(r"^[+-]?(?:inf|nan)$")
_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _strip_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif quote == "'":
            if char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == "#":
            return line[:index]
    return line


def _balanced(text: str) -> bool:
    square = 0
    curly = 0
    quote: str | None = None
    escaped = False
    for char in text:
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if quote == "'":
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "[":
            square += 1
        elif char == "]":
            square -= 1
        elif char == "{":
            curly += 1
        elif char == "}":
            curly -= 1
        if square < 0 or curly < 0:
            raise TomlCompatError("unexpected closing delimiter")
    if quote is not None:
        raise TomlCompatError("multiline basic/literal strings are unsupported")
    return square == 0 and curly == 0


def _split_assignment(text: str) -> tuple[str, str]:
    quote: str | None = None
    escaped = False
    depth = 0
    for index, char in enumerate(text):
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if quote == "'":
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "=" and depth == 0:
            key = text[:index].strip()
            value = text[index + 1 :].strip()
            if not key or not value:
                break
            return key, value
    raise TomlCompatError("expected key = value assignment")


def _split_dotted_key(text: str) -> list[str]:
    parts: list[str] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        if text[index] == '"':
            try:
                value, end = json.JSONDecoder().raw_decode(text, index)
            except json.JSONDecodeError as exc:
                raise TomlCompatError(f"invalid quoted key: {exc}") from exc
            if not isinstance(value, str):
                raise TomlCompatError("quoted key must be a string")
            parts.append(value)
            index = end
        elif text[index] == "'":
            end = text.find("'", index + 1)
            if end < 0:
                raise TomlCompatError("unterminated literal key")
            parts.append(text[index + 1 : end])
            index = end + 1
        else:
            end = index
            while end < len(text) and text[end] not in ". \t\r\n":
                end += 1
            value = text[index:end]
            if not _BARE_KEY_RE.fullmatch(value):
                raise TomlCompatError(f"invalid bare key: {value!r}")
            parts.append(value)
            index = end
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        if text[index] != ".":
            raise TomlCompatError("expected dot between key segments")
        index += 1
    if not parts:
        raise TomlCompatError("empty key")
    return parts


class _ValueParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.index = 0

    def _space(self) -> None:
        while self.index < len(self.text) and self.text[self.index].isspace():
            self.index += 1

    def parse(self) -> Any:
        value = self._value()
        self._space()
        if self.index != len(self.text):
            raise TomlCompatError(f"unexpected value suffix: {self.text[self.index:]!r}")
        return value

    def _value(self) -> Any:
        self._space()
        if self.index >= len(self.text):
            raise TomlCompatError("missing value")
        char = self.text[self.index]
        if char == '"':
            try:
                value, end = json.JSONDecoder().raw_decode(self.text, self.index)
            except json.JSONDecodeError as exc:
                raise TomlCompatError(f"invalid string: {exc}") from exc
            if not isinstance(value, str):
                raise TomlCompatError("basic string expected")
            self.index = end
            return value
        if char == "'":
            end = self.text.find("'", self.index + 1)
            if end < 0:
                raise TomlCompatError("unterminated literal string")
            value = self.text[self.index + 1 : end]
            self.index = end + 1
            return value
        if char == "[":
            return self._array()
        if char == "{":
            return self._inline_table()
        start = self.index
        while self.index < len(self.text) and self.text[self.index] not in ",]} \t\r\n":
            self.index += 1
        token = self.text[start : self.index]
        if token == "true":
            return True
        if token == "false":
            return False
        normalized = token.replace("_", "")
        if _INT_RE.fullmatch(token):
            return int(normalized)
        if _FLOAT_RE.fullmatch(token):
            return float(normalized)
        if _SPECIAL_FLOAT_RE.fullmatch(token):
            return float(normalized)
        raise TomlCompatError(f"unsupported bare value: {token!r}")

    def _array(self) -> list[Any]:
        self.index += 1
        values: list[Any] = []
        while True:
            self._space()
            if self.index >= len(self.text):
                raise TomlCompatError("unterminated array")
            if self.text[self.index] == "]":
                self.index += 1
                return values
            values.append(self._value())
            self._space()
            if self.index < len(self.text) and self.text[self.index] == ",":
                self.index += 1
                continue
            if self.index < len(self.text) and self.text[self.index] == "]":
                continue
            raise TomlCompatError("expected comma or closing bracket in array")

    def _inline_table(self) -> dict[str, Any]:
        self.index += 1
        result: dict[str, Any] = {}
        while True:
            self._space()
            if self.index >= len(self.text):
                raise TomlCompatError("unterminated inline table")
            if self.text[self.index] == "}":
                self.index += 1
                return result
            key_start = self.index
            quote: str | None = None
            escaped = False
            while self.index < len(self.text):
                char = self.text[self.index]
                if quote == '"':
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quote:
                        quote = None
                elif quote == "'":
                    if char == quote:
                        quote = None
                elif char in {'"', "'"}:
                    quote = char
                elif char == "=":
                    break
                self.index += 1
            if self.index >= len(self.text):
                raise TomlCompatError("inline table assignment missing equals")
            key_parts = _split_dotted_key(self.text[key_start : self.index].strip())
            self.index += 1
            value = self._value()
            _assign(result, key_parts, value)
            self._space()
            if self.index < len(self.text) and self.text[self.index] == ",":
                self.index += 1
                continue
            if self.index < len(self.text) and self.text[self.index] == "}":
                continue
            raise TomlCompatError("expected comma or closing brace in inline table")


def _assign(root: dict[str, Any], path: list[str], value: Any) -> None:
    target = root
    for segment in path[:-1]:
        existing = target.get(segment)
        if existing is None:
            existing = {}
            target[segment] = existing
        if not isinstance(existing, dict):
            raise TomlCompatError(f"key path conflicts at {segment!r}")
        target = existing
    leaf = path[-1]
    if leaf in target:
        raise TomlCompatError(f"duplicate key: {'.'.join(path)}")
    target[leaf] = value


def loads_compat(text: str) -> dict[str, Any]:
    """Parse the safe Elves-generated TOML subset without third-party packages."""
    root: dict[str, Any] = {}
    table_path: list[str] = []
    pending = ""
    pending_line = 0

    def process(statement: str, line_no: int) -> None:
        nonlocal table_path
        stripped = statement.strip()
        if not stripped:
            return
        if stripped.startswith("[["):
            raise TomlCompatError(f"line {line_no}: array-of-tables is unsupported")
        if stripped.startswith("["):
            if not stripped.endswith("]"):
                raise TomlCompatError(f"line {line_no}: malformed table header")
            table_path = _split_dotted_key(stripped[1:-1].strip())
            target = root
            for segment in table_path:
                existing = target.get(segment)
                if existing is None:
                    existing = {}
                    target[segment] = existing
                if not isinstance(existing, dict):
                    raise TomlCompatError(f"line {line_no}: table conflicts at {segment!r}")
                target = existing
            return
        key, raw_value = _split_assignment(stripped)
        value = _ValueParser(raw_value).parse()
        target = root
        for segment in table_path:
            child = target.get(segment)
            if not isinstance(child, dict):
                raise TomlCompatError(f"line {line_no}: invalid table path")
            target = child
        _assign(target, _split_dotted_key(key), value)

    for line_no, raw_line in enumerate(text.splitlines(), 1):
        clean = _strip_comment(raw_line).strip()
        if not clean and not pending:
            continue
        if pending:
            pending += "\n" + clean
            if _balanced(pending):
                process(pending, pending_line)
                pending = ""
            continue
        if clean.startswith("[") and not clean.startswith("[[") and clean.endswith("]"):
            process(clean, line_no)
            continue
        pending = clean
        pending_line = line_no
        if _balanced(pending):
            process(pending, pending_line)
            pending = ""
    if pending:
        raise TomlCompatError(f"line {pending_line}: unterminated value")
    return root


def loads(text: str) -> dict[str, Any]:
    """Load TOML on every supported Python, using stdlib tomllib when available."""
    if _tomllib is not None:
        return _tomllib.loads(text)
    return loads_compat(text)
