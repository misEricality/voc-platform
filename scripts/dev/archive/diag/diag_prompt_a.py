"""对照组：简化版强制枚举 prompt（诊断质量下降根因）"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI

client = OpenAI(api_key=os.getenv('DEEPSEEK_API_KEY'), base_url='https://api.deepseek.com/v1')

samples = [
    '和朋友开黑最快乐，天天五排上分',
    '打击感超爽但优化太差，卡成PPT，闪退好几次',
]

L3_LIST = ('核心玩法,战斗系统,技能系统,动作系统,游戏系统,机制规则,数值平衡,职业平衡,角色强度,'
           '版本调整,克制关系,流程长度,游戏时长,关卡数量,支线内容,通关后内容,内容规模,多周目,'
           '随机要素,刷宝,重玩价值,关卡设计,地图探索,解谜,开放世界,区域布局,难度曲线,难度选项,'
           '挑战性,上手门槛,主线,支线,结局,情节结构,叙事节奏,剧情篇幅,角色塑造,人物刻画,背景设定,'
           '题材来源,文化内涵,原创设定,画质,美术风格,建模,特效,光影,动画演出,场景设计,BGM,配乐,'
           '音效,主题曲,片尾曲,中文配音,翻译质量,字幕,本地化,打击感,按键响应,移动控制,连招流畅度,'
           '手柄支持,键鼠,自定义按键,触屏,辅助功能,UI,HUD,菜单,新手引导,地图导航,帧数,掉帧,卡顿,'
           '加载时间,闪退,崩溃,Bug,存档,配置要求,显卡适配,画质设置,渲染优化,DLSS,FSR,服务器,延迟,'
           '丢包,匹配,加速器,云存档,成就,跨平台,家庭共享,定价,折扣,史低,性价比,DLC,抽卡,战令,'
           '季票,抢先体验,更新频率,版本,停更,跳票,客服响应,退款,封禁申诉,账号,外挂,匹配质量,队友,'
           '玩家氛围,举报,创意工坊,Mod,玩家社区,活动,赛事,好友,组队,公会,语音,整体评价,整活/梗')

prompt_tpl = """请分析以下消费者评论的情感与主题。

【评论文本】
{text}

【可用 L3 标签（必须精确选择，禁止自造）】
{L3}

请按以下 JSON 输出：
{{
  "sentiment": "positive|negative|neutral",
  "sentiment_score": -1到1,
  "topic": "从一级标签选：玩法与内容/叙事与表现/操作与交互/技术与性能/商业与发行/社区与生态/其他",
  "opinions": [
    {{"l3": "必须从词表选", "sentiment": "positive|negative|neutral", "sentiment_score": -1到1, "quote": "原声片段", "is_core": true}}
  ]
}}

严格规则：
1. 每条有效观点都要输出 opinions
2. 整活/梗类选"整活/梗"
3. 无具体场景的夸/贬选"整体评价"
4. 非中文/乱码/时间纪念 → opinions 为空
5. 整体情感=sentiment 代表核心观点(is_core)的情感"""

for s in samples:
    resp = client.chat.completions.create(
        model='deepseek-chat',
        messages=[
            {'role': 'system', 'content': '你是资深消费者洞察分析师，严格按 JSON 输出。'},
            {'role': 'user', 'content': prompt_tpl.format(text=s, L3=L3_LIST)},
        ],
        temperature=0.1,
        response_format={'type': 'json_object'},
        timeout=60,
    )
    content = resp.choices[0].message.content
    print(f'原声: {s[:45]}')
    print(f'  输出: {content[:400]}')
    print()