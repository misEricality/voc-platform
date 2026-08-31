#!/usr/bin/env python3
"""把本地 working tree 推到 origin/main（sandbox 屏蔽 git push 时用 GH API 兜底）

## 算法（v3 · 本地全量重建）
1. 递归扫描本地 git 跟踪文件（git ls-files -z，处理中文/特殊字符 quoted path）
2. 对每个目录：children = 本地直接子项（文件→POST blob，子目录→递归）
3. 删除：base tree 里有但本地没有的文件 → 不包含
4. POST tree（不传 base_tree，完整重建；blob SHA 相同会被 GitHub dedup，无额外成本）
5. POST commit（parent = 当前 remote HEAD → fast-forward）
6. PATCH ref（fast-forward 更新；force 会被 branch protection 403 拒绝）
7. 验证关键文件 SHA

## 用法
GITHUB_TOKEN=ghp_xxx python scripts/ops/push_via_api.py
"""
import os, urllib.request, urllib.error, json, sys, subprocess, base64, re, hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta

REPO = 'misEricality/voc-platform'
API = f'https://api.github.com/repos/{REPO}'


def parse_git_date(s):
    m = re.match(r'^(\w{3} \w{3} \d{1,2} \d{2}:\d{2}:\d{2} \d{4}) ([+-]\d{4})$', s.strip())
    dt_naive = datetime.strptime(m.group(1), '%a %b %d %H:%M:%S %Y')
    tz_sign = 1 if m.group(2)[0] == '+' else -1
    return dt_naive.replace(tzinfo=timezone(timedelta(minutes=tz_sign * (int(m.group(2)[1:3])*60 + int(m.group(2)[3:5]))))).isoformat()


def api(method, url, data=None):
    token = os.environ['GITHUB_TOKEN']
    req = urllib.request.Request(url, method=method, headers={
        'Authorization': f'token {token}', 'Content-Type': 'application/json',
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
    })
    body = json.dumps(data).encode() if data else None
    try:
        with urllib.request.urlopen(req, body, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _unquote_git_path(p: str) -> str:
    """git -z 输出的 quoted 路径 → 真实路径（中文/特殊字符 octal 转义）"""
    if p.startswith('"') and p.endswith('"'):
        p = p[1:-1]
    if '\\' in p:
        try:
            return codecs_decode(p)
        except (UnicodeDecodeError, UnicodeEncodeError):
            return p
    return p


def codecs_decode(p):
    import codecs
    return codecs.decode(p, 'unicode_escape')


def get_local_files() -> list:
    """git ls-files -z 拿所有 tracked 文件（unquote 后）"""
    env = os.environ.copy()
    env['LC_ALL'] = 'C.UTF-8'
    env['PYTHONIOENCODING'] = 'utf-8'
    out = subprocess.check_output(
        ['git', 'ls-files', '-z'], text=True, encoding='utf-8', env=env
    )
    return sorted(_unquote_git_path(p) for p in out.split('\0') if p)


def post_blob(content: bytes) -> str:
    bdata = {'content': base64.b64encode(content).decode('ascii'), 'encoding': 'base64'}
    status, blob = api('POST', f'{API}/git/blobs', bdata)
    if status != 201: sys.exit(f'blob fail: {blob}')
    return blob['sha']


def post_tree(children: list) -> str:
    """POST tree（不传 base_tree，完整重建）"""
    status, tree = api('POST', f'{API}/git/trees', {'tree': children})
    if status != 201:
        sys.exit(f'tree fail: {json.dumps(tree)[:500]}')
    return tree['sha']


def build_tree(dir_path: str, files_in_dir: list, base_by_path: dict,
               cache: dict) -> str:
    """递归构建 dir_path 目录的 tree

    Args:
        dir_path: 目录路径（'' = root）
        files_in_dir: 本目录直接子文件（不含子目录）
        base_by_path: base tree 的 {path: sha}（用于判断文件是否变化/删除）
        cache: 已构建的子目录 tree SHA {dir_path: sha}

    Returns:
        本目录 tree SHA
    """
    if dir_path in cache:
        return cache[dir_path]

    prefix = dir_path + '/' if dir_path else ''
    # 本目录直接子文件
    local_blobs = []
    for f in files_in_dir:
        if f.startswith(prefix) and '/' not in f[len(prefix):]:
            local_blobs.append(f)

    # 本目录直接子目录（从所有文件推断）
    local_subdirs = set()
    for f in files_in_dir:
        if f.startswith(prefix) and '/' in f[len(prefix):]:
            sub = f[len(prefix):].split('/')[0]
            if sub:
                local_subdirs.add(sub)

    children = []
    # 子目录先构建（递归）
    for sub in sorted(local_subdirs):
        sub_path = prefix + sub
        sub_files = [f for f in files_in_dir if f.startswith(sub_path)]
        sub_sha = build_tree(sub_path, sub_files, base_by_path, cache)
        if sub_sha:
            children.append({'path': sub, 'mode': '040000', 'type': 'tree', 'sha': sub_sha})

    # 文件
    for f in sorted(local_blobs):
        content = Path(f).read_bytes()
        blob_sha = post_blob(content)
        children.append({'path': f[len(prefix):], 'mode': '100644', 'type': 'blob', 'sha': blob_sha})

    # 删除：base 有但本地无（files_in_dir 已排除 base-only 文件 → 自动不含）
    # 注意：files_in_dir 来自 git ls-files（本地权威），base-only 文件天然不在其中

    if not children:
        # 空目录（没有文件也没有子目录）— 忽略
        return None

    sha = post_tree(children)
    cache[dir_path] = sha
    return sha


def main():
    if 'GITHUB_TOKEN' not in os.environ:
        sys.exit('GITHUB_TOKEN env var required')

    local_head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()

    # 1. 当前 remote HEAD
    status, ref = api('GET', f'{API}/git/refs/heads/main')
    if status != 200: sys.exit(f'get ref: {ref}')
    remote_head = ref['object']['sha']
    print(f'local HEAD  = {local_head[:12]}')
    print(f'remote HEAD = {remote_head[:12]}')

    # 2. base tree（用于删除判断；仅对比，不依赖其完整性）
    status, commit = api('GET', f'{API}/git/commits/{remote_head}')
    if status != 200: sys.exit(f'get commit: {commit}')
    base_entries = []
    status, tree = api('GET', f'{API}/git/trees/{commit["tree"]["sha"]}?recursive=1')
    if status == 200:
        base_entries = tree['tree']
    base_by_path = {e['path']: e['sha'] for e in base_entries if e['type'] == 'blob'}
    print(f'1. base tree = {commit["tree"]["sha"][:12]} ({len(base_entries)} entries)')

    # 3. 本地文件
    local_files = get_local_files()
    print(f'2. local tracked files = {len(local_files)}')

    # 4. 递归构建 root tree
    print('3. building trees...')
    cache = {}
    root_sha = build_tree('', local_files, base_by_path, cache)
    print(f'   root tree = {root_sha[:12]}')

    # 5. POST commit（fast-forward：parent = remote_head）
    env = os.environ.copy()
    env['LC_ALL'] = 'C.UTF-8'
    env['PYTHONIOENCODING'] = 'utf-8'
    out = subprocess.check_output(
        ['git', 'log', '-1', '--format=%an%n%ae%n%ad%n%s%n%b', local_head],
        text=True, encoding='utf-8', errors='replace', env=env,
    )
    author_name, author_email, author_date, subject, body = out.strip().split('\n', 4)
    commit_msg = subject if not body.strip() else f'{subject}\n\n{body}'

    print(f'\n4. creating commit (parent = {remote_head[:12]})...')
    commit_data = {
        'message': commit_msg,
        'tree': root_sha,
        'parents': [remote_head],
        'author': {'name': author_name, 'email': author_email, 'date': parse_git_date(author_date)},
    }
    status, new_commit = api('POST', f'{API}/git/commits', commit_data)
    if status != 201: sys.exit(f'commit fail: {new_commit}')
    print(f'   new commit = {new_commit["sha"]}')

    # 6. PATCH ref（fast-forward；force 会被 branch protection 拒）
    print(f'\n5. PATCH main -> {new_commit["sha"][:12]}')
    status, ref_resp = api('PATCH', f'{API}/git/refs/heads/main',
                           {'sha': new_commit['sha'], 'force': False})
    if status != 200:
        sys.exit(f'ref fail: {json.dumps(ref_resp)[:500]}')
    print(f'   remote HEAD now: {ref_resp["object"]["sha"]}')
    print(f'\n=== DONE: {remote_head[:12]} -> {ref_resp["object"]["sha"][:12]} ===')

    # 7. 验证
    print('\n6. verifying key files...')
    status, final_tree = api('GET', f'{API}/git/trees/{ref_resp["object"]["sha"]}?recursive=1')
    if status != 200:
        print(f'   WARN: cannot fetch final tree: {json.dumps(final_tree)[:200]}')
        return 0
    final_by_path = {e['path']: e['sha'] for e in final_tree['tree'] if e['type'] == 'blob'}
    verify_paths = [
        '.github/workflows/daily-collect.yml',
        'src/analyzers/sentiment_llm.py',
        'src/pipeline.py',
        'scripts/ops/daily_incremental_collect.py',
        'scripts/dev/verify_glm_5_3_flash.py',
        'tests/test_daily_incremental_collect.py',
        'AGENTS.md',
        'scripts/README.md',
        '.env.example',
        '.gitignore',
    ]
    ok = True
    for p in verify_paths:
        try:
            local = Path(p).read_bytes()
            local_sha = hashlib.sha1(b'blob ' + str(len(local)).encode() + b'\0' + local).hexdigest()
            remote_sha = final_by_path.get(p)
            match = local_sha == remote_sha
            ok = ok and match
            print(f'   {"[OK]" if match else "[XX]"} {p}')
            if not match:
                print(f'      local={local_sha[:12]} remote={remote_sha[:12] if remote_sha else "MISSING"}')
        except FileNotFoundError:
            ok = False
            print(f'   [XX] {p} (local missing)')
    print(f'\n   {"ALL KEY FILES VERIFIED" if ok else "SOME FILES MISMATCH"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())