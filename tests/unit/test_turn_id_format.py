"""S3-B 验证：turn_id 单调可读格式 `t{index:03d}-{short_uuid}`。"""
from __future__ import annotations

import re

from sunday.memory.models import new_turn_id


def test_turn_id_format():
    """符合 t<3 位 index>-<6 位 hex> 模式。"""
    tid = new_turn_id(1)
    assert re.fullmatch(r"t\d{3}-[0-9a-f]{6}", tid), f"格式不对：{tid}"


def test_turn_id_pads_index_to_three_digits():
    """index 不足 3 位补 0，便于字典序与数值序一致。"""
    assert new_turn_id(1).startswith("t001-")
    assert new_turn_id(7).startswith("t007-")
    assert new_turn_id(42).startswith("t042-")
    assert new_turn_id(100).startswith("t100-")


def test_turn_id_is_lexicographically_monotone_within_three_digits():
    """同一 session 下，按 index 递增的 turn_id 字典序严格递增（≤999）。"""
    ids = [new_turn_id(i) for i in range(1, 50)]
    sorted_ids = sorted(ids)
    assert ids == sorted_ids, "字典序应与生成顺序一致"


def test_turn_id_uniqueness_under_same_index():
    """同 index 下多次生成不重复（短 UUID 后缀提供唯一性）。"""
    ids = {new_turn_id(1) for _ in range(100)}
    assert len(ids) == 100, "100 次同 index 调用应得 100 个不同 ID"


def test_turn_id_handles_large_index():
    """index ≥ 1000 时不截断（用更多位）。"""
    tid = new_turn_id(1234)
    assert tid.startswith("t1234-"), f"大 index 应原样保留：{tid}"
