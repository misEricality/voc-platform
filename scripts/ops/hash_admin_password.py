"""生成管理员密码哈希（Web 看板 ADMIN_PASSWORD_HASH）

用法：
    python scripts/ops/hash_admin_password.py <明文密码>
    # 输出 pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>，粘贴到 .env 的
    # ADMIN_PASSWORD_HASH= 行（.env 已 gitignore）

最后更新：2026-09-01
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from src.api.auth import hash_password  # noqa: E402


def main() -> int:
    if len(sys.argv) > 1:
        password = sys.argv[1]
    else:
        password = getpass.getpass("管理员密码：")
        confirm = getpass.getpass("确认密码：")
        if password != confirm:
            print("ERROR: 两次输入不一致")
            return 2
    if not password:
        print("ERROR: 密码不能为空")
        return 2
    print()
    print("已生成 ADMIN_PASSWORD_HASH（粘贴到 .env）：")
    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
