"""原声分析 Agent（2026-09-09 · 见 docs/architecture/ORIGINAL_VOICE_ANALYSIS_AGENT.md）

模块职责：
- chat.py：DeepSeek-V4-Flash 流式 SSE 调用 + 工具编排
- tools.py：4 个 tool（query_overview / query_topics / query_comments / search_docs）的 Python 实现
- skills.py：YAML skill 模板匹配（轻量版，不引入 DSH skill 协议）
- sessions.py：会话/消息 CRUD + Markdown 导出

所有端点位于 `/api/agent/*`，与现有 `/api/*` 公开只读端点同等鉴权（IP 速率限制）。
"""
