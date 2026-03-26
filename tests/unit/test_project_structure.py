"""T1-1 验证：项目骨架静态检查（无网络、无文件写入）"""
from __future__ import annotations

import tomllib
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).parent.parent.parent


# ── pyproject.toml ────────────────────────────────────────────────────────────

def _load_pyproject() -> dict:
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)


def test_pyproject_has_required_deps():
    """pyproject.toml 包含所有核心依赖"""
    data = _load_pyproject()
    deps = data["project"]["dependencies"]
    dep_names = {d.split(">=")[0].split("==")[0].lower() for d in deps}
    required = {"agno", "pydantic-settings", "pyyaml", "click", "httpx", "python-dotenv"}
    missing = required - dep_names
    assert not missing, f"缺少依赖: {missing}"


def test_pyproject_has_entry_point():
    """pyproject.toml 注册了 sunday CLI 入口点"""
    data = _load_pyproject()
    scripts = data["project"].get("scripts", {})
    assert "sunday" in scripts, "缺少 [project.scripts] sunday 入口点"
    assert "sunday.cli:main" in scripts["sunday"], "入口点应指向 sunday.cli:main"


def test_pyproject_has_dev_extras():
    """pyproject.toml 包含 dev extras（pytest、ruff）"""
    data = _load_pyproject()
    dev_deps = data["project"].get("optional-dependencies", {}).get("dev", [])
    dev_names = {d.split(">=")[0].split("==")[0].lower() for d in dev_deps}
    assert "pytest" in dev_names, "dev extras 缺少 pytest"
    assert "ruff" in dev_names, "dev extras 缺少 ruff"


def test_pyproject_version_matches_package():
    """pyproject.toml 版本与 sunday.__version__ 一致"""
    data = _load_pyproject()
    pyproject_version = data["project"]["version"]
    import sunday
    assert sunday.__version__ == pyproject_version


# ── .gitignore ────────────────────────────────────────────────────────────────

def _gitignore_lines() -> list[str]:
    return (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_gitignore_protects_env():
    """.gitignore 包含 .env（防止密钥泄漏）"""
    lines = _gitignore_lines()
    assert ".env" in lines, ".gitignore 应包含 .env"


def test_gitignore_protects_venv():
    """.gitignore 包含 .venv/"""
    lines = _gitignore_lines()
    assert ".venv/" in lines or ".venv" in lines, ".gitignore 应包含 .venv/"


def test_gitignore_protects_workspace_memory():
    """.gitignore 包含 workspace/memory/（每日日志不提交）"""
    lines = _gitignore_lines()
    assert "workspace/memory/" in lines, ".gitignore 应包含 workspace/memory/"


# ── 包可导入 ──────────────────────────────────────────────────────────────────

def test_package_importable():
    """sunday 包可导入，且 __version__ 非空"""
    import sunday
    assert sunday.__version__, "__version__ 不应为空"
    assert isinstance(sunday.__version__, str)


def test_version_format():
    """版本号符合 semver 格式（X.Y.Z）"""
    import sunday
    parts = sunday.__version__.split(".")
    assert len(parts) == 3, f"版本号格式应为 X.Y.Z，实际：{sunday.__version__}"
    for part in parts:
        assert part.isdigit(), f"版本号各部分应为数字：{part}"


# ── .env.example ─────────────────────────────────────────────────────────────

def test_env_example_exists():
    """.env.example 文件存在"""
    assert (ROOT / ".env.example").exists(), "缺少 .env.example 文件"


def test_env_example_has_required_vars():
    """.env.example 包含必要的环境变量名"""
    content = (ROOT / ".env.example").read_text(encoding="utf-8")
    required_vars = ["ANTHROPIC_API_KEY", "SUNDAY_CONFIG_FILE"]
    for var in required_vars:
        assert var in content, f".env.example 缺少变量：{var}"
