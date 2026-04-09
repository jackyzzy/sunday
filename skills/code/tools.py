"""
代码辅助技能工具。

本技能当前不注册独立工具（TOOLS = []）。
代码执行能力（run_python、run_shell）已作为通用 CLI 工具在 register_cli_tools() 中注册。
代码阅读/搜索由内置 read_file、search_files、content_search 覆盖。

── 未来扩展方向 ──────────────────────────────────────────────────────────────
以下工具在"LLM 单靠 CLI 工具组合不可靠"时值得加入：

async def run_tests(path: str = ".", args: str = "") -> str:
    \"\"\"运行 pytest 并返回结构化结果（通过数/失败数/错误摘要）。
    相比直接 run_shell("pytest")，本工具解析输出为固定格式，避免 LLM 误读原始终端输出。\"\"\"
    ...

async def extract_symbols(path: str) -> str:
    \"\"\"通过 AST 解析提取文件中的函数名、类名及其行号，返回结构化列表。
    适合在修改代码前快速了解文件结构，比 read_file 全文更高效。\"\"\"
    ...

async def check_syntax(path: str) -> str:
    \"\"\"对 Python 文件做语法检查（py_compile），返回错误位置和描述。
    在 write_file 写入代码后调用，作为轻量验证步骤。\"\"\"
    ...

新工具加入标准：
- 输出需要结构化解析，LLM 直接读 run_shell 输出容易出错
- 操作逻辑复杂，用 run_python 临时写代码完成不可靠
- 不是对现有 CLI 工具的简单包装
──────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

from sunday.tools.registry import ToolMeta  # noqa: F401（未来注册工具时使用）

TOOLS = []
