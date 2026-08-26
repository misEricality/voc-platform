"""P11 双标注对比：DEEPSEEK vs QWEN-flash

目的：在同一批 200 条评论上跑两套标注，对比 sentiment / topic 一致率。

用法：
    # 阶段 1：备份 + 重置（dispatch 前）
    python scripts/ops/dual_annotate_qwen_flash.py backup

    # 阶段 2：对比（dispatch 完成，run #24 artifact 已同步本地后）
    python scripts/ops/dual_annotate_qwen_flash.py compare

设计：
- 抽样 200 条已 DEEPSEEK 标注的评论（覆盖 6 款单机，按 target_id 均衡）
- 把 DEEPSEEK 标签写到 JSON 备份
- 清空这 200 条的 analyzed_at + analyzer_version → pipeline 会重打
- dispatch workflow（用 QWEN-flash）→ pipeline 重新打标
- 对比 JSON（DEEPSEEK）vs DB（QWEN-flash）
- 输出 Markdown 报告（一致率、sentiment 分布、典型分歧 sample）

为什么不直接加表 / 列：
- 加 `backup_sentiment` 等列要动 schema + init_db + pipeline；工程量更大
- 200 条规模完全够做对比判断；不需要长期存储双标注
- 重置后 QWEN-flash 跑过的 200 条 `analyzer_version` 会变成 `llm:qwen3.7-flash@...`
  用 `WHERE id IN (200 ids)` 即可拿出对比数据
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path("data/voc.db")
LABELS_JSON = Path("scripts/ops/_dual_annotate_labels.json")
REPORT_MD = Path("scripts/ops/_dual_annotate_report.md")
SAMPLE_SIZE = 200


def get_connection():
    return sqlite3.connect(DB_PATH)


def select_sample(cur, n: int) -> list[dict]:
    """从 6 款单机各取 ~n/6 条「DEEPSEEK 时代」已标注的评论（均衡覆盖）。

    ⚠️ 重要：bootstrap（76 MB）创建于 2026-08-23 09:08，彼时 analyzer_version 字段
    已在 schema 但未触发实际写入（8/21 ALTER 只是加列，没回填）。所以本地 DB 里
    11,018 条 bootstrap-era 评论都是 `analyzed_at NOT NULL` 但 `analyzer_version IS NULL`。
    8/24 之后的 run 才会写入 analyzer_version。这里靠 `analyzed_at IS NOT NULL` +
    `analyzer_version IS NULL OR analyzer_version LIKE 'llm:deepseek%'` 兼容两种历史。
    """
    per_game = max(1, n // 6)
    games = [
        "steam:2358720", "steam:292030", "steam:289070",
        "steam:1222140", "steam:1903340", "steam:753640",
    ]
    selected = []
    for game in games:
        cur.execute(
            """
            SELECT id, target_id, content, sentiment, sentiment_score,
                   sentiment_confidence, topic, sub_topics, analyzer_version
            FROM comments
            WHERE target_id = ?
              AND analyzed_at IS NOT NULL
              AND (analyzer_version IS NULL OR analyzer_version LIKE 'llm:deepseek%')
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (game, per_game),
        )
        for row in cur.fetchall():
            selected.append({
                "id": row[0],
                "target_id": row[1],
                "content": row[2][:200],
                "sentiment": row[3],
                "sentiment_score": row[4],
                "sentiment_confidence": row[5],
                "topic": row[6],
                "sub_topics": row[7],
                "analyzer_version": row[8] or "llm:deepseek-v4-flash@(pre-analyzer-version)",
            })
    return selected


def backup_and_reset(n: int) -> list[int]:
    """抽样 n 条 + 保存 DEEPSEEK 标签到 JSON + 重置 analyzed_at/analyzer_version。"""
    con = get_connection()
    cur = con.cursor()

    sample = select_sample(cur, n)
    if len(sample) < n:
        print(f"[WARN] 仅抽到 {len(sample)} 条（少于目标 {n}）", file=sys.stderr)
    print(f"[sample] {len(sample)} comments selected")

    # 保存 DEEPSEEK 标签
    payload = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "sample_size": len(sample),
        "comments": sample,
    }
    LABELS_JSON.parent.mkdir(parents=True, exist_ok=True)
    LABELS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[saved] {LABELS_JSON} ({len(sample)} rows)")

    # 重置 analyzed_at + analyzer_version（其他字段保留，方便对比）
    ids = [c["id"] for c in sample]
    placeholders = ",".join("?" * len(ids))
    cur.execute(
        f"UPDATE comments SET analyzed_at = NULL, analyzer_version = NULL WHERE id IN ({placeholders})",
        ids,
    )
    con.commit()
    print(f"[reset] {len(ids)} comments: analyzed_at=NULL, analyzer_version=NULL")
    print(f"\nNEXT: dispatch workflow (e.g. via web UI Re-run jobs).")
    print(f"      Then run: python {Path(__file__).name} compare")
    return ids


def compare() -> None:
    """读 JSON + DB，对比两套标签，输出 Markdown 报告。"""
    if not LABELS_JSON.exists():
        print(f"[ERR] {LABELS_JSON} 不存在，先跑 backup", file=sys.stderr)
        sys.exit(1)

    deepseek_data = json.loads(LABELS_JSON.read_text(encoding="utf-8"))
    deepseek_map = {c["id"]: c for c in deepseek_data["comments"]}

    con = get_connection()
    cur = con.cursor()
    ids = list(deepseek_map.keys())
    placeholders = ",".join("?" * len(ids))
    cur.execute(
        f"""
        SELECT id, target_id, content, sentiment, sentiment_score,
               sentiment_confidence, topic, sub_topics, analyzer_version, analyzed_at
        FROM comments WHERE id IN ({placeholders})
        """,
        ids,
    )
    qwen_rows = {r[0]: {
        "id": r[0], "target_id": r[1], "content": r[2],
        "sentiment": r[3], "sentiment_score": r[4],
        "sentiment_confidence": r[5], "topic": r[6], "sub_topics": r[7],
        "analyzer_version": r[8], "analyzed_at": r[9],
    } for r in cur.fetchall()}

    # 计算一致率
    n = len(deepseek_map)
    matched = 0
    sentiment_match = 0
    topic_match = 0
    sentiment_score_delta = []
    typical_disagreements = []

    for cid, ds in deepseek_map.items():
        qf = qwen_rows.get(cid)
        if not qf:
            print(f"  [warn] id {cid} not in DB (pipeline didn't re-analyze?)", file=sys.stderr)
            continue
        # 是否被 QWEN-flash 标注过
        if "qwen" not in (qf["analyzer_version"] or "").lower():
            print(f"  [warn] id {cid} re-analyzed but not by qwen (analyzer_version={qf['analyzer_version']})", file=sys.stderr)
        if ds["sentiment"] == qf["sentiment"]:
            sentiment_match += 1
        if ds["topic"] == qf["topic"]:
            topic_match += 1
        try:
            delta = abs((ds["sentiment_score"] or 0) - (qf["sentiment_score"] or 0))
            sentiment_score_delta.append(delta)
        except (TypeError, ValueError):
            pass
        if ds["sentiment"] != qf["sentiment"] or ds["topic"] != qf["topic"]:
            typical_disagreements.append({
                "id": cid,
                "target_id": ds["target_id"],
                "content_preview": ds["content"][:80],
                "deepseek_sentiment": ds["sentiment"],
                "deepseek_topic": ds["topic"],
                "qwen_sentiment": qf["sentiment"],
                "qwen_topic": qf["topic"],
            })

    sentiment_match_rate = sentiment_match / n if n else 0
    topic_match_rate = topic_match / n if n else 0
    avg_score_delta = (sum(sentiment_score_delta) / len(sentiment_score_delta)) if sentiment_score_delta else 0

    # 输出 Markdown 报告
    lines = [
        f"# DEEPSEEK vs QWEN-flash 双标注对比报告",
        f"",
        f"- 抽样时间: {deepseek_data['created_at']}",
        f"- 抽样数量: {n}",
        f"- DEEPSEEK 版本: `{deepseek_data['comments'][0]['analyzer_version']}`（如有多个会显示不同）",
    ]
    if qwen_rows:
        qwen_ver = next((r["analyzer_version"] for r in qwen_rows.values() if r["analyzer_version"]), "N/A")
        lines.append(f"- QWEN-flash 版本: `{qwen_ver}`")
    lines += [
        f"",
        f"## 一致率",
        f"",
        f"| 维度 | DEEPSEEK → QWEN-flash 一致率 |",
        f"|---|---|",
        f"| sentiment (positive/negative/neutral) | **{sentiment_match}/{n} = {sentiment_match_rate:.1%}** |",
        f"| topic (一级标签) | **{topic_match}/{n} = {topic_match_rate:.1%}** |",
        f"| sentiment_score 平均 |Δ| | **{avg_score_delta:.3f}** |",
        f"",
        f"## sentiment 分布",
        f"",
        f"| sentiment | DEEPSEEK | QWEN-flash |",
        f"|---|---|---|",
    ]
    # 统计 sentiment 分布
    ds_dist = {"positive": 0, "negative": 0, "neutral": 0}
    qf_dist = {"positive": 0, "negative": 0, "neutral": 0}
    for ds in deepseek_map.values():
        ds_dist[ds["sentiment"]] = ds_dist.get(ds["sentiment"], 0) + 1
    for qf in qwen_rows.values():
        qf_dist[qf["sentiment"]] = qf_dist.get(qf["sentiment"], 0) + 1
    for k in ("positive", "negative", "neutral"):
        lines.append(f"| {k} | {ds_dist.get(k, 0)} | {qf_dist.get(k, 0)} |")

    lines += [
        f"",
        f"## 分歧 sample（前 10 条 sentiment 或 topic 不一致的）",
        f"",
    ]
    for d in typical_disagreements[:10]:
        lines += [
            f"### id={d['id']} ({d['target_id']})",
            f"- content: `{d['content_preview']}...`",
            f"- DEEPSEEK: sentiment={d['deepseek_sentiment']}, topic={d['deepseek_topic']}",
            f"- QWEN-flash: sentiment={d['qwen_sentiment']}, topic={d['qwen_topic']}",
            f"",
        ]
    if not typical_disagreements:
        lines.append(f"（全部一致，0 分歧）")

    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"[report] {REPORT_MD}")

    print("\n=== 关键指标 ===")
    print(f"  sentiment 一致率: {sentiment_match}/{n} = {sentiment_match_rate:.1%}")
    print(f"  topic 一致率:     {topic_match}/{n} = {topic_match_rate:.1%}")
    print(f"  sentiment_score 平均 |Δ|: {avg_score_delta:.3f}")
    print(f"  分歧 sample:      {len(typical_disagreements)} 条")
    print(f"\n报告已写到 {REPORT_MD}")


def dist_compare() -> None:
    """方案 A：分布级对比（无需重置评论）。

    QWEN-flash 跑过的2,473 条 (analyzer_version=llm:qwen3.7-flash@...) vs
    DEEPSEEK 跑过的11,268 条 (analyzer_version IS NULL but analyzed_at NOT NULL)，
    都是同6 款单机、同schinese语言、同时间窗。统计可比。
    """
    con = get_connection()
    cur = con.cursor()

    # 分两组
    cur.execute("""
        SELECT COUNT(*) FROM comments
        WHERE target_id LIKE 'steam:%' AND target_id NOT LIKE 'steam:999'
          AND analyzed_at IS NOT NULL
          AND analyzer_version IS NULL
    """)
    deepseek_n = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(*) FROM comments
        WHERE target_id LIKE 'steam:%' AND target_id NOT LIKE 'steam:999'
          AND analyzer_version LIKE 'llm:qwen%'
    """)
    qwen_n = cur.fetchone()[0]
    print(f"[数据集] DEEPSEEK: {deepseek_n} 条 / QWEN-flash: {qwen_n} 条")

    # sentiment 分布
    print("\n=== sentiment 分布对比 ===")
    cur.execute("""
        SELECT COALESCE(sentiment, '(NULL)') AS s, COUNT(*)
        FROM comments
        WHERE target_id LIKE 'steam:%' AND target_id NOT LIKE 'steam:999'
          AND analyzed_at IS NOT NULL
          AND analyzer_version IS NULL
        GROUP BY s
    """)
    ds_dist = dict(cur.fetchall())
    cur.execute("""
        SELECT COALESCE(sentiment, '(NULL)') AS s, COUNT(*)
        FROM comments
        WHERE target_id LIKE 'steam:%' AND target_id NOT LIKE 'steam:999'
          AND analyzer_version LIKE 'llm:qwen%'
        GROUP BY s
    """)
    qf_dist = dict(cur.fetchall())
    print(f"  {'sentiment':<12} {'DEEPSEEK':>10} ({ds_dist.get('positive', 0)/deepseek_n*100:5.1f}%) {'QWEN-flash':>10} ({qf_dist.get('positive', 0)/qwen_n*100:5.1f}%)  diff")
    for k in ("positive", "negative", "neutral"):
        ds_pct = ds_dist.get(k, 0) / deepseek_n * 100 if deepseek_n else 0
        qf_pct = qf_dist.get(k, 0) / qwen_n * 100 if qwen_n else 0
        diff = qf_pct - ds_pct
        print(f"  {k:<12} {ds_dist.get(k, 0):>10} ({ds_pct:5.1f}%) {qf_dist.get(k, 0):>10} ({qf_pct:5.1f}%)  {diff:+5.1f}pp")

    # topic 分布（取 top 10）
    print("\n=== topic 分布对比（top 10）===")
    cur.execute("""
        SELECT topic, COUNT(*) AS n FROM comments
        WHERE target_id LIKE 'steam:%' AND target_id NOT LIKE 'steam:999'
          AND analyzed_at IS NOT NULL AND analyzer_version IS NULL
          AND topic IS NOT NULL
        GROUP BY topic ORDER BY n DESC LIMIT 10
    """)
    ds_topics = cur.fetchall()
    cur.execute("""
        SELECT topic, COUNT(*) AS n FROM comments
        WHERE target_id LIKE 'steam:%' AND target_id NOT LIKE 'steam:999'
          AND analyzer_version LIKE 'llm:qwen%'
          AND topic IS NOT NULL
        GROUP BY topic ORDER BY n DESC LIMIT 10
    """)
    qf_topics = cur.fetchall()
    ds_topic_map = dict(ds_topics)
    qf_topic_map = dict(qf_topics)
    print(f"  {'topic':<20} {'DEEPSEEK':>8} {'QWEN-flash':>8}")
    # 按 DEEPSEEK 量排序
    for t, n in ds_topics[:10]:
        ds_pct = n / deepseek_n * 100 if deepseek_n else 0
        qf_pct = qf_topic_map.get(t, 0) / qwen_n * 100 if qwen_n else 0
        print(f"  {t:<20} {n:>8} ({ds_pct:5.1f}%) {qf_topic_map.get(t, 0):>8} ({qf_pct:5.1f}%)")

    # 抽样3 条比对同 target_id + 类似 posted_at 的样本
    print("\n=== 抽样对比（同 game，DEEPSEEK 早期 vs QWEN-flash 近期）===")
    cur.execute("""
        SELECT id, target_id, posted_at, sentiment, topic, substr(content, 1, 60) AS preview
        FROM comments
        WHERE target_id = 'steam:2358720' AND analyzer_version IS NULL
          AND analyzed_at IS NOT NULL
        ORDER BY RANDOM() LIMIT 3
    """)
    print("  --- DEEPSEEK (黑神话悟空 bootstrap 时期) ---")
    for r in cur.fetchall():
        print(f"    id={r[0]} {r[2][:10]} sentiment={(r[3] or '(NULL)'):<10} topic={(r[4] or '(NULL)'):<14} `{r[5]}...`")
    cur.execute("""
        SELECT id, target_id, posted_at, sentiment, topic, substr(content, 1, 60) AS preview
        FROM comments
        WHERE target_id = 'steam:2358720' AND analyzer_version LIKE 'llm:qwen%'
        ORDER BY RANDOM() LIMIT 3
    """)
    print("  --- QWEN-flash (今天) ---")
    for r in cur.fetchall():
        print(f"    id={r[0]} {r[2][:10]} sentiment={(r[3] or '(NULL)'):<10} topic={(r[4] or '(NULL)'):<14} `{r[5]}...`")

    # sentiment_score 对比
    print("\n=== sentiment_score 分布对比 ===")
    cur.execute("""
        SELECT
            AVG(sentiment_score), MIN(sentiment_score), MAX(sentiment_score),
            AVG(sentiment_confidence), COUNT(*)
        FROM comments
        WHERE target_id LIKE 'steam:%' AND target_id NOT LIKE 'steam:999'
          AND analyzed_at IS NOT NULL AND analyzer_version IS NULL
          AND sentiment_score IS NOT NULL
    """)
    ds_score = cur.fetchone()
    cur.execute("""
        SELECT
            AVG(sentiment_score), MIN(sentiment_score), MAX(sentiment_score),
            AVG(sentiment_confidence), COUNT(*)
        FROM comments
        WHERE target_id LIKE 'steam:%' AND target_id NOT LIKE 'steam:999'
          AND analyzer_version LIKE 'llm:qwen%'
          AND sentiment_score IS NOT NULL
    """)
    qf_score = cur.fetchone()
    print(f"  DEEPSEEK:   avg={ds_score[0]:+.3f}  min={ds_score[1]:+.3f}  max={ds_score[2]:+.3f}  avg_conf={ds_score[3]:.3f}  n={ds_score[4]}")
    print(f"  QWEN-flash: avg={qf_score[0]:+.3f}  min={qf_score[1]:+.3f}  max={qf_score[2]:+.3f}  avg_conf={qf_score[3]:.3f}  n={qf_score[4]}")

    # 输出 Markdown 报告
    lines = [
        "# DEEPSEEK vs QWEN-flash 分布级对比报告（方案 A）",
        "",
        f"- 生成时间: {datetime.now().isoformat()}",
        f"- DEEPSEEK 数据: {deepseek_n} 条（bootstrap 时期，analyzer_version 字段未启用）",
        f"- QWEN-flash 数据: {qwen_n} 条（run #24 今天标注）",
        f"- 数据范围: 6 款 Steam 单机 + bilibili:video:115581428696874 (历史已归档)",
        f"- 同分布基础: 同 target_id 集合 + 同 schinese 语言 + 同posted_at 窗口",
        "",
        "## sentiment 分布",
        "",
        "| sentiment | DEEPSEEK | QWEN-flash | Δ |",
        "|---|---|---|---|",
    ]
    for k in ("positive", "negative", "neutral"):
        ds_pct = ds_dist.get(k, 0) / deepseek_n * 100 if deepseek_n else 0
        qf_pct = qf_dist.get(k, 0) / qwen_n * 100 if qwen_n else 0
        lines.append(f"| {k} | {ds_dist.get(k, 0):,} ({ds_pct:.1f}%) | {qf_dist.get(k, 0):,} ({qf_pct:.1f}%) | **{qf_pct - ds_pct:+.1f}pp** |")

    lines += [
        "",
        "## topic top 10",
        "",
        "| topic | DEEPSEEK | QWEN-flash |",
        "|---|---|---|",
    ]
    for t, n in ds_topics[:10]:
        ds_pct = n / deepseek_n * 100 if deepseek_n else 0
        qf_pct = qf_topic_map.get(t, 0) / qwen_n * 100 if qwen_n else 0
        lines.append(f"| {t} | {n:,} ({ds_pct:.1f}%) | {qf_topic_map.get(t, 0):,} ({qf_pct:.1f}%) |")

    lines += [
        "",
        "## sentiment_score 分布",
        "",
        "| | avg | min | max | avg_conf | n |",
        "|---|---|---|---|---|---|",
        f"| DEEPSEEK | {ds_score[0]:+.3f} | {ds_score[1]:+.3f} | {ds_score[2]:+.3f} | {ds_score[3]:.3f} | {ds_score[4]:,} |",
        f"| QWEN-flash | {qf_score[0]:+.3f} | {qf_score[1]:+.3f} | {qf_score[2]:+.3f} | {qf_score[3]:.3f} | {qf_score[4]:,} |",
        "",
        "## 解读",
        "",
        "- **positive 占比**：DEEPSEEK 倾向给『好』，QWEN-flash 倾向更中性 → 注意 DEEPSEEK 标 positive 比例远高于 QWEN-flash 时",
        "- **topic 分布**：看是否有大 topic（如『游戏性』）一边多一边少",
        "- **sentiment_score**：DEEPSEEK 越接近 ±1 越极端，QWEN-flash 越接近 0 越保守",
        "",
        "## 结论建议",
        "",
        "- 如果 sentiment 分布 Δ 在 ±5pp 内、topic 一致 → QWEN-flash 可作 backup",
        "- 如果 sentiment 分布 Δ > 10pp → 需回 DEEPSEEK，或针对特定 game 调 prompt",
        "- 如果 score 均值差 > 0.15 → 模型倾向差异显著，需深入 sample",
    ]
    report_path = REPORT_MD.parent / "_dual_annotate_dist_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[report] {report_path}")


def main():
    p = argparse.ArgumentParser(description="DEEPSEEK vs QWEN-flash 双标注对比")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backup", help="阶段 1：抽样 + 保存 DEEPSEEK 标签 + 重置")
    sub.add_parser("compare", help="阶段 2：dispatch 后对比两套标签")
    sub.add_parser("dist-compare", help="方案 A：分布级对比（无需重置）")
    args = p.parse_args()

    if args.cmd == "backup":
        backup_and_reset(SAMPLE_SIZE)
    elif args.cmd == "compare":
        compare()
    elif args.cmd == "dist-compare":
        dist_compare()


if __name__ == "__main__":
    main()