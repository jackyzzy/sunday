"""文件操作技能工具 — 从 sunday.tools.cli_tool 重新导出"""
from sunday.tools.cli_tool import list_dir, read_file, search_files, write_file

__all__ = ["read_file", "write_file", "list_dir", "search_files"]
