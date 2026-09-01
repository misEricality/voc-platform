"""生成 config/topics/l3_definitions.yaml（128 个 L3 标签定义 + 常见表述）

方案 4（观点短语 → 程序匹配）的匹配词典。
每个 L3 配："定义 + 常见表述关键词"，程序用关键词做包含匹配。

用法：
    python scripts/dev/gen_l3_definitions.py
输出：
    config/topics/l3_definitions.yaml（人工审核后生效）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

import yaml
from openai import OpenAI

from src.analyzers.normalize import load_hierarchy

OUT_PATH = Path("config/topics/l3_definitions.yaml")
BATCH_SIZE = 30  # 每批生成的标签数


def collect_l3(hierarchy: dict) -> list[tuple[str, str, str]]:
    """收集全部 (L1, L2, L3) 三元组"""
    out = []
    for l1, subs in hierarchy.items():
        if not isinstance(subs, dict):
            continue
        for l2, l3_words in subs.items():
            if isinstance(l3_words, list):
                for l3 in l3_words:
                    out.append((l1, l2, l3))
    return out


def build_prompt(items: list[tuple[str, str, str]]) -> str:
    """构造定义生成 prompt"""
    lines = "\n".join(
        f'{i+1}. [{l1}/{l2}] {l3}' for i, (l1, l2, l3) in enumerate(items)
    )
    return f"""请为以下游戏评论分析标签（L3）编写定义与常见表述。

要求：
1. 每个标签输出：definition（一句话解释该标签覆盖什么）+ keywords（常见表述关键词数组，5-10个，包含口语化说法）
2. keywords 用于关键词匹配，要覆盖用户可能说出的各种表达（如"优化差/优化烂/优化好"、"卡成PPT/掉帧/卡顿"）
3. 关键词必须是中性词（不包含评价倾向），"好不好"由情感字段承载
4. 严格 JSON 输出

标签列表：
{lines}

输出格式：
{{
  "definitions": [
    {{"l3": "动作系统", "definition": "战斗动作相关机制与反馈", "keywords": ["动作", "招式", "连招", "战斗手感", "攻击"]}},
    ...
  ]
}}"""


def call_llm(client: OpenAI, prompt: str) -> list[dict]:
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "你是中文语义词典专家，严格按 JSON 输出。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        response_format={"type": "json_object"},
        timeout=120,
    )
    content = resp.choices[0].message.content
    try:
        data = json.loads(content)
        return data.get("definitions", [])
    except json.JSONDecodeError:
        import re
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return []
        return json.loads(m.group(0)).get("definitions", [])


def main() -> None:
    hierarchy = load_hierarchy()
    items = collect_l3(hierarchy)
    print(f"共 {len(items)} 个 L3 标签")

    client = OpenAI(
        api_key=__import__("os").getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com/v1",
    )

    definitions: list[dict] = []
    # 按批生成
    for start in range(0, len(items), BATCH_SIZE):
        chunk = items[start : start + BATCH_SIZE]
        print(f"生成批次 {start//BATCH_SIZE + 1}: {len(chunk)} 个标签...")
        defs = call_llm(client, build_prompt(chunk))
        definitions.extend(defs)
        # 校验覆盖
        chunk_l3 = {l3 for _, _, l3 in chunk}
        defs_l3 = {d.get("l3") for d in defs}
        missing = chunk_l3 - defs_l3
        if missing:
            print(f"  ⚠️ 缺失 {len(missing)} 个: {missing}")

    print(f"共生成 {len(definitions)} 条定义")

    # 组装 yaml
    def_map: dict[str, dict] = {}
    for d in definitions:
        l3 = d.get("l3", "").strip()
        if not l3:
            continue
        def_map[l3] = {
            "definition": d.get("definition", "").strip(),
            "keywords": d.get("keywords", []),
        }

    # 校验：所有 L3 都有定义吗？
    all_l3 = {l3 for _, _, l3 in items}
    missing_final = all_l3 - set(def_map.keys())
    print(f"最终缺失 {len(missing_final)} 个定义: {missing_final if missing_final else '无'}")

    # 写文件
    payload = {
        "# 说明": "L3 标签定义与常见表述词典（方案4：观点短语→程序匹配用）",
        "definitions": def_map,
    }
    OUT_PATH.write_text(
        yaml.dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    print(f"✅ 已写入 {OUT_PATH}（{len(def_map)} 条）")
    print("⚠️ 请人工审核后使用")


if __name__ == "__main__":
    main()