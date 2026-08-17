"""黄金集回归门禁：方案4 程序匹配层不回归

黄金集来源：data/validation/ 500 条人工抽样验证中「标注验证=1（正确）」的 410 条，
字段为 (phrase -> full_path)。任何词典 / 匹配规则改动后必须跑此用例，
确保已正确标注的样本不被改坏。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.analyzers.normalize import (
    build_keyword_index,
    build_l3_mapping,
    load_definitions,
    load_hierarchy,
    map_l3_to_path,
    match_l3,
)

FIXTURE = Path(__file__).parent / "fixtures" / "golden_match_set.json"


def _predict(phrase: str, kidx, mapping, defs) -> str | None:
    l3 = match_l3(phrase, kidx, defs)
    return map_l3_to_path(l3, mapping) if l3 else None


def test_golden_set_no_regression():
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert golden.get("taxonomy_version") == "gdt-3.1.1", "黄金集词表版本必须为 gdt-3.1.1"
    items = golden.get("items", [])
    assert items, "黄金集不应为空"

    hierarchy = load_hierarchy()

    mapping = build_l3_mapping(hierarchy)
    defs = load_definitions()
    kidx = build_keyword_index(defs)

    failed = []
    for item in items:
        pred = _predict(item["phrase"], kidx, mapping, defs)
        if pred != item["full_path"]:
            failed.append((item["phrase"], item["full_path"], pred))

    assert not failed, (
        f"黄金集回归失败 {len(failed)}/{len(items)}：\n"
        + "\n".join(f"  {p} | 期望 {e} 实际 {a}" for p, e, a in failed[:20])
    )
