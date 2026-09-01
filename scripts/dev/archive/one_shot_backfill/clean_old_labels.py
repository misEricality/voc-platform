"""P9 阶段1 · 旧标签清洗：把 GDT v3.0（旧 L1×7）存量标签迁移到 GDT v3.1.1（L1×10）。

幂等：只改「旧标签名」映射到「新标签名」，绝不动已是新体系的标签。
可安全地在全量重打前 / 后各跑一次（重打会覆盖多数评论，本脚本兜底重打未覆盖的行）。

用法：
    python scripts/dev/clean_old_labels.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from src.storage.db import init_db, Comment, CommentOpinion
from src.analyzers.normalize import build_l3_mapping, load_hierarchy, map_l3_to_path

# 旧 L1 → 新 L1（comments.topic 用）
OLD_L1_MAP = {
    "其他": "综合与元表达",
    "玩法与内容": "机制与内容",
    "叙事与表现": "叙事与世界观",
    "操作与交互": "操控与交互",
    "商业与发行": "商业与运营",
    "社区与生态": "社区与社交",
    # 技术与性能 新旧同名，无需迁移
}

# 旧 L3 → 新 L3（comment_opinions.full_path 的末段用；继承 rebuild_golden_set.py 的 MECHANICAL_MAP 并补齐）
OLD_L3_MAP = {
    "整体评价": "总体体验评价",
    "核心玩法": "总体体验评价",
    "战斗系统": "战斗系统",
    "技能系统": "角色养成·技能树",
    "动作系统": "动作系统",
    "游戏系统": "规则逻辑",
    "机制规则": "规则逻辑",
    "数值平衡": "数值设计",
    "职业平衡": "职业·角色平衡",
    "角色强度": "职业·角色平衡",
    "版本调整": "版本强弱分布",
    "克制关系": "机制克制关系",
    "流程长度": "流程长度·主线时长",
    "游戏时长": "流程长度·主线时长",
    "关卡数量": "关卡布局设计",
    "支线内容": "支线任务故事",
    "通关后内容": "终局·通关后内容(Endgame)",
    "内容规模": "内容体量与规模",
    "多周目": "多周目与重复可玩性",
    "随机要素": "多周目与重复可玩性",
    "刷宝": "刷宝机制",
    "重玩价值": "多周目与重复可玩性",
    "关卡设计": "关卡布局设计",
    "地图探索": "地图探索机制",
    "解谜": "解谜机制",
    "开放世界": "开放世界设计",
    "区域布局": "箱庭空间设计",
    "难度曲线": "难度曲线",
    "难度选项": "难度梯度",
    "挑战性": "挑战性考验",
    "上手门槛": "上手门槛",
    "主线": "主线剧情",
    "支线": "支线任务故事",
    "结局": "多结局设计",
    "情节结构": "叙事节奏与结构",
    "叙事节奏": "叙事节奏与结构",
    "剧情篇幅": "叙事节奏与结构",
    "角色塑造": "角色塑造与性格",
    "人物刻画": "角色塑造与性格",
    "背景设定": "世界观·背景设定",
    "题材来源": "世界观·背景设定",
    "文化内涵": "文化内涵与隐喻",
    "原创设定": "世界观·背景设定",
    "画质": "画面分辨率·清晰度",
    "美术风格": "美术视觉风格",
    "建模": "3D模型精细度",
    "特效": "粒子与光影特效",
    "光影": "粒子与光影特效",
    "动画演出": "动画演出·CG过场",
    "场景设计": "场景构图与美感",
    "BGM": "背景音乐(BGM)",
    "配乐": "原声带(OST)·配乐",
    "音效": "战斗·环境音效",
    "主题曲": "主题曲·片尾曲",
    "片尾曲": "主题曲·片尾曲",
    "中文配音": "中文配音表现",
    "翻译质量": "文本翻译准确性",
    "字幕": "字幕显示与同步",
    "本地化": "文本翻译准确性",
    "打击感": "打击感与震动反馈",
    "按键响应": "按键输入响应·延迟",
    "移动控制": "角色移动与视角控制",
    "连招流畅度": "招式·连招衔接",
    "手柄支持": "手柄支持与适配",
    "键鼠": "键鼠操控体验",
    "自定义按键": "自定义按键映射",
    "触屏": "手柄支持与适配",
    "辅助功能": "无障碍辅助功能",
    "UI": "UI视觉与布局",
    "HUD": "HUD抬头显示",
    "菜单": "菜单导航与逻辑",
    "新手引导": "新手引导与教程",
    "地图导航": "地图探索机制",
    "帧数": "帧数波动·掉帧",
    "掉帧": "帧数波动·掉帧",
    "卡顿": "画面卡顿·顿挫",
    "加载时间": "场景加载速度",
    "闪退": "程序闪退",
    "崩溃": "程序崩溃·报错",
    "Bug": "运行Bug·代码漏洞",
    "存档": "存档损坏·丢失",
    "配置要求": "硬件配置要求",
    "显卡适配": "显卡适配与优化",
    "画质设置": "渲染品质",
    "渲染优化": "渲染品质",
    "DLSS": "DLSS·FSR超采样",
    "FSR": "DLSS·FSR超采样",
    "服务器": "服务器连通性·状态",
    "延迟": "网络延迟(Ping)",
    "丢包": "丢包·网络掉线",
    "匹配": "匹配机制(网络侧)",
    "加速器": "加速器依赖度",
    "云存档": "云存档同步",
    "成就": "成就系统",
    "跨平台": "跨平台联机·进度",
    "家庭共享": "跨平台联机·进度",
    "定价": "售价策略",
    "折扣": "折扣促销力度",
    "史低": "历史最低价",
    "性价比": "性价比与心理预期",
    "DLC": "DLC·付费扩展包",
    "抽卡": "抽卡·开箱概率",
    "战令": "战令·通行证设计",
    "季票": "DLC·付费扩展包",
    "抢先体验": "抢先体验(EA)完成度",
    "更新频率": "内容更新频率",
    "版本": "版本强弱分布",
    "停更": "停更与维护态度",
    "跳票": "停更与维护态度",
    "客服响应": "客服响应与退款服务",
    "退款": "客服响应与退款服务",
    "封禁申诉": "客服响应与退款服务",
    "账号": "客服响应与退款服务",
    "外挂": "外挂与作弊现象",
    "匹配质量": "匹配机制(网络侧)",
    "队友": "队友行为(坑·挂机·送头)",
    "玩家氛围": "社区风气与玩家素质",
    "举报": "反作弊系统(VAC·EAC)",
    "创意工坊": "创意工坊",
    "Mod": "Mod模组生态",
    "玩家社区": "社区风气与玩家素质",
    "活动": "社区活动与赛事",
    "赛事": "社区活动与赛事",
    "好友": "组队开黑·匹配社交",
    "组队": "组队开黑·匹配社交",
    "公会": "公会·战队系统",
    "语音": "语音沟通体验",
}

# 整活/梗：旧路径「其他/整活/梗」（L2/L3 同名带斜杠），直接整路径替换
OLD_FULL_PATH_OVERRIDES = {
    "其他/整活/梗": "综合与元表达/社区梗与反讽/网络梗与段子",
    "其他/整活/梗/整活/梗": "综合与元表达/社区梗与反讽/网络梗与段子",
}


def migrate_full_path(fp: str, l3_mapping: dict) -> str | None:
    """旧 full_path → 新 full_path；无法迁移返回 None（保持原样）。"""
    if fp in OLD_FULL_PATH_OVERRIDES:
        return OLD_FULL_PATH_OVERRIDES[fp]
    segs = fp.split("/")
    if len(segs) < 3:
        return None
    l3_old = segs[-1]
    l3_new = OLD_L3_MAP.get(l3_old)
    if not l3_new:
        return None
    new_path = map_l3_to_path(l3_new, l3_mapping)
    return new_path


def main() -> None:
    parser = argparse.ArgumentParser(description="旧标签清洗（v3.0 → v3.1.1）")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不落盘")
    args = parser.parse_args()

    engine, SessionLocal = init_db()
    session = SessionLocal()

    hierarchy = load_hierarchy()
    l3_mapping = build_l3_mapping(hierarchy)

    # 1. comments.topic
    topic_before = Counter()
    topic_after = Counter()
    comment_changed = 0
    comments = list(session.execute(select(Comment)).scalars())
    for c in comments:
        topic_before[c.topic] += 1
        if c.topic in OLD_L1_MAP:
            c.topic = OLD_L1_MAP[c.topic]
            comment_changed += 1
        topic_after[c.topic] += 1

    # 2. comment_opinions.full_path
    path_before = Counter()
    path_after = Counter()
    opinion_changed = 0
    opinion_unmapped = Counter()
    opinions = list(session.execute(select(CommentOpinion)).scalars())
    for op in opinions:
        path_before[op.full_path] += 1
        new_path = migrate_full_path(op.full_path, l3_mapping)
        if new_path and new_path != op.full_path:
            op.full_path = new_path
            opinion_changed += 1
        elif new_path is None:
            opinion_unmapped[op.full_path] += 1
        path_after[op.full_path] += 1

    print("=" * 70)
    print(f"comments.topic 迁移 {comment_changed} 条")
    print(f"comment_opinions.full_path 迁移 {opinion_changed} 条")

    print("\n--- topic 迁移后 L1 分布（top）---")
    total_c = sum(topic_after.values()) or 1
    for t, c in topic_after.most_common(12):
        print(f"  {t!r:<16} {c:>5}  {c*100/total_c:5.1f}%")

    print("\n--- full_path 迁移后 L1 分布（top）---")
    l1_after = Counter()
    for fp, c in path_after.items():
        l1_after[fp.split("/")[0]] += c
    total_o = sum(l1_after.values()) or 1
    for l1, c in l1_after.most_common(14):
        print(f"  {l1:<16} {c:>5}  {c*100/total_o:5.1f}%")

    if opinion_unmapped:
        print("\n--- 未迁移的旧 full_path（应已在新体系或无需迁移）---")
        for fp, c in opinion_unmapped.most_common(30):
            print(f"  {c:>5}  {fp}")

    if args.dry_run:
        session.rollback()
        print("\n[dry-run] 未落盘，已回滚")
    else:
        session.commit()
        print("\n[已提交] 旧标签清洗完成")
    session.close()


if __name__ == "__main__":
    main()
