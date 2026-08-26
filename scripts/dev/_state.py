"""Pre-push state check."""
import base64
import json
import os
import subprocess
import urllib.request

# 1. local HEAD
local_head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=".").stdout.strip()
print(f"local HEAD: {local_head[:12]}")

# 2. remote main
TOKEN = os.environ["GH_TOKEN"]
req = urllib.request.Request(
    "https://api.github.com/repos/misEricality/voc-platform/git/ref/heads/main",
    headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"},
)
remote = json.loads(urllib.request.urlopen(req).read())["object"]["sha"]
print(f"remote main: {remote[:12]}")

# 3. is local ahead?
local_origin_main = subprocess.run(["git", "rev-parse", "origin/main"], capture_output=True, text=True, cwd=".").stdout.strip()
print(f"local origin/main: {local_origin_main[:12]}")

# 4. workflow file content parity
req = urllib.request.Request(
    "https://api.github.com/repos/misEricality/voc-platform/contents/.github/workflows/daily-collect.yml",
    headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"},
)
data = json.loads(urllib.request.urlopen(req).read())
remote_wf_sha = data["sha"]
print(f"remote workflow sha: {remote_wf_sha[:12]}")

# 5. local workflow sha
local_wf_sha = subprocess.run(["git", "hash-object", ".github/workflows/daily-collect.yml"], capture_output=True, text=True, cwd=".").stdout.strip()
print(f"local workflow sha:  {local_wf_sha[:12]}")