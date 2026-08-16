# 下一代 AI 游戏洞察系统 (AI Game VOC Insights Architecture) 技术规范文档

> **文档状态**：Draft for Vibe Coding / Implementation
> **面向对象**：AI 编程助手（Cursor / Claude Dev / Windsurf / Copilot）、后端工程师、LLM Prompt 工程师
> **版本**：v1.0.0

---

## 1. 系统架构概述 (System Architecture)

本系统旨在建设下一代游戏 VOC（Voice of Customer）认知洞察引擎，采用 **“监控 + 诊断” 双轨解耦架构**：

```text
                  ┌──────────────────────────────────────────┐
                  │          全量玩家评论文本 (Raw VOC)       │
                  └────────────────────┬─────────────────────┘
                                       │
                      ┌────────────────┴────────────────┐
                      ▼                                 ▼
         【轨道 A：GDT 监控分类体系】           【轨道 B：PEDM 体验诊断模型】
         * 定位：WHAT & Where (大盘指标)       * 定位：WHY & How (专家诊断)
         * 结构：静态 10 大正交树 (L1-L3)      * 结构：多维图谱 Schema (E × P × Entity)
         * 算力：高吞吐、低成本 (轻量模型)      * 算力：高智能、按需触发 (LLM Structuring)
                      │                                 │
                      │ (生成 BI 统计大盘)              │ (手动框选 / L3.5 穿透)
                      ▼                                 ▼
         ┌─────────────────────────┐       ┌─────────────────────────┐
         │     监控大盘 (Spike)    ├──────>│   研发诊断报告 (Tasks)  │
         └─────────────────────────┘       └─────────────────────────┘

```

### 1.1 双轨分工原则

1. **轨道 A：GDT 游戏功能监控体系 (Game Domain Taxonomy)**
* **职责**：全量文本实时打标。回答“游戏中的哪个**客观组件**出了多少量的正/负评”。
* **特点**：高吞吐、低成本、指标可跨版本聚合对比。


2. **轨道 B：PEDM 玩家体验诊断模型 (Player Experience Diagnosis Model)**
* **职责**：对特定问题下钻深层归因。回答“玩家的哪种**主观心理**收到了何种**设计缺陷**的伤害”。
* **特点**：高智能、结构化提取、输出可直接派发给策划/工程师的修改建议。



### 1.2 L3.5 动态微话题下钻机制 (MVP 阶段)

* **定位**：L3.5 并非静态 YAML 树节点，而是针对特定 $L3$ 实体在选中时间段内的**动态数据视图（Dynamic Topic Clusters）**。
* **触发方式**：**手动触发 (Manual On-Demand Trigger)**。分析师在看板框选时间段与 $L3$ 节点后点击下钻，调用 LLM 实时聚类。
* **隔离边界**：L3.5 严格限定为**现象/对象微分类**（如 `[闪避无敌帧窗口期]`），**严禁包含归因与心理推演**，确保不与 PEDM 体验模型产生边界侵蚀。

---

## 2. 数据模型定义 (TypeScript Data Interfaces)

为便于 vibe coding 直接生成代码，以下定义系统核心接口类型：

```typescript
// 1. 原始评论输入数据结构
export interface RawComment {
  comment_id: string;
  game_id: string;
  timestamp: string; // ISO8601
  text: string;
  rating?: number;
  playtime_hours?: number;
  platform?: string;
}

// 2. 轨道 A：GDT 全量打标输出结果
export interface GDTLabelResult {
  comment_id: string;
  tags: Array<{
    l1: string; // 对应 GDT L1
    l2: string; // 对应 GDT L2
    l3: string; // 对应 GDT L3
    sentiment: 'Positive' | 'Neutral' | 'Negative';
  }>;
}

// 3. L3.5 动态微话题聚类结果 (按需生成)
export interface L35TopicClusterResult {
  l3_anchor: string; // 关联的 GDT L3 节点名称
  time_window: { start: string; end: string };
  clusters: Array<{
    topic_id: string;
    topic_name: string; // 微现象短语，如 "闪避无敌帧窗口期"
    hit_count: number;
    volume_ratio: string;
    sample_comment_ids: string[];
  }>;
}

// 4. 轨道 B：PEDM 体验诊断输出结果
export interface PEDMDiagnosisResult {
  comment_id: string;
  scenarios: Array<{
    experience_dimension: string; // 对应 PEDM E1-E6 / E0
    problem_pattern: string;       // 对应 PEDM P1-P7 / P0
    target_entity_ref: string;     // 必须指针强关联 GDT L3 节点路径
    impact: {
      severity: 'S_Critical' | 'S_High' | 'S_Medium' | 'S_Low';
      user_intent: 'I_Churn_Risk' | 'I_Bug_Report' | 'I_Constructive_Advice' | 'I_Emotional_Vent';
    };
    symptom_description: string;   // 现象精炼摘要
    actionable_input_for_dev: string | null; // 给研发/策划的具体 Task 建议，若为 P0 则为 null
  }>;
}

```

---

## 3. 标签体系一：GDT 游戏功能监控体系 (Game Domain Taxonomy)

> 负责游戏客体组件的分类统计，遵循 **MECE 原则**，全量穷尽游戏各子系统。

### 3.1 完全定义 (YAML 格式)

```yaml
# ============================================================
# GDT (Game Domain Taxonomy) v3.1 完全树状词表
# 用于全量文本的定量分类与 BI 看板堆叠图展现
# ============================================================

GDT_Taxonomy:
  # ---------------- L1-01 机制与内容 ----------------
  - l1: 机制与内容
    l2_list:
      - l2: 核心机制与循环
        l3_list: [战斗系统, 动作系统, 解谜机制, 刷宝机制, 生存与建造, 资源管理, 规则逻辑]
      - l2: 关卡与空间
        l3_list: [关卡布局设计, 地图探索机制, 开放世界设计, 箱庭空间设计]
      - l2: 平衡性与数值
        l3_list: [数值设计, 职业/角色平衡, 版本强弱分布, 机制克制关系]
      - l2: 难度与成长
        l3_list: [难度曲线, 难度梯度, 挑战性考验, 上手门槛, 角色养成/技能树]
      - l2: 内容量与生命周期
        l3_list: [流程长度/主线时长, 内容体量与规模, 终局/通关后内容(Endgame), 多周目与重复可玩性]

  # ---------------- L1-02 操控与交互 ----------------
  - l1: 操控与交互
    l2_list:
      - l2: 操作手感与反馈
        l3_list: [打击感与震动反馈, 按键输入响应/延迟, 角色移动与视角控制, 招式/连招衔接]
      - l2: 设备操控适配
        l3_list: [手柄支持与适配, 键鼠操控体验, 自定义按键映射, 无障碍辅助功能]
      - l2: 界面与交互(UI/UX)
        l3_list: [UI视觉与布局, HUD抬头显示, 菜单导航与逻辑, 新手引导与教程]

  # ---------------- L1-03 视觉与艺术 ----------------
  - l1: 视觉与艺术
    l2_list:
      - l2: 画面品质与技术
        l3_list: [画面分辨率/清晰度, 3D模型精细度, 粒子与光影特效, 渲染品质]
      - l2: 美术与演出
        l3_list: [美术视觉风格, 场景构图与美感, 动画演出/CG过场]

  # ---------------- L1-04 叙事与世界观 ----------------
  - l1: 叙事与世界观
    l2_list:
      - l2: 剧情与叙事
        l3_list: [主线剧情, 支线任务故事, 多结局设计, 叙事节奏与结构, 文案品质]
      - l2: 角色与设定
        l3_list: [角色塑造与性格, 世界观/背景设定, 文化内涵与隐喻]
      - l2: 本地化
        l3_list: [文本翻译准确性, 字幕显示与同步]

  # ---------------- L1-05 声音与音频 ----------------
  - l1: 声音与音频
    l2_list:
      - l2: 音乐
        l3_list: [背景音乐(BGM), 原声带(OST)/配乐, 主题曲/片尾曲]
      - l2: 音效与配音
        l3_list: [战斗/环境音效, 中文配音表现, 外语配音表现]

  # ---------------- L1-06 技术与性能 ----------------
  - l1: 技术与性能
    l2_list:
      - l2: 帧数与流畅度
        l3_list: [帧率表现(FPS), 帧数波动/掉帧, 画面卡顿/顿挫, 场景加载速度]
      - l2: 稳定性与缺陷
        l3_list: [程序崩溃/报错, 程序闪退, 运行Bug/代码漏洞, 存档损坏/丢失]
      - l2: 硬件与优化
        l3_list: [硬件配置要求, 显卡适配与优化, DLSS/FSR超采样, 设备发热与功耗]

  # ---------------- L1-07 平台与安全 ----------------
  - l1: 平台与安全
    l2_list:
      - l2: 联机与网络
        l3_list: [服务器连通性/状态, 网络延迟(Ping), 丢包/网络掉线, 匹配机制(网络侧), 加速器依赖度]
      - l2: 平台生态功能
        l3_list: [Steam Deck/掌机适配, 云存档同步, 成就系统, 跨平台联机/进度]
      - l2: 环境与安全防护
        l3_list: [外挂与作弊现象, 反作弊系统(VAC/EAC), 第三方DRM(Denuvo), 第三方启动器(Launcher)绑定]

  # ---------------- L1-08 社区与社交 ----------------
  - l1: 社区与社交
    l2_list:
      - l2: 多人社交环境
        l3_list: [队友行为(坑/挂机/送头), 组队开黑/匹配社交, 语音沟通体验, 公会/战队系统, 社区风气与玩家素质]
      - l2: UGC与生态
        l3_list: [创意工坊, Mod模组生态, 社区活动与赛事]

  # ---------------- L1-09 商业与运营 ----------------
  - l1: 商业与运营
    l2_list:
      - l2: 定价与性价比
        l3_list: [售价策略, 折扣促销力度, 历史最低价, 性价比与心理预期]
      - l2: 商业模式与氪金
        l3_list: [DLC/付费扩展包, 抽卡/开箱概率, 战令/通行证设计, 微交易/内购点, 肝度与时间成本]
      - l2: 运营与版本更新
        l3_list: [内容更新频率, 抢先体验(EA)完成度, 暗改/机制变更, 停更与维护态度, 客服响应与退款服务]

  # ---------------- L1-10 综合与元表达 ----------------
  - l1: 综合与元表达
    l2_list:
      - l2: 整体印象
        l3_list: [综合推荐度, 总体体验评价]
      - l2: 社区梗与反讽
        l3_list: [评测区排版/字符画, 网络梗与段子, 反讽/阴阳怪气表达]

```

---

## 4. 标签体系二：PEDM 玩家体验诊断模型 (Player Experience Diagnosis Model)

> 负责心理因果链归因，基于 $E \text{(体验)} \times P \text{(缺陷模式)} \times \text{Entity (引用GDT)} \times I \text{(影响/意图)}$ 构建。

### 4.1 完全定义

#### 维度一：玩家体验地图 (Experience Map, 21 个核心节点 + 1 个元节点)

* **E1. 核心乐趣与心流 (Core Fun & Flow)**
* `E1_1_Combat_Thrill` 战斗爽感与动作反馈
* `E1_2_Exploration` 探索与惊喜感
* `E1_3_Tactical_Depth` 策略思考与博弈
* `E1_4_Mastery_Growth` 操控熟练与掌控感
* `E1_5_Creative_Freedom` 机制自由与创造力
* `E1_6_Collection_Drive` 收集与完成度满足


* **E2. 学习与掌握 (Learning & Onboarding)**
* `E2_1_Onboarding_Smoothness` 新手引导与破冰
* `E2_2_Rule_Clarity` 机制与规则认知
* `E2_3_Skill_Threshold` 操作门槛与适应期


* **E3. 投入与留存 (Progression & Retention)**
* `E3_1_Effort_Reward_Balance` 投入回报感 (心流/收益比)
* `E3_2_Pacing_Rhythm` 内容节奏与消耗速度
* `E3_3_Endgame_Sustainability` 终局/长期玩法支撑力
* `E3_4_Replayability` 重复游玩/多周目价值


* **E4. 情绪与沉浸 (Emotion & Immersion)**
* `E4_1_Atmospheric_Immersion` 视听与世界观沉浸
* `E4_2_Narrative_Resonance` 剧情与角色情感共鸣
* `E4_3_Frustration_Burnout` 负面情绪调控 (挫败感/审美疲劳)


* **E5. 信任与价值 (Value & Trust)**
* `E5_1_Price_To_Content` 物有所值感
* `E5_2_Monetization_Fairness` 商业化合理性
* `E5_3_Dev_Transparency` 研发承诺与信任感


* **E6. 社交与生态环境 (Social Environment)**
* `E6_1_Competitive_Fairness` 竞技公平性
* `E6_2_CoOp_Dynamics` 组队与协作体验
* `E6_3_Community_Vibe` 社区风气与玩家互动


* **E0. 元表达与无体验 (Meta & Non-Experience)**
* `E0_Meta_General` 纯梗/排版字符画/无特定体验



#### 维度二：归一化问题模式库 (Problem Pattern Schema)

* `P1_Imbalance` **投入产出严重失衡** (刷半天无提升、高肝低产)
* `P2_Friction` **心流阻断与高挫败** (判定苛刻、跑图繁琐、硬性阻隔)
* `P3_Void` **内容空倦与体验断档** (通关后无事可做、后期同质化)
* `P4_Expectation_Gap` **预期落差与信任破裂** (宣传欺诈、画质缩水、暗改)
* `P5_Degenerate_Meta` **策略死板与选择剥夺** (环境同质化、强迫特定打法)
* `P6_Technical_Blocker` **技术性体验阻断** (崩溃闪退、严重掉帧、高延迟)
* `P7_Aggressive_Monetization` **侵入式/逼迫式商业化** (逼氪、战令逼肝、恶意抽卡)
* `P0_None` **无设计缺陷** (正评/纯梗/排版字符画/无痛点)

#### 维度三：影响严重度与用户意图 (Impact & Intent)

* **Severity**: `S_Critical` (退款/卸载/刷差评) | `S_High` (严重劝退) | `S_Medium` (日常槽点) | `S_Low` (轻微调侃)
* **User Intent**: `I_Churn_Risk` (流失风险) | `I_Bug_Report` ( Bug反馈) | `I_Constructive_Advice` (建设性建议) | `I_Emotional_Vent` (纯吐槽)

---

## 5. Prompt 工程规范 (System Prompts for LLM Implementation)

Vibe coding 时，请直接复用以下为大模型编写的标准 Prompt 模板。

### 5.1 轨道 A 打标 Prompt (GDT Classifier)

```text
System Instruction:
你是一个精通游戏架构的 VOC 分类引擎。请读取输入评论，按照 GDT 标签体系进行打标。

[规则]
1. 提取评论中提及的所有游戏组件，打上 L1, L2, L3 以及情感倾向 (Positive / Neutral / Negative)。
2. L1, L2, L3 的命名必须严格匹配 GDT YAML 规范，严禁自创词汇。
3. 纯梗/字符画请归入：L1: 综合与元表达 -> L2: 社区梗与反讽 -> L3: 评测区排版/字符画 或 网络梗与段子。

输出 JSON 格式，遵照 GDTLabelResult 接口。

```

### 5.2 L3.5 动态微话题提取 Prompt (L3.5 Cluster Extractor)

```text
System Instruction:
你是一个游戏体验下钻分析师。请分析给定 L3 节点 ({{L3_ANCHOR}}) 下的文本集合，提取出 Top 5 集中出现的【微观现象/对象短语】。

[严格禁令]
1. 提取短语仅限“微观对象/现象微细分”（如："闪避无敌帧窗口期"、"锁定镜头卡墙角"）。
2. 绝对禁止在短语中包含情感词或心理推演（严禁出现 "体验差"、"引发挫败感"、"设计失误" 等词汇）。

输出 JSON 格式，遵照 L35TopicClusterResult 接口。

```

### 5.3 轨道 B 诊断 Prompt (PEDM Extractor)

```text
System Instruction:
你是一个资深游戏主策划与体验诊断专家。请对输入的玩家评论进行逻辑归因诊断。

[边界处理与垃圾防护 (Garbage-In Guard)]
1. 若评论为正评、纯梗、字符画或无设计缺陷的吐槽：
   - experience_dimension 设置为 "E0_Meta_General"
   - problem_pattern 设置为 "P0_None"
   - actionable_input_for_dev 必须强行设置为 null。
2. 若评论包含有效痛点：
   - 从 PEDM E1-E6 中选择最贴切的体验维度。
   - 从 PEDM P1-P7 中选择最贴切的设计缺陷模式。
   - target_entity_ref 必须从 GDT 的 L3 节点中选择标准路径（如 "机制与内容 -> 核心机制与循环 -> 战斗系统"）。
   - 生成 actionable_input_for_dev，格式为：【后端/策划/引擎分析输入】+ 具体可落地的优化建议。

输出 JSON 格式，遵照 PEDMDiagnosisResult 接口。

```

---

## 6. Vibe Coding 开发阶段任务清单 (Implementation Checklist)

* [ ] **Task 1: 基础 Schema 校验器**：基于 TypeScript Interfaces 编写 Pydantic / Zod Schema，保障 LLM Structured Output 格式校验。
* [ ] **Task 2: GDT 轻量打标服务**：实现轨道 A Pipeline，接入小模型/Fast Model，完成全量文本入库与 BI 大盘统计。
* [ ] **Task 3: L3.5 手动触发 API**：实现按需提取接口 `POST /api/v1/insights/l35-cluster`，根据指定的 `l3_node` 和 `time_range` 调取 LLM 生成微话题。
* [ ] **Task 4: PEDM 诊断服务**：实现轨道 B 穿透接口 `POST /api/v1/insights/diagnose`，结合 `P0_None` 容错逻辑，生成可派发的诊断 Task。
* [ ] **Task 5: 看板 UX 联动集成**：联调前端，实现点击“GDT Spike 节点 -> 展示 L3.5 气泡云 -> 点击穿透展开 PEDM 研发诊断报告”的完整交互链路。