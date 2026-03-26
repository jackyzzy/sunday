"""配置系统测试"""
import pytest
from pathlib import Path
from unittest.mock import patch
import yaml
import tempfile
import os


def test_sunday_config_defaults(tmp_path):
    """测试默认配置加载"""
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "agent": {"name": "TestSunday"},
        "model": {"provider": "anthropic", "id": "claude-opus-4-5"},
    }))

    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "test-key",
        "SUNDAY_CONFIG_FILE": str(config_file),
    }):
        from sunday.config import Settings
        s = Settings()
        cfg = s.sunday
        assert cfg.agent.name == "TestSunday"
        assert cfg.model.provider == "anthropic"
        assert cfg.reasoning.max_steps == 10  # 默认值
        assert cfg.memory.log_retention_days == 30  # 默认值


def test_get_api_key_success():
    """测试 API key 获取"""
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-test123",
        "SUNDAY_CONFIG_FILE": "configs/agent.yaml",
    }):
        from sunday.config import Settings
        s = Settings()
        assert s.get_api_key("anthropic") == "sk-ant-test123"


def test_get_api_key_missing():
    """测试缺失 API key 时抛出异常"""
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "",
        "SUNDAY_CONFIG_FILE": "configs/agent.yaml",
    }):
        from sunday.config import Settings
        s = Settings()
        with pytest.raises(ValueError, match="anthropic"):
            s.get_api_key("anthropic")


def test_workspace_dir_in_agent_config():
    """测试路径字段只在 AgentConfig 中定义"""
    from sunday.config import AgentConfig, MemoryConfig
    agent_cfg = AgentConfig()
    assert hasattr(agent_cfg, "workspace_dir")
    assert hasattr(agent_cfg, "sessions_dir")
    # MemoryConfig 不含路径字段
    mem_cfg = MemoryConfig()
    assert not hasattr(mem_cfg, "workspace_dir")
    assert not hasattr(mem_cfg, "sessions_dir")
