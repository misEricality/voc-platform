"""按 GDT v3.1.1 重建黄金集并生成 pytest fixture。

数据来源：
- data/validation/_sample_500_from_xlsx.json（由 export_validation_sample.py 生成）
- data/validation/_golden_overrides.json（机械映射无法覆盖的人工校正项）

输出：
- tests/fixtures/golden_match_set.json（回归测试用）
- data/validation/golden_gdt_500.json（带审计字段，便于回看哪些样本被排除）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.analyzers.normalize import build_l3_mapping, load_hierarchy, map_l3_to_path

SAMPLE = Path("data/validation/_sample_500_from_xlsx.json")
OVERRIDES = Path("data/validation/_golden_overrides.json")
FIXTURE = Path("tests/fixtures/golden_match_set.json")
AUDIT = Path("data/validation/golden_gdt_500.json")

TAXONOMY_VERSION = "gdt-3.1.1"

# 语义不完整/纯玩梗/无法稳定程序匹配的样本，不进入回归门禁。
EXCLUDE_OPINION_IDS = {4048, 3964, 4666}

MECHANICAL_MAP = {
    "整体评价": "总体体验评价",
    "整活/梗": "网络梗与段子",
    "核心玩法": "总体体验评价",
    "战斗系统": "战斗系统",
    "动作系统": "动作系统",
    "游戏系统": "规则逻辑",
    "机制规则": "规则逻辑",
    "解谜": "解谜机制",
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
    "重玩价值": "多周目与重复可玩性",
    "关卡设计": "关卡布局设计",
    "地图探索": "地图探索机制",
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


def main() -> None:
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    hierarchy = load_hierarchy()
    mapping = build_l3_mapping(hierarchy)

    audit_rows = []
    fixture_items = []
    for row in sample:
        oid = row["opinion_id"]
        old_l3 = row["old_full_path"].rsplit("/", 1)[-1]
        excluded = oid in EXCLUDE_OPINION_IDS
        override = overrides.get(str(oid))
        if override and "/" in override:
            new_path = override
        else:
            new_l3 = override or MECHANICAL_MAP.get(old_l3)
            new_path = map_l3_to_path(new_l3, mapping) if new_l3 else None

        audit_rows.append(
            {
                "opinion_id": oid,
                "phrase": row["phrase"],
                "old_full_path": row["old_full_path"],
                "new_full_path": new_path,
                "excluded": excluded,
                "label_validation": row["label_validation"],
            }
        )

        if not excluded and new_path:
            fixture_items.append(
                {
                    "phrase": row["phrase"],
                    "full_path": new_path,
                }
            )

    fixture = {
        "taxonomy_version": TAXONOMY_VERSION,
        "items": fixture_items,
    }
    FIXTURE.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    AUDIT.write_text(
        json.dumps(audit_rows, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"golden fixture items: {len(fixture_items)}")
    print(f"audit rows: {len(audit_rows)}")
    print(f"excluded: {sum(1 for r in audit_rows if r['excluded'])}")


if __name__ == "__main__":
    main()
