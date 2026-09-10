"""Skill 匹配单元测试（2026-09-09 第 4 轮）

覆盖：
- load_skills：3 个 YAML 都加载成功
- 关键词触发：「痛点」「趋势」「原声」分别命中不同 skill
- tool_name + tool_args 触发：query_topics + sentiment=negative 命中负面痛点
- priority 排序：高 priority 排前面
- 多 skill 同时匹配时返回列表（按 priority desc）
- 不匹配 → 空列表
"""
from __future__ import annotations

import pytest

from src.agent.skills import (
    compose_skill_prompts,
    find_relevant_skills,
    load_skills,
    _reset_cache,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """每个测试前清缓存（lru_cache + dev 改 yaml 后生效）"""
    _reset_cache()
    yield
    _reset_cache()


def test_load_skills_count():
    """应该加载 3 个 YAML（n-negative-pain-points / n-sentiment-trend / n-concrete-examples）"""
    skills = load_skills()
    names = {s["name"] for s in skills}
    assert "负面痛点聚焦" in names
    assert "情感趋势对比" in names
    assert "原声引用增强" in names


def test_keyword_negative_pain_points():
    """「痛点」「抱怨」「吐槽」都应命中负面痛点聚焦"""
    for q in ["玩家痛点是什么", "玩家抱怨的", "吐槽最多", "主要不满", "游戏的缺点"]:
        matches = find_relevant_skills(question=q)
        names = [s["name"] for s in matches]
        assert "负面痛点聚焦" in names, f"query {q!r} 应命中负面痛点，实际：{names}"


def test_keyword_sentiment_trend():
    """「趋势」「对比」「变化」应命中情感趋势对比"""
    for q in ["最近趋势", "对比一下", "时间变化", "近期如何"]:
        matches = find_relevant_skills(question=q)
        names = [s["name"] for s in matches]
        assert "情感趋势对比" in names, f"query {q!r} 应命中情感趋势，实际：{names}"


def test_keyword_concrete_examples():
    """「例子」「原声」「举几个」应命中原声引用增强"""
    for q in ["举个例子", "看看原声", "玩家具体怎么说的", "给几个例子"]:
        matches = find_relevant_skills(question=q)
        names = [s["name"] for s in matches]
        assert "原声引用增强" in names, f"query {q!r} 应命中原声引用，实际：{names}"


def test_no_match_returns_empty():
    """无关 query 应返回空列表"""
    for q in ["你好", "今天天气", "1+1等于几"]:
        matches = find_relevant_skills(question=q)
        assert matches == [], f"query {q!r} 不应命中任何 skill，实际：{[s['name'] for s in matches]}"


def test_tool_name_trigger():
    """tool_name=query_topics + tool_args={sentiment: negative} 应命中负面痛点"""
    matches = find_relevant_skills(
        question="随便问问",  # 不含任何关键词
        tool_name="query_topics",
        tool_args={"sentiment": "negative", "target": "steam:2358720", "level": "L1"},
    )
    names = [s["name"] for s in matches]
    assert "负面痛点聚焦" in names


def test_tool_name_trigger_arg_mismatch_no_hit():
    """tool_name 命中但 tool_args 不匹配 → 不应命中"""
    matches = find_relevant_skills(
        question="随便问问",
        tool_name="query_topics",
        tool_args={"sentiment": "positive"},  # 不是 negative
    )
    names = [s["name"] for s in matches]
    assert "负面痛点聚焦" not in names, f"positive 不应命中负面痛点 skill，实际：{names}"


def test_priority_sort_desc():
    """多个 skill 命中时按 priority desc 排序"""
    matches = find_relevant_skills(question="给我举几个原声吐槽痛点最近的例子")
    # 同时命中"负面痛点"(10) + "原声引用"(5)
    if len(matches) >= 2:
        assert matches[0]["priority"] >= matches[1]["priority"]


def test_compose_skill_prompts_empty():
    """空列表 → 空字符串（chat.py 直接拼到 system prompt）"""
    assert compose_skill_prompts([]) == ""


def test_compose_skill_prompts_contains_names():
    """应包含 skill name 与 system_prompt 内容"""
    out = compose_skill_prompts([{
        "name": "测试 skill",
        "system_prompt": "这是测试内容",
        "priority": 10,
    }])
    assert "测试 skill" in out
    assert "这是测试内容" in out
    assert "skill 注入" in out  # 标题段


def test_missing_skill_file_graceful(tmp_path, monkeypatch):
    """skills 目录不存在时不崩（load_skills 返回空）"""
    from src.agent import skills as skills_mod
    monkeypatch.setattr(skills_mod, "SKILLS_DIR", tmp_path / "nonexistent")
    _reset_cache()
    assert load_skills() == []


def test_tool_schemas_loaded_from_yaml():
    """4 个 tool schema 应从 config/agent/tools.yaml 加载（2026-09-09 §3 红线修复）

    锁住：TOOL_SCHEMAS 数量恒为 4、名称集合稳定、schema 字段齐全。
    任何 yaml 改坏或文件丢失 → 此测试会失败（fail-fast）。
    """
    from src.agent.tools import TOOL_SCHEMAS, reload_tool_schemas

    reload_tool_schemas()  # 清 lru_cache；保证拿到 yaml 最新内容
    assert isinstance(TOOL_SCHEMAS, list)
    assert len(TOOL_SCHEMAS) == 4, f"应 4 个 tool，实际 {len(TOOL_SCHEMAS)}：{[s.get('function', {}).get('name') for s in TOOL_SCHEMAS]}"

    names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert names == {"query_overview", "query_topics", "query_comments", "search_docs"}

    # 所有 schema 都符合 OpenAI function calling 结构
    for s in TOOL_SCHEMAS:
        assert s["type"] == "function"
        assert "name" in s["function"]
        assert "description" in s["function"]
        assert s["function"]["parameters"]["type"] == "object"
        assert "properties" in s["function"]["parameters"]

    # 关键参数必填：target 在 3 个数据类 tool 中必填
    for tool_name in ("query_overview", "query_topics", "query_comments"):
        s = next(x for x in TOOL_SCHEMAS if x["function"]["name"] == tool_name)
        assert "target" in s["function"]["parameters"]["required"], f"{tool_name} 应要求 target"


def test_tool_schemas_yaml_missing_fails_fast(tmp_path, monkeypatch):
    """tools.yaml 缺失 → 启动期 RuntimeError（不静默给空 list 让 LLM 调不到 tool）

    用 monkeypatch 把 _TOOLS_YAML 指向不存在的路径后 reload，
    期望抛 RuntimeError 含明确提示。
    """
    from src.agent import tools as t_mod

    monkeypatch.setattr(t_mod, "_TOOLS_YAML", tmp_path / "nonexistent_tools.yaml")
    t_mod._load_tool_schemas.cache_clear()

    with pytest.raises(RuntimeError, match="tools.yaml"):
        t_mod._load_tool_schemas()

    # 不需要恢复（monkeypatch 自动还原；其他测试用原文件不受影响）
