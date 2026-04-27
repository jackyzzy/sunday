"""MemoryClient — Sunday 统一记忆访问接口。

设计原则：
- **单进程单实例**：由 `bootstrap.build_memory_client(settings)` 构造一次，注入到
  CLI / Gateway / ReactAgent / ContextBuilder 等所有需要持久化的组件
- **客户端无业务态**：不缓存索引、不持有"当前 session"，所有操作显式传 session_id
- **Memory 内部管全部生命周期**：原子性、并发协调、TTL、索引一致性都在实现内部
- **REST 友好**：sub-client 方法直接对应未来 HTTP API 端点（`sessions.write_turn`
  ↔ `POST /v1/sessions/{id}/turns`），换 backend 时调用方零修改

四个 sub-client：
    sessions   — L3 会话目录（meta/stream/turns/index）
    knowledge  — L1 长期事实 + L2 每日（MEMORY.md / USER.md / daily/）
    logs       — 结构化执行日志（logs/{session_id}.jsonl）
    workspace  — 只读 L0 用户配置（SOUL/AGENTS/TOOLS/RUNTIME_RULES）
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sunday.memory.models import (
        LogEvent,
        RuntimeRules,
        SessionEvent,
        SessionMeta,
        SessionThread,
        TurnRecord,
    )


WorkspaceFile = Literal["SOUL", "AGENTS", "TOOLS", "RUNTIME_RULES"]
KnowledgeLayer = Literal["MEMORY", "USER"]


class SessionsClient(Protocol):
    """sessions/ 资源域：会话生命周期、事件流、轮次记录、主线。"""

    # ── 生命周期 ──────────────────────────────────────────────────────────
    async def create(self, *, session_id: str | None = None) -> "SessionMeta": ...
    async def ensure(self, session_id: str) -> "SessionMeta": ...
    async def get(self, session_id: str) -> "SessionMeta | None": ...
    async def list(self, *, limit: int = 100) -> list["SessionMeta"]: ...
    async def delete(self, session_id: str) -> None: ...
    """删除整个会话目录及其级联资源（reports/、logs/{id}.jsonl、index 条目）。"""
    async def reset(self, session_id: str) -> None: ...

    # ── 事件流 + 轮次（实现内部保证 turns/、meta、index 三者一致）──────
    async def append_event(self, session_id: str, event: "SessionEvent") -> None: ...
    async def write_turn(self, session_id: str, turn: "TurnRecord") -> None: ...
    async def load_events(
        self, session_id: str, *, max_events: int = 200
    ) -> list["SessionEvent"]: ...
    async def load_turns(self, session_id: str) -> list["TurnRecord"]: ...
    async def load_turn(
        self, session_id: str, turn_id: str
    ) -> "TurnRecord | None": ...

    # ── 主线 ──────────────────────────────────────────────────────────────
    async def get_thread(self, session_id: str) -> "SessionThread | None": ...
    async def save_thread(
        self, session_id: str, thread: "SessionThread"
    ) -> None: ...

    # ── 派生路径（替代直接访问内部 _dir）────────────────────────────────
    def report_dir(self, session_id: str, turn_id: str) -> Path: ...


class KnowledgeClient(Protocol):
    """knowledge：L1 事实（MEMORY.md / USER.md）+ L2 每日（daily/YYYY-MM-DD.md）。"""

    async def append_daily(
        self, content: str, *, day: date | None = None
    ) -> None: ...
    async def upsert_fact(
        self, section: str, key: str, value: str, *, priority: str = "P1"
    ) -> None: ...
    async def upsert_user_profile(self, key: str, value: str) -> None: ...
    async def read_layer(self, layer: KnowledgeLayer) -> str: ...
    async def read_daily(self, day: date) -> str: ...


class LogsClient(Protocol):
    """logs：每会话结构化执行日志（logs/{session_id}.jsonl）。"""

    async def emit(self, session_id: str, event: "LogEvent") -> None: ...
    async def read(
        self, session_id: str, *, tail: int = 500
    ) -> list["LogEvent"]: ...
    async def delete(self, session_id: str) -> None: ...


class WorkspaceClient(Protocol):
    """workspace：只读 L0 用户配置（SOUL/AGENTS/TOOLS/RUNTIME_RULES + skills 列表）。"""

    async def read(self, name: WorkspaceFile) -> str: ...
    async def read_runtime_rules(self) -> "RuntimeRules": ...
    async def list_skills(self) -> list[str]: ...


@runtime_checkable
class MemoryClient(Protocol):
    """Sunday 记忆系统的统一入口。所有持久化都通过它的 sub-client 走。"""

    sessions: SessionsClient
    knowledge: KnowledgeClient
    logs: LogsClient
    workspace: WorkspaceClient

    async def aclose(self) -> None: ...
