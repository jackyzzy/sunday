"""T1-2 验证：配置系统单元测试（无网络，fake env key，tmp_path）"""
from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

# ── 原有 4 个测试 ─────────────────────────────────────────────────────────────

def test_sunday_config_defaults(tmp_path):
    """测试默认配置加载"""
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({
        "agent": {"name": "TestSunday"},
        "model": {"provider": "anthropic", "id": "claude-opus-4-5"},
    }))

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ANTHROPIC_API_KEY", "test-key")
        mp.setenv("SUNDAY_CONFIG_FILE", str(config_file))
        from sunday.config import Settings
        s = Settings()
        cfg = s.sunday
        assert cfg.agent.name == "TestSunday"
        assert cfg.model.provider == "anthropic"
        assert cfg.reasoning.max_steps == 10  # 默认值
        assert cfg.memory.log_retention_days == 30  # 默认值


def test_get_api_key_success():
    """测试 API key 获取"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ANTHROPIC_API_KEY", "sk-ant-test123")
        mp.setenv("SUNDAY_CONFIG_FILE", "configs/agent.yaml")
        from sunday.config import Settings
        s = Settings()
        assert s.get_api_key("anthropic") == "sk-ant-test123"


def test_get_api_key_missing():
    """测试缺失 API key 时抛出异常"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ANTHROPIC_API_KEY", "")
        mp.setenv("SUNDAY_CONFIG_FILE", "configs/agent.yaml")
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


# ── 新增 11 个测试 ────────────────────────────────────────────────────────────

def test_all_config_models_have_defaults():
    """9 个子模型均可无参构造（所有字段有默认值）"""
    from sunday.config import (
        AgentConfig,
        MCPConfig,
        MemoryConfig,
        ModelConfig,
        ReasoningConfig,
        SkillsConfig,
        SundayConfig,
        TaskConfig,
        ToolsConfig,
    )
    # 无参构造不应抛异常
    ModelConfig()
    ReasoningConfig()
    MemoryConfig()
    ToolsConfig()
    MCPConfig()
    SkillsConfig()
    AgentConfig()
    TaskConfig()
    SundayConfig()


def test_yaml_missing_fields_use_defaults(tmp_path):
    """最小 YAML（只含 agent.name）时其余字段使用默认值"""
    config_file = tmp_path / "agent.yaml"
    config_file.write_text(yaml.dump({"agent": {"name": "MinTest"}}))

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ANTHROPIC_API_KEY", "fake-key")
        mp.setenv("SUNDAY_CONFIG_FILE", str(config_file))
        from sunday.config import Settings
        s = Settings()
        cfg = s.sunday
        # 未指定的字段应使用默认值
        assert cfg.model.provider == "anthropic"
        assert cfg.reasoning.thinking_level == "medium"
        assert cfg.tools.default_timeout == 30
        assert cfg.mcp.servers == []


def test_yaml_type_error_raises(tmp_path):
    """类型错误的 YAML 应触发 ValidationError"""
    config_file = tmp_path / "agent.yaml"
    # max_steps 应为 int，传入字符串应触发验证错误
    config_file.write_text(yaml.dump({"reasoning": {"max_steps": "not-a-number"}}))

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ANTHROPIC_API_KEY", "fake-key")
        mp.setenv("SUNDAY_CONFIG_FILE", str(config_file))
        from sunday.config import Settings
        s = Settings()
        with pytest.raises((ValidationError, ValueError)):
            _ = s.sunday


def test_workspace_dir_tilde_expanded(tmp_path):
    """workspace_dir 中的 ~ 被展开为绝对路径"""
    from sunday.config import AgentConfig
    cfg = AgentConfig()  # 默认使用 ~/.sunday/workspace
    assert not str(cfg.workspace_dir).startswith("~"), (
        f"workspace_dir 应展开 ~，实际：{cfg.workspace_dir}"
    )
    assert cfg.workspace_dir.is_absolute(), (
        f"workspace_dir 应为绝对路径，实际：{cfg.workspace_dir}"
    )


def test_settings_sunday_cached():
    """多次访问 settings.sunday 返回同一对象（cached_property）"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ANTHROPIC_API_KEY", "fake-key")
        mp.setenv("SUNDAY_CONFIG_FILE", "configs/agent.yaml")
        from sunday.config import Settings
        s = Settings()
        cfg1 = s.sunday
        cfg2 = s.sunday
        assert cfg1 is cfg2, "Settings.sunday 应是 cached_property，每次返回同一实例"


def test_api_key_not_in_error_message():
    """ValueError 错误信息不应包含真实 key 内容"""
    fake_key = "sk-ant-secret-12345"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ANTHROPIC_API_KEY", "")
        mp.setenv("SUNDAY_CONFIG_FILE", "configs/agent.yaml")
        from sunday.config import Settings
        s = Settings()
        try:
            s.get_api_key("anthropic")
            pytest.fail("应抛出 ValueError")
        except ValueError as e:
            assert fake_key not in str(e), "错误信息不应包含 API key"


def test_openai_api_key():
    """可以获取 OpenAI API key"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OPENAI_API_KEY", "sk-openai-test")
        mp.setenv("SUNDAY_CONFIG_FILE", "configs/agent.yaml")
        from sunday.config import Settings
        s = Settings()
        assert s.get_api_key("openai") == "sk-openai-test"


def test_unknown_provider_raises():
    """未知 provider 应抛出 ValueError"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ANTHROPIC_API_KEY", "fake-key")
        mp.setenv("SUNDAY_CONFIG_FILE", "configs/agent.yaml")
        from sunday.config import Settings
        s = Settings()
        with pytest.raises(ValueError):
            s.get_api_key("cohere")


def test_tools_config_deny_list_defaults():
    """ToolsConfig 默认包含危险命令黑名单"""
    from sunday.config import ToolsConfig
    cfg = ToolsConfig()
    assert len(cfg.deny_list) > 0, "deny_list 不应为空"
    # 默认包含 rm -rf
    deny_str = " ".join(cfg.deny_list)
    assert "rm" in deny_str, "deny_list 应包含 rm 相关规则"


def test_mcp_servers_empty_by_default():
    """MCPConfig 默认服务器列表为空"""
    from sunday.config import MCPConfig
    cfg = MCPConfig()
    assert cfg.servers == [], "默认 MCP 服务器列表应为空"


def test_sunday_config_file_missing_falls_back_to_defaults(tmp_path):
    """配置文件不存在时回退到默认 SundayConfig"""
    nonexistent = tmp_path / "nonexistent.yaml"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ANTHROPIC_API_KEY", "fake-key")
        mp.setenv("SUNDAY_CONFIG_FILE", str(nonexistent))
        from sunday.config import Settings
        s = Settings()
        cfg = s.sunday
        # 应返回全默认配置而不是抛异常
        assert cfg.agent.name == "Sunday"
        assert cfg.model.provider == "anthropic"


# ── api_key_env 新增 3 个测试 ─────────────────────────────────────────────────

def test_get_api_key_via_explicit_env():
    """api_key_env 显式指定时，从对应环境变量读取 key（忽略 provider 映射）"""
    import os
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test")
        mp.setenv("OPENAI_API_KEY", "")
        mp.setenv("SUNDAY_CONFIG_FILE", "configs/agent.yaml")
        from sunday.config import Settings
        s = Settings()
        key = s.get_api_key("openai", api_key_env="DEEPSEEK_API_KEY")
        assert key == "sk-deepseek-test"


def test_get_api_key_fallback_to_provider():
    """api_key_env=None 时，回退到按 provider 名的默认映射"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ANTHROPIC_API_KEY", "sk-ant-fallback")
        mp.setenv("SUNDAY_CONFIG_FILE", "configs/agent.yaml")
        from sunday.config import Settings
        s = Settings()
        key = s.get_api_key("anthropic", api_key_env=None)
        assert key == "sk-ant-fallback"


def test_get_api_key_missing_env_raises():
    """api_key_env 指向不存在的环境变量时，抛出 ValueError 并提示变量名"""
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("MOONSHOT_API_KEY", raising=False)
        mp.setenv("SUNDAY_CONFIG_FILE", "configs/agent.yaml")
        from sunday.config import Settings
        s = Settings()
        with pytest.raises(ValueError, match="MOONSHOT_API_KEY"):
            s.get_api_key("openai", api_key_env="MOONSHOT_API_KEY")
