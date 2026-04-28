"""TemplateLoader — 任务模板自动发现与懒加载。

发现路径优先级（高 → 低）：
1. user_dir（workspace/templates/）
2. builtin_dir（configs/templates/）

同名 task_type 用户版本覆盖内置版本（与 SkillLoader 同构）。

目录结构（扁平，无子目录）：
    {root}/step_types.yaml          ← 特殊文件：step_type 注册表
    {root}/{task_type}.yaml         ← 任务模板（文件名即 task_type）
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from sunday.templates.models import StepTypeMeta, TaskTypeTemplate

logger = logging.getLogger(__name__)

_STEP_TYPES_FILENAME = "step_types.yaml"


class TemplateLoader:
    """任务模板自动加载器。"""

    def __init__(
        self,
        builtin_dir: Path | None = None,
        user_dir: Path | None = None,
    ) -> None:
        self._builtin_dir = builtin_dir
        self._user_dir = user_dir
        self._step_types: dict[str, StepTypeMeta] = {}
        self._task_types: dict[str, TaskTypeTemplate] = {}

    def discover(self) -> None:
        """扫描模板目录。先 builtin 再 user，user 覆盖 builtin。"""
        self._step_types.clear()
        self._task_types.clear()
        for tdir in (self._builtin_dir, self._user_dir):
            if tdir is None or not tdir.exists():
                continue
            self._load_step_types(tdir)
            self._load_task_types(tdir)
        logger.info(
            "模板发现完成：%d 个 step_type，%d 个 task_type",
            len(self._step_types), len(self._task_types),
        )

    def get_step_meta(self, step_type: str) -> StepTypeMeta | None:
        return self._step_types.get(step_type)

    def get_task_template(self, task_type: str) -> TaskTypeTemplate | None:
        return self._task_types.get(task_type)

    def list_task_types(self) -> list[str]:
        return list(self._task_types.keys())

    def _load_step_types(self, root: Path) -> None:
        f = root / _STEP_TYPES_FILENAME
        if not f.exists():
            return
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning("解析 %s 失败：%s", f, e)
            return
        for name, meta in (data.get("step_types") or {}).items():
            try:
                self._step_types[name] = StepTypeMeta(step_type=name, **meta)
            except Exception as e:
                logger.warning("step_type %s 加载失败：%s", name, e)

    def _load_task_types(self, root: Path) -> None:
        for f in sorted(root.glob("*.yaml")):
            if f.name == _STEP_TYPES_FILENAME:
                continue  # 跳过特殊注册文件
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                tpl = TaskTypeTemplate(**data)
                # 健康检查：task_type 必须与文件名（去后缀）一致
                if tpl.task_type != f.stem:
                    logger.warning(
                        "模板 %s 的 task_type=%s 与文件名不一致，跳过",
                        f, tpl.task_type,
                    )
                    continue
                self._task_types[tpl.task_type] = tpl
            except Exception as e:
                logger.warning("task 模板 %s 加载失败：%s", f, e)
