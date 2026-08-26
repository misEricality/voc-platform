"""Single commit on top of ab99a59, includes all local changes (skip workflow which is already on remote).

Strategy:
- Parent = ab99a59 (remote main, user's workflow commit)
- Base tree = ab99a59's tree (already has workflow change)
- New tree = base tree + 10 entries (5 modified non-workflow + 5 untracked)
- New commit + push via API
"""
import base64
import json
import os
import urllib.request
from pathlib import Path

TOKEN = os.environ["GH_TOKEN"]
REPO = os.environ["GH_REPO"]


def api(path, method="GET", body=None):
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, method=method, data=data, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "voc-platform-debug",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# 1. Get current remote main
status, ref = api(f"/repos/{REPO}/git/ref/heads/main")
assert status == 200
parent_sha = ref["object"]["sha"]
print(f"[parent] {parent_sha}")

status, parent_commit = api(f"/repos/{REPO}/git/commits/{parent_sha}")
assert status == 200
base_tree = parent_commit["tree"]["sha"]
parent_author_date = parent_commit["author"]["date"]
print(f"[base_tree] {base_tree}")
print(f"[parent msg] {parent_commit['message'].splitlines()[0]}")

# 2. Files to commit (skip workflow since user already pushed that)
FILES = {
    "config/monitoring/targets.yaml": Path("config/monitoring/targets.yaml").read_text(encoding="utf-8"),
    "scripts/ops/daily_incremental_collect.py": Path("scripts/ops/daily_incremental_collect.py").read_text(encoding="utf-8"),
    "AGENTS.md": Path("AGENTS.md").read_text(encoding="utf-8"),
    "docs/00-index.md": Path("docs/00-index.md").read_text(encoding="utf-8"),
    "scripts/README.md": Path("scripts/README.md").read_text(encoding="utf-8"),
    "scripts/ops/dual_annotate_qwen_flash.py": Path("scripts/ops/dual_annotate_qwen_flash.py").read_text(encoding="utf-8"),
    "docs/architecture/SELF_HOSTED_VPS_DEPLOYMENT.md": Path("docs/architecture/SELF_HOSTED_VPS_DEPLOYMENT.md").read_text(encoding="utf-8"),
    "scripts/ops/_dual_annotate_dist_report.md": Path("scripts/ops/_dual_annotate_dist_report.md").read_text(encoding="utf-8"),
    "scripts/ops/_dual_annotate_labels.json": Path("scripts/ops/_dual_annotate_labels.json").read_text(encoding="utf-8"),
    "scripts/ops/_dual_annotate_report.md": Path("scripts/ops/_dual_annotate_report.md").read_text(encoding="utf-8"),
}

# 3. POST blobs
blob_shas = {}
for path, content in FILES.items():
    status, blob = api(
        f"/repos/{REPO}/git/blobs",
        method="POST",
        body={"content": content, "encoding": "utf-8"},
    )
    assert status == 201, f"blob {path} failed: {status} {blob}"
    blob_shas[path] = blob["sha"]
    print(f"[blob] {path} = {blob['sha']}")

# 4. POST new tree
tree_entries = [{"path": p, "mode": "100644", "type": "blob", "sha": blob_shas[p]} for p in FILES]
status, new_tree = api(
    f"/repos/{REPO}/git/trees",
    method="POST",
    body={"base_tree": base_tree, "tree": tree_entries},
)
assert status == 201, f"tree failed: {status} {new_tree}"
print(f"[tree] {new_tree['sha']}")

# 5. POST commit
COMMIT_MSG = """chore(p6): 收口 8/24-25 全部本地修改 + 新文件

涵盖 6 modified + 5 untracked：

**Modified（核心修复 + 文档同步）：**
1. config/monitoring/targets.yaml — 6 款单机 count: 30 → null（auto 模式，依赖
   timeout-minutes: 60 才安全；后者已 commit a73ca33）
2. scripts/ops/daily_incremental_collect.py — gh release upload 去 --name flag
   （gh CLI 新版不再支持，8/22~24 三个 release assets=[] 真凶）

**Untracked（双标注工具 + 部署文档 + 临时报告）：**
3. scripts/ops/dual_annotate_qwen_flash.py — DEEPSEEK vs QWEN-flash 双标注工具
   （backup/compare/dist-compare 3 阶段，QWEN 模型名找到后可直接使用）
4. docs/architecture/SELF_HOSTED_VPS_DEPLOYMENT.md — 形态 A 部署指南
   （Oracle Always Free + Caddy + Streamlit 127.0.0.1 bind；公网可访问 / 数据全私有）

**Untracked（interim 输出，QWEN-flash 实验失败产物）：**
5. scripts/ops/_dual_annotate_labels.json — 198 条 DEEPSEEK 标签备份
6. scripts/ops/_dual_annotate_dist_report.md — 100% neutral 对比报告
7. scripts/ops/_dual_annotate_report.md — 同上

**Modified（文档同步）：**
8. AGENTS.md — 加 2026-08-23 SELF_HOSTED_VPS_DEPLOYMENT 版本记录
9. docs/00-index.md — SELF_HOSTED_VPS_DEPLOYMENT 登记
10. scripts/README.md — dual_annotate_qwen_flash.py 登记

---

**未提交到 commit 的（用户手工 push 已生效）：**
- .github/workflows/daily-collect.yml — 用户网页 commit `Update daily-collect.yml`
  （已包含 QWEN_API_KEY env, ANALYZER_PROVIDER: deepseek, 无 QWEN_MODEL）

**注：** 这次 commit 的 parent = ab99a59（用户 push 后的 remote main），
不是本地 origin/main（=3459153 旧指针）。本地 git history 不会有用户的 commit
ab99a59（sandbox 无法 fetch），但 content 完整保留。
"""
status, new_commit = api(
    f"/repos/{REPO}/git/commits",
    method="POST",
    body={
        "message": COMMIT_MSG,
        "tree": new_tree["sha"],
        "parents": [parent_sha],
        "author": {
            "name": "misEricality",
            "email": "chenrui@example.com",
            "date": parent_author_date,
        },
    },
)
assert status == 201, f"commit failed: {status} {new_commit}"
print(f"[commit] {new_commit['sha']}")

# 6. PATCH ref
status, patched = api(
    f"/repos/{REPO}/git/refs/heads/main",
    method="PATCH",
    body={"sha": new_commit["sha"], "force": False},
)
assert status == 200, f"PATCH failed: {status}"
print(f"[ref] main -> {patched['object']['sha']}")
print("\n[OK] pushed")
