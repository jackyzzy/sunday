"""任务模板模块：与 SkillLoader 同构的 auto-discovery。"""
from sunday.templates.loader import TemplateLoader
from sunday.templates.models import StepTypeMeta, TaskTypeTemplate

__all__ = ["TemplateLoader", "StepTypeMeta", "TaskTypeTemplate"]
