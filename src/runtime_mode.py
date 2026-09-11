"""运行形态开关（2026-09-11）

**为什么需要它**：本站是「本地采集 + 标注 → VPS 只读展示」的双机形态（变体 ①b，见
`docs/architecture/SELF_HOSTED_VPS_DEPLOYMENT.md` §0.5）。但线上 admin 页的
「立即采集 / backfill」按钮会**真的在 VPS 上跑 `pipeline.run_pipeline()`**（含 LLM 标注），
而那样做的实际后果是：

- 结果只落 VPS 库，**次日 02:00 被整库推送覆盖掉**（白干）；
- VPS 上没有 `BILIBILI_SESSDATA` / 本机代理 → B 站必然 412 或 0 条；
- VPS 没装 ML 依赖、也**没有生产标注器（GLM）的 Key** → `get_analyzer()` 回落到默认
  `deepseek`，写进库的 `analyzer_version` 与本地口径不一致，**污染溯源**；
- 每跑一次都在花 token。

即「**看着能采，其实白采还有害**」。所以把形态做成**代码级收口**，而不是靠人记着别点：
`display_only()` 为真时 ①admin 写操作一律 403（GET 保留，线上看板照常"看"）；
②`pipeline.run_pipeline()` 直接拒绝执行（**采集与标注一起挡**）。

**默认值刻意跟 `PUBLIC_MODE` 走**：公网形态本身就是展示端，这样不需要在 `.env` 里
额外记一个开关（少一个能忘的地方）；本地开发（`PUBLIC_MODE=0`）完全不受影响。
只有确需一台**可写的**公网实例（例如将来改成 VPS 自己采集）时才显式设 `DISPLAY_ONLY=0`。
"""
from __future__ import annotations

import os


def display_only() -> bool:
    """本实例是否为「只读展示端」（不采集、不标注、不改采集任务）。

    每次调用都读 env —— 支持测试 `monkeypatch` 与运行期调整，与 `src/api/auth.py`
    里限流器「每请求读 env」的既有风格保持一致。
    """
    return os.getenv("DISPLAY_ONLY", os.getenv("PUBLIC_MODE", "0")) == "1"
