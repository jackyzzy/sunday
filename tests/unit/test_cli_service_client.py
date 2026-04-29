"""S1-B 验证：CLI 通过 ServiceClient 走 service，事件流正确处理。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from sunday.cli import main
from sunday.service.client import (
    is_service_running,
    new_session_id,
    service_pid_file,
    spawn_service_if_needed,
)


def test_new_session_id_format():
    """new_session_id 返回 8 位 hex（与原 cli.py 行为一致）。"""
    sid = new_session_id()
    assert len(sid) == 8
    assert all(c in "0123456789abcdef" for c in sid)


def test_service_pid_file_path():
    p = service_pid_file()
    assert p.name == "service.pid"
    assert p.parent.name == ".sunday"


def test_is_service_running_no_pid_file(tmp_path: Path):
    """PID 文件不存在 → False。"""
    with patch("sunday.service.client.service_pid_file",
               return_value=tmp_path / "no_such_pid"):
        assert is_service_running() is False


def test_is_service_running_dead_pid(tmp_path: Path):
    """PID 文件存在但进程不存在 → False。"""
    pid_file = tmp_path / "service.pid"
    pid_file.write_text("999999999")  # 几乎肯定不存在的 PID

    with patch("sunday.service.client.service_pid_file", return_value=pid_file):
        assert is_service_running() is False


def test_spawn_skips_when_running(tmp_path: Path):
    """已在跑时不重复 spawn，直接返回 True。"""
    with patch("sunday.service.client.is_service_running", return_value=True):
        # 不应触发 subprocess.Popen
        assert spawn_service_if_needed() is True


def test_run_command_streams_events_to_stdout(tmp_path: Path):
    """sunday run 通过 client 收事件 → 打印 plan / step_result / 最终 output。"""
    runner = CliRunner()

    async def fake_submit(self, session_id, task, thinking_level="medium",
                          model_override=None, auto_confirm=False):
        from sunday.service.protocol import EventType, Message
        # 模拟一段流：plan → step_result → done
        yield Message(type=EventType.PLAN, session_id=session_id, data={
            "goal": "测试目标",
            "steps": [{"id": "s1", "intent": "拿数据"}],
        })
        yield Message(type=EventType.STEP_RESULT, session_id=session_id, data={
            "step_id": "s1", "status": "done", "verified": True,
        })
        yield Message(type=EventType.DONE, session_id=session_id, data={
            "content": "完成结果",
        })

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *_exc):
        return None

    with (
        patch("sunday.service.client.is_service_running", return_value=True),
        patch("sunday.service.client.ServiceClient.__aenter__", fake_aenter),
        patch("sunday.service.client.ServiceClient.__aexit__", fake_aexit),
        patch("sunday.service.client.ServiceClient.submit_task", fake_submit),
    ):
        result = runner.invoke(main, ["run", "测试任务", "--session", "abc12345"])

    assert result.exit_code == 0
    assert "测试目标" in result.output     # plan 被打印
    assert "[✓] s1" in result.output       # step_result 被打印
    assert "完成结果" in result.output     # final output
    assert "abc12345" in result.output    # session id 提示


def test_run_propagates_error_event(tmp_path: Path):
    """service 推 ERROR 事件时 CLI 退出码 1。"""
    runner = CliRunner()

    async def fake_submit(self, session_id, task, **kwargs):
        from sunday.service.protocol import EventType, Message
        yield Message(type=EventType.ERROR, session_id=session_id, data={
            "message": "task failed",
        })

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *_exc):
        return None

    with (
        patch("sunday.service.client.is_service_running", return_value=True),
        patch("sunday.service.client.ServiceClient.__aenter__", fake_aenter),
        patch("sunday.service.client.ServiceClient.__aexit__", fake_aexit),
        patch("sunday.service.client.ServiceClient.submit_task", fake_submit),
    ):
        result = runner.invoke(main, ["run", "boom"])

    assert result.exit_code == 1
    combined = result.output + (result.stderr if getattr(result, "stderr", None) else "")
    assert "task failed" in combined or "失败" in combined
