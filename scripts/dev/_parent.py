"""Get parent commit ab99a59 details."""
import base64
import json
import os
import urllib.request

TOKEN = os.environ["GH_TOKEN"]
HDR = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}

# Get remote main commit
req = urllib.request.Request(
    "https://api.github.com/repos/misEricality/voc-platform/git/ref/heads/main",
    headers=HDR,
)
remote = json.loads(urllib.request.urlopen(req).read())["object"]
main_sha = remote["sha"]
print(f"main sha: {main_sha}")

# Get commit details
req = urllib.request.Request(
    f"https://api.github.com/repos/misEricality/voc-platform/git/commits/{main_sha}",
    headers=HDR,
)
commit = json.loads(urllib.request.urlopen(req).read())
print(f"commit message:\n{commit['message']}")
print(f"parent: {commit['parents'][0]['sha']}")
print(f"tree: {commit['tree']['sha']}")