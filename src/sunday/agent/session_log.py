"""Session 级结构化日志 — 将 AgentLoop 执行事件持久化为 JSONL。

每次 AgentLoop 执行（CLI 或 TUI 模式）自动写入：
    ~/.sunday/logs/{session_id}.jsonl

每行一个 JSON 对象，字段固定：
    {"ts": "ISO8601", "session_id": "...", "event": "...", "data": {...}}

事件类型：session_start / plan / step_start / sub_step_result /
         step_result / replan / team_error / error / session_end
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SessionLog:
    """将一次 AgentLoop 执行的关键事件写入 JSONL 日志文件。

    写入失败静默降级（只 WARNING），不影响主流程。
    同步追加写入（emit 单线程串行调用，无并发风险）。
    """

    def __init__(self, log_dir: Path, session_id: str) -> None:
        self._session_id = session_id
        self._start_ts = datetime.now(timezone.utc)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            self._path: Path | None = log_dir / f"{session_id}.jsonl"
        except OSError as e:
            logger.warning("SessionLog 目录创建失败，日志将跳过：%s", e)
            self._path = None

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def log(self, event_type: str, data: dict[str, Any]) -> None:
        """将 emit 事件翻译为语义化 log 事件并写入 JSONL。

        mapping:
          status:executing:xxx → step_start  (含 intent, node_type)
          status:replanning    → replan       (含 step_id, replan_count, failure_reason)
          plan                 → plan         (含 step intent/criteria)
          step_result          → step_result  (含 verify_reason, duration_ms)
          sub_step_result      → sub_step_result
          team_error           → team_error
          error                → error
          其余 status（thinking/summarizing/team:xxx 等）静默忽略
        """
        if event_type == "status":
            status_val = data.get("status", "")
            if status_val.startswith("executing:"):
                self._write("step_start", {
                    "step_id": status_val.split(":", 1)[1],
                    "intent": data.get("intent", ""),
                    "node_type": data.get("node_type", ""),
                })
            elif status_val == "replanning":
                self._write("replan", {
                    "step_id": data.get("step_id", ""),
                    "replan_count": data.get("replan_count"),
                    "max_replans": data.get("max_replans"),
                    "failure_reason": data.get("failure_reason", ""),
                })
        elif event_type == "plan":
            self._write("plan", {
                "goal": data.get("goal", ""),
                "steps": data.get("steps", []),
            })
        elif event_type == "step_result":
            self._write("step_result", {
                "step_id": data.get("step_id", ""),
                "status": data.get("status", ""),
                "verified": data.get("verified"),
                "verify_reason": data.get("verify_reason", ""),
                "duration_ms": data.get("duration_ms"),
            })
        elif event_type == "sub_step_result":
            self._write("sub_step_result", {
                "parent_step_id": data.get("parent_step_id", ""),
                "sub_step_id": data.get("sub_step_id", ""),
                "status": data.get("status", ""),
                "verified": data.get("verified"),
                "verify_reason": data.get("verify_reason", ""),
            })
        elif event_type == "team_error":
            self._write("team_error", {
                "step_id": data.get("step_id", ""),
                "phase": data.get("phase", ""),
                "sub_step_id": data.get("sub_step_id", ""),
                "error": data.get("error", ""),
            })
        elif event_type == "error":
            self._write("error", {"message": data.get("message", "")})

    def log_start(self, task: str, thinking_level: str, mode: str) -> None:
        """写入 session_start 事件（在 run() 最前端调用）。"""
        self._write("session_start", {
            "task": task,
            "thinking_level": thinking_level,
            "mode": mode,
        })

    def log_end(self, summary: str, team_results: list) -> None:
        """写入 session_end 事件（正常完成路径）。"""
        total = len(team_results)
        passed = sum(1 for tr in team_results if tr.passed)
        outcome = "success" if passed == total else ("partial" if passed > 0 else "failed")
        self._write("session_end", {
            "outcome": outcome,
            "steps_total": total,
            "steps_passed": passed,
            "steps_failed": total - passed,
            "duration_seconds": round(
                (datetime.now(timezone.utc) - self._start_ts).total_seconds(), 2
            ),
            "summary_snippet": summary[:200],
        })

    def log_abort(self) -> None:
        """写入 session_end 事件（CancelledError 路径）。"""
        self._write("session_end", {
            "outcome": "aborted",
            "duration_seconds": round(
                (datetime.now(timezone.utc) - self._start_ts).total_seconds(), 2
            ),
        })

    def log_error(self, error: Exception) -> None:
        """写入 session_end 事件（未捕获异常路径）。"""
        self._write("session_end", {
            "outcome": "error",
            "error": str(error),
            "duration_seconds": round(
                (datetime.now(timezone.utc) - self._start_ts).total_seconds(), 2
            ),
        })

    # ── 内部 ──────────────────────────────────────────────────────────────────

    def _write(self, event: str, data: dict[str, Any]) -> None:
        if self._path is None:
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": self._session_id,
            "event": event,
            "data": data,
        }
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("SessionLog 写入失败（session=%s）：%s", self._session_id, e)
