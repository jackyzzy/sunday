"""文件 IO 原语 — 原子写、JSON / JSONL 读取。

供 LocalMemoryClient 各 sub-client 共用。所有方法都是同步的，调用方按需在
asyncio.Lock 内调用以保证并发一致性。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, content: str) -> None:
    """先写 .tmp 再 rename，保证原子性。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_or(path: Path, default: Any) -> Any:
    """读 JSON；不存在或解析失败返回 default。"""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def read_jsonl_all(path: Path) -> list[dict[str, Any]]:
    """读取整个 JSONL 文件，返回所有行解析后的对象。"""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def read_jsonl_tail(path: Path, max_events: int) -> list[dict[str, Any]]:
    """读 JSONL 末尾 N 条；不存在返回空列表。"""
    return read_jsonl_all(path)[-max_events:]


def append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    """追加一行 JSON。调用方需保证父目录已存在。"""
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def write_json(path: Path, data: Any) -> None:
    """原子写入 JSON（带缩进，便于人阅）。"""
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
