"""LocalMemoryClient — 本地文件系统实现的 MemoryClient。

通过 `bootstrap.build_memory_client(settings)` 在每个进程构造一次。
sub-client 之间通过 `LocalSessionsClient._bind_logs(...)` 建立级联依赖
（删除 session 时同步删 logs/{id}.jsonl）。
"""
from __future__ import annotations

from pathlib import Path

from sunday.memory.local.knowledge import LocalKnowledgeClient
from sunday.memory.local.logs import LocalLogsClient
from sunday.memory.local.sessions import LocalSessionsClient
from sunday.memory.local.workspace import LocalWorkspaceClient


class LocalMemoryClient:
    """实现 MemoryClient Protocol 的本地文件后端。"""

    def __init__(
        self,
        *,
        sessions_dir: Path,
        memory_dir: Path,
        log_dir: Path,
        workspace_dir: Path,
        skills_dir: Path | None = None,
        retention_days: int = 30,
        run_janitor: bool = True,
    ) -> None:
        self.logs = LocalLogsClient(log_dir)
        self.sessions = LocalSessionsClient(sessions_dir, logs_client=self.logs)
        self.knowledge = LocalKnowledgeClient(
            memory_dir,
            retention_days=retention_days,
            run_janitor=run_janitor,
        )
        self.workspace = LocalWorkspaceClient(workspace_dir, skills_dir=skills_dir)
        # 重新建立级联依赖，确保 LocalSessionsClient.delete() 调到 logs.delete()
        self.sessions._bind_logs(self.logs)

    async def aclose(self) -> None:
        await self.knowledge.aclose()


__all__ = ["LocalMemoryClient"]
