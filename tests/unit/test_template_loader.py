"""TemplateLoader 单元测试"""
from pathlib import Path

import yaml

from sunday.templates.loader import TemplateLoader


def test_discover_loads_step_types_and_task_templates(tmp_path):
    """builtin_dir 下的 step_types.yaml 和 task 模板都被加载"""
    builtin = tmp_path / "templates"
    builtin.mkdir()
    (builtin / "step_types.yaml").write_text(yaml.safe_dump({
        "step_types": {
            "research": {"description": "调研", "notes": "搜集"},
            "analysis": {"description": "分析", "notes": "评分"},
        }
    }), encoding="utf-8")
    (builtin / "test_task.yaml").write_text(yaml.safe_dump({
        "task_type": "test_task",
        "description": "测试任务",
        "synthesis": {
            "enabled": True,
            "required_sections": ["A 章节", "B 章节"],
            "document_name_hint": "测试报告.md",
        },
    }), encoding="utf-8")

    loader = TemplateLoader(builtin_dir=builtin)
    loader.discover()

    research = loader.get_step_meta("research")
    assert research is not None
    assert research.description == "调研"
    assert loader.get_step_meta("analysis").description == "分析"

    tpl = loader.get_task_template("test_task")
    assert tpl is not None
    assert tpl.synthesis.enabled is True
    assert tpl.synthesis.required_sections == ["A 章节", "B 章节"]
    assert tpl.synthesis.document_name_hint == "测试报告.md"


def test_synthesis_disabled_by_default(tmp_path):
    """task 模板未声明 synthesis 时，enabled 默认为 false"""
    builtin = tmp_path / "templates"
    builtin.mkdir()
    (builtin / "no_synth.yaml").write_text(yaml.safe_dump({
        "task_type": "no_synth",
        "description": "无综合",
    }), encoding="utf-8")

    loader = TemplateLoader(builtin_dir=builtin)
    loader.discover()

    tpl = loader.get_task_template("no_synth")
    assert tpl is not None
    assert tpl.synthesis.enabled is False
    assert tpl.synthesis.required_sections == []


def test_user_dir_overrides_builtin(tmp_path):
    """user_dir 同名 task_type 覆盖 builtin_dir 版本"""
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()

    (builtin / "task_a.yaml").write_text(yaml.safe_dump({
        "task_type": "task_a", "description": "原始版本",
    }), encoding="utf-8")
    (user / "task_a.yaml").write_text(yaml.safe_dump({
        "task_type": "task_a", "description": "用户覆盖版本",
    }), encoding="utf-8")

    loader = TemplateLoader(builtin_dir=builtin, user_dir=user)
    loader.discover()

    tpl = loader.get_task_template("task_a")
    assert tpl is not None
    assert tpl.description == "用户覆盖版本"


def test_filename_mismatch_is_skipped(tmp_path):
    """task_type 字段与文件名不一致的模板会被跳过（健康检查）"""
    builtin = tmp_path / "templates"
    builtin.mkdir()
    (builtin / "expected_name.yaml").write_text(yaml.safe_dump({
        "task_type": "wrong_name", "description": "名字不一致",
    }), encoding="utf-8")

    loader = TemplateLoader(builtin_dir=builtin)
    loader.discover()

    assert loader.get_task_template("expected_name") is None
    assert loader.get_task_template("wrong_name") is None


def test_step_types_yaml_is_not_loaded_as_task_template(tmp_path):
    """step_types.yaml 是特殊文件，不应被当作 task 模板"""
    builtin = tmp_path / "templates"
    builtin.mkdir()
    (builtin / "step_types.yaml").write_text(yaml.safe_dump({
        "step_types": {"research": {"description": "test"}}
    }), encoding="utf-8")

    loader = TemplateLoader(builtin_dir=builtin)
    loader.discover()

    # step_types.yaml 没有 task_type 字段，不会被加载为任务模板
    assert loader.list_task_types() == []
    # 但 step_types 注册表已加载
    assert loader.get_step_meta("research") is not None


def test_discover_handles_missing_dirs(tmp_path):
    """builtin/user 目录不存在时不抛异常"""
    loader = TemplateLoader(
        builtin_dir=tmp_path / "nonexistent_builtin",
        user_dir=tmp_path / "nonexistent_user",
    )
    loader.discover()  # 不应抛异常

    assert loader.list_task_types() == []
    assert loader.get_step_meta("any") is None


def test_discover_handles_corrupt_yaml(tmp_path):
    """损坏的 yaml 文件被跳过，其他正常文件继续加载"""
    builtin = tmp_path / "templates"
    builtin.mkdir()
    (builtin / "corrupt.yaml").write_text(": invalid : yaml :", encoding="utf-8")
    (builtin / "good.yaml").write_text(yaml.safe_dump({
        "task_type": "good", "description": "正常模板",
    }), encoding="utf-8")

    loader = TemplateLoader(builtin_dir=builtin)
    loader.discover()

    assert loader.get_task_template("good") is not None
    assert loader.get_task_template("corrupt") is None


def test_load_real_builtin_templates():
    """真实的 configs/templates/ 目录可以正常加载（端到端 sanity check）"""
    project_root = Path(__file__).parent.parent.parent
    builtin = project_root / "configs" / "templates"
    if not builtin.exists():
        return  # 跳过：目录不存在

    loader = TemplateLoader(builtin_dir=builtin)
    loader.discover()

    # 8 个内置任务模板都应被加载
    expected = {
        "analysis_recommendation", "research", "code", "creative",
        "qa", "summarization", "planning", "diagnosis",
    }
    actual = set(loader.list_task_types())
    missing = expected - actual
    assert not missing, f"缺少内置任务模板：{missing}"

    # 产出综合文档的类型必须配 required_sections
    for tt in ("analysis_recommendation", "research", "code",
               "summarization", "planning", "diagnosis"):
        tpl = loader.get_task_template(tt)
        assert tpl.synthesis.enabled is True, f"{tt} 应启用 synthesis"
        assert len(tpl.synthesis.required_sections) > 0, f"{tt} 缺 required_sections"

    # 直接输出型不启用 synthesis
    for tt in ("creative", "qa"):
        tpl = loader.get_task_template(tt)
        assert tpl.synthesis.enabled is False, f"{tt} 不应启用 synthesis"

    # step_types 应有 research / analysis / synthesis
    for st in ("research", "analysis", "synthesis"):
        assert loader.get_step_meta(st) is not None
