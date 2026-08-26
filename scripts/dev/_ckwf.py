"""Check remote workflow."""
import base64
import json
import os
import urllib.request

TOKEN = os.environ["GH_TOKEN"]
req = urllib.request.Request(
    "https://api.github.com/repos/misEricality/voc-platform/contents/.github/workflows/daily-collect.yml",
    headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"},
)
data = json.loads(urllib.request.urlopen(req).read())
content = base64.b64decode(data["content"]).decode("utf-8")
print("=== remote workflow env block ===")
for line in content.splitlines():
    if "env:" in line or "API_KEY" in line or "MODEL" in line or "ANALYZER_PROVIDER" in line or "VOC_SKIP" in line:
        print(line)
print(f"\nsha: {data['sha'][:12]}")