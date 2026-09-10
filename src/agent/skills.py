"""Skill 模板匹配（2026-09-09 · 见 docs/architecture/ORIGINAL_VOICE_ANALYSIS_AGENT.md §4）

Skill 是预定义的「系统提示词段」，按用户问题关键词或 tool 调用自动注入到 system prompt。
类比 Anthropic DSH 的 skill 协议，但**简化版**：不引入指令路径/sandbox，纯粹 prompt 段。

YAML schema（每个文件 = 一个 skill）：
```yaml
name: 负面痛点聚焦
description: 一句话描述（前端展示用）
triggers:
  keywords: [痛点, 抱怨, 吐槽, ...]   # 用户问题命中任一即触发
  tool_name: query_topics              # 可选：该 tool 被调用时触发
  tool_args: { sentiment: negative }   # 可选：仅当 tool_args 匹配时触发
priority: 10                           # 可选：0-100，高的覆盖低的同名前缀（防重叠）
system_prompt: |
  ...（追加到主 system prompt 之后）
```
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("voc.agent.skills")

SKILLS_DIR = Path(__file__).resolve().parents[2] / "config" / "agent" / "skills"


@lru_cache(maxsize=1)
def load_skills() -> list[dict]:
    """加载 config/agent/skills/*.yaml → list of skill dicts（lru_cache 缓存）

    失败：单文件 YAML 解析失败 → 记 warning 跳过，其他文件照常加载
    """
    if not SKILLS_DIR.exists():
        log.warning(f"skills dir missing: {SKILLS_DIR}")
        return []
    skills: list[dict] = []
    for path in sorted(SKILLS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            log.warning(f"skill yaml parse failed: {path.name}: {e}")
            continue
        if not isinstance(data, dict) or "name" not in data or "system_prompt" not in data:
            log.warning(f"skill yaml invalid (need name + system_prompt): {path.name}")
            continue
        data.setdefault("description", "")
        data.setdefault("triggers", {})
        data.setdefault("priority", 0)
        data["_file"] = path.name
        skills.append(data)
    log.info(f"skills loaded: {len(skills)} from {SKILLS_DIR}")
    return skills


def _match_keyword(question: str, keywords: list[str]) -> bool:
    q_lower = question.lower()
    return any(kw.lower() in q_lower for kw in keywords)


def _match_tool(triggers: dict, tool_name: str | None,
                 tool_args: dict | None) -> bool:
    """tool 触发匹配：tool_name 命中 + 可选 tool_args 子集匹配"""
    if "tool_name" not in triggers:
        return False
    if triggers["tool_name"] != tool_name:
        return False
    if "tool_args" in triggers and triggers["tool_args"]:
        if not tool_args:
            return False
        for k, v in triggers["tool_args"].items():
            if tool_args.get(k) != v:
                return False
    return True


def find_relevant_skills(*, question: str, tool_name: str | None = None,
                          tool_args: dict | None = None) -> list[dict]:
    """匹配相关 skill（按 priority desc；去重 _file）

    Returns: list of {"name", "description", "system_prompt", "priority"}
    """
    all_skills = load_skills()
    matched: list[dict] = []
    seen_files: set[str] = set()
    for s in all_skills:
        if s["_file"] in seen_files:
            continue
        triggers = s["triggers"] or {}
        hit = False
        # 关键词触发
        if triggers.get("keywords") and _match_keyword(question, triggers["keywords"]):
            hit = True
        # tool 触发
        if tool_name and _match_tool(triggers, tool_name, tool_args):
            hit = True
        if hit:
            matched.append(s)
            seen_files.add(s["_file"])
    matched.sort(key=lambda x: -x.get("priority", 0))
    return matched


def compose_skill_prompts(skills: list[dict]) -> str:
    """把匹配的 skills 拼成一段 system prompt 段"""
    if not skills:
        return ""
    parts = ["\n\n## 当前场景指令（skill 注入）\n"]
    for s in skills:
        parts.append(f"### {s['name']}\n{s['system_prompt'].strip()}\n")
    return "".join(parts)


# 测试钩子：清缓存（避免测试间污染）
def _reset_cache() -> None:
    load_skills.cache_clear()
