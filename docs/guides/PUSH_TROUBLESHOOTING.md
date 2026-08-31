# 🛠️ Push 排查指南（每次 push 前必读）

> **目的**：避免重复踩坑。DSH 沙箱屏蔽 git 出站协议栈，但有 `scripts/ops/push_via_api.py`（GH REST API 兜底）；本指南列决策树 + 已知坑 + 验证清单。
>
> **触发时机**：任何时候要 `git push` 到 GitHub 前 5 分钟，先读这个。

---

## 1. 决策树：sandbox 推 vs 手动 git 推

按这个流程选：

```
需要 push 到 GitHub？
  │
  ├─ 在 sandbox（DSH/agent 进程）？
  │   │
  │   ├─ push main / 涉及 refs 更新
  │   │   │
  │   │   ├─ 之前 sandbox push 成功过？+ 没连续 5+ 次 refs POST/PATCH？
  │   │   │   → 用 scripts/ops/push_via_api.py 沙箱推（运行前读本文 §3 避坑）
  │   │   │
  │   │   └─ 连续 PATCH ref 403 / blob POST 仍可用 → ⛔ **手动 git push 更高效**
  │   │       （参考本文 §3 坑 4：refs 二级限流）
  │   │       提示用户：'sandbox refs 限流了，你本地 git push origin main --force 即可'
  │   │
  │   └─ push 到新分支（force 不需要）？
  │       → 沙箱推（POST ref 创建分支；成功率高）
  │
  └─ 在本机（用户终端）？
      → 直接 git push（最简单，不读这个文档也行）
```

**触发"手动 git 更高效"提醒的硬条件**（任一）：

- `PATCH /git/refs/heads/main` 连续 2 次 403，但 `POST /git/blobs` 仍 201
- sandbox 累计创建/删除/更新 5+ 个分支
- 用户在 chat 里说"再试一次"超过 2 次
- commit 含 100+ 文件的全量重建

→ **立即停止 sandbox 操作**，提示用户手动 `git push origin main:main --force`，不要继续盲试。

---

## 2. 沙箱内 push 通用路径（push_via_api.py 用法）

### 2.1 前置检查

```bash
# 1. PAT 权限
#   GitHub → Settings → Developer settings → Fine-grained tokens
#   → Contents: Read and write（**不是 Read-only**）
#   → Repositories: Only select misEricality/voc-platform

# 2. token 放在环境变量
$env:GITHUB_TOKEN = 'github_pat_...'
```

### 2.2 推荐执行方式

```powershell
# 一行：sync 检查 + 推送 + 验证
.\.venv-ml\Scripts\python.exe scripts\ops\push_via_api.py
```

脚本自动：
1. 抓远端 main ref（不硬编码，避免 parent 过期）
2. 递归扫描本地工作树，POST blob + 嵌套 POST tree
3. POST commit（parent = 远端 main，fast-forward）
4. PATCH ref（fast-forward，不传 `force: true`）
5. 验证 10 个关键文件 SHA

---

## 3. 已知坑清单（按时间倒序）

### 🆕 坑 5：root 目录排序 bug（2026-08-31）

**症状**：
- 脚本输出 `[/] OK xxx` 但 commit 的 tree 里子目录改动全部丢失
- 验证显示 root 下的目录（如 `.github/workflows/daily-collect.yml`）SHA 没变
- 子目录里明明改了文件但远端没生效

**根因**：
- `sorted_dirs` 用 `p.count('/')` 排序，`''`（root）和 `src` 都 = 0
- Python `set` 无序，root 可能**先于**子目录处理
- root 处理时查 `new_subdir_trees['src']` → 还没填 → 用**旧的 src SHA**
- commit tree 引用旧子目录 SHA → 子目录改动全部失效

**修复**：
```python
# 错误：sorted_dirs = sorted(all_dirs, key=lambda p: p.count('/') if p else 0, reverse=True)
# 正确：root 强制最后
sorted_dirs = sorted(all_dirs - {''}, key=lambda p: p.count('/'), reverse=True) + ['']
```

**另外**：需要把所有改动目录的**祖先目录**也加入重建集合，否则祖先目录不会重建：
```python
all_dirs = set()
for d in changes_by_dir.keys():
    parts = d.split('/') if d else []
    for i in range(len(parts) + 1):
        all_dirs.add('/'.join(parts[:i]))
all_dirs.add('')  # root
```

### 🆕 坑 6：basename key 冲突（2026-08-31）

**症状**：
- 仓库有同名目录：`src/` 和 `product/prototype/src/`
- src/ 的 Python 源码被前端 .js/.html 文件覆盖（`src/` 下出现 `app.js` `dashboard.css`）
- 验证发现 `src/` 目录树内容与 Python 源码完全不一致

**根因**：
- `new_subdir_trees` key 用了 basename（如 `'src'`）
- 后处理的 `product/prototype/src` 覆盖了先处理的 `src` 的 SHA
- root 处理时 `new_subdir_trees['src']` 拿到的是 `product/prototype/src` 的 tree SHA
- commit tree 把前端文件塞到 `src/` 下

**修复**：
- key 必须用**完整仓库路径**（`'src'` 不够，要 `'product/prototype/src'`）
- children entry 在 base tree 里的 `path` 字段是**完整路径**，用 `c['path']` 查 `new_subdir_trees`

```python
# 错误：new_subdir_trees[bn] = new_sha  # bn = basename
# 正确：key 用完整目录路径
new_subdir_trees[d] = new_sha  # d = 完整路径
```

### 🆕 坑 4：GitHub refs 二级限流（2026-08-31）

**症状**：
- `POST /git/blobs` 仍 201
- `POST /git/commits` 仍 201
- `PATCH /git/refs/heads/main` 持续 403
- `POST /git/refs`（创建分支）也从 201 变成 403
- 等待 60-180s 仍 403
- 错误信息：`{"message": "Resource not accessible by personal access token", "status": "403"}`
- 检查 PAT 权限 = `Contents: Read and write` 看起来正常

**根因**：
- **GitHub 对 ref 写操作的 secondary rate limit**
- 触发条件：短时间内大量 `POST/PATCH /git/refs/*`（创建/更新/删除分支）
- 限流时间窗：1 小时左右（不确定）
- 与权限无关，PAT 权限正确也会 403
- blob/tree/commit 的 rate limit 独立，不会被这个限流影响

**触发模式**（以下情况容易触发）：
- 短时间内反复 PATCH ref 试错（即使 403 也继续试）
- 频繁创建/删除测试分支（`test-push-tmp` `test-ref-perm` `fix/glm-push` 等）
- 全量重建 commit（tree 巨大，触发额外的写保护？）

**应对**：
1. **立即停止 sandbox 操作**（不要继续盲试，浪费限流时间窗）
2. **提醒用户手动 git push**：
   ```bash
   git push origin main:main --force
   ```
3. sandbox 内 `POST blobs/tree/commits` 仍可用来预生成完整 commit 对象，但**不要 PATCH ref**
4. 等 1 小时左右限流可能自然恢复（不保证）

**校验 PAT 权限 ≠ 检查限流**：
- PAT 权限问题：错误信息是 `403 Forbidden`（"Resource not accessible"）
- 限流问题：可能也是 `403`，但**仅** ref 操作 403，blob POST 仍可用
- 区分：先试一个 `POST /git/blobs` 确认 token 有写权限，再判断是不是限流

### 🆕 坑 1：REMOTE_HEAD 硬编码过期（2026-08-31）

**症状**：
- 推送后远端 commit 看起来正确，但 `git log` 显示历史被压扁
- 远端 squash commit `e1380fe` 消失，被压成普通 commit
- commit chain 少了一两个节点

**根因**：
- 脚本里 `REMOTE_HEAD = '98b237e...'` 硬编码
- 但实际上远端已经推进到 `e1380fe`（在硬编码值之后）
- POST commit 时 `parents=[98b237e]` → 远端 main = `98b237e` 不在新 commit 链上
- PATCH ref 用 `force: true` → 强行覆盖，远端历史被截断

**修复**：
- 不要硬编码 REMOTE_HEAD
- 每次推送前**实时 GET** 远端 ref：
  ```python
  remote_head = api('GET', f'{API}/git/refs/heads/main')['object']['sha']
  ```
- 避免使用 `force: true`（除非明确知道要丢弃历史）

### 坑 2：fine-grained PAT 涉及 .github/ workflow 必须加 `workflows:write` scope（2026-08-25）

**症状**：
- `POST /repos/.../contents/.github/workflows/daily-collect.yml` 403
- `POST /git/blobs` + 嵌套 tree 也 403
- 错误信息 `Resource not accessible by personal access token`

**根因**：
- fine-grained PAT 的 `Contents: write` 不覆盖 `.github/` 目录
- GitHub 把 workflow 文件视为受保护资源
- 必须额外加 `Workflows: write` 权限

**应对**：
- 沙箱推送 `.github/` 改动时，让用户先在 GitHub 网页手动 commit（最快）
- 或在 PAT 设置里加 `Workflows: write` 权限（注意 security impact）

### 坑 3：dotfile path 触发 404（2026-08-28）

**症状**：
- `POST /git/trees` with `base_tree` + 单个 dotfile path（`.github/workflows/*.yml`）→ **404 Not Found**
- 无 `base_tree` + 完整 tree POST 也 404（同样路径问题）

**根因**：
- GitHub API 处理 base_tree modify 时的已知 bug
- dotfile 路径在某些场景触发 base_tree lookup 失败

**修复**：
- **不用 base_tree**，传**完整 children** 列表
- 或**先 POST 子目录 tree**（nested），再在父 tree 里用 sub-tree SHA 引用

---

## 4. Push 后必做验证（5 步）

```bash
# 1. 远端 main = 本地 HEAD
.\.venv-ml\Scripts\python.exe -c "
import urllib.request, json, os
token = os.environ['GITHUB_TOKEN']
req = urllib.request.Request(
    'https://api.github.com/repos/misEricality/voc-platform/git/refs/heads/main',
    headers={'Authorization': f'token {token}', 'Accept': 'application/vnd.github+json'}
)
print('remote main =', json.loads(urllib.request.urlopen(req).read())['object']['sha'])
"
# 期望 = git rev-parse HEAD

# 2. 文件数 = 158（本地 ls-files）
# 3. 关键 10 文件 SHA 全匹配（用 git ls-tree -r HEAD 与远端 tree 对比）

# 4. src/ 不被污染（应为 Python 源码，无 .js/.html）
# 5. .github/workflows/ 含新 workflow 文件
```

完整验证脚本：临时用 `scripts/ops/_verify_proper.py`（提交时删除），或抄 §4 进 push_via_api.py 的 step 7。

---

## 5. 回滚路径

### 5.1 Sandbox 内回滚（限流时不可用）

```python
# PATCH ref 回上一个 commit（fast-forward）
api('PATCH', f'{API}/git/refs/heads/main',
    {'sha': previous_commit_sha, 'force': True})
```

- **仅在 sandbox 限流恢复后**才能用
- 不要硬编码 previous_commit_sha，从 `git log --oneline -5` 拿

### 5.2 本机手动回滚（限流时推荐）

```bash
# 强制覆盖远端 main 到指定 commit
git push origin <commit_sha>:main --force
```

- `--force` 会丢弃中间历史
- 内容零丢失的前提：`git diff <commit_sha>..HEAD` 已确认无重要改动

---

## 6. 排障检查清单

按顺序勾选：

- [ ] PAT 权限：`Contents: Read and write` + `Workflows: write`（如涉及 .github/）
- [ ] `GITHUB_TOKEN` 环境变量已设
- [ ] 本地 HEAD 与远端 main 没有分叉（`git fetch` 看）
- [ ] 改的文件**在** `git ls-files` 中（不被 .gitignore 排除）
- [ ] `push_via_api.py` 用实时 REMOTE_HEAD，不用硬编码
- [ ] 跑完看脚本输出 4 个关键 info：`base tree`、`changes =`、`creating commit`、`PATCH main`
- [ ] PATCH 后 GET main ref 确认 SHA 匹配
- [ ] 5 个关键文件 SHA 对比（见 §4）

---

## 7. 相关文档

- `MEMORY.md` §59-68：沙箱内 push 通用路径 + PAT 安全规则
- `.workbuddy/memory/2026-08-28.md`：沙箱 push 路径的完整步骤（首次实现）
- `.workbuddy/memory/2026-08-31.md`：本次踩坑详情（root 排序、basename 冲突、refs 限流）
- `scripts/ops/push_via_api.py`：当前的 push 工具（v3 本地全量重建）
- `scripts/README.md`：所有 ops 脚本索引
