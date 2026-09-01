"""从 输出打标结果.xlsx 提取 500 条抽样验证样本（供黄金集重建）。

运行环境：使用 workspace 自带 Python（含 openpyxl）。
"""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl

XLSX = Path("data/exports/输出打标结果.xlsx")
OUT = Path("data/validation/_sample_500_from_xlsx.json")


def main() -> None:
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb["opinions"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))

    sampled = []
    for r in rows:
        if r[14] is None:
            continue
        sampled.append(
            {
                "opinion_id": r[0],
                "comment_id": r[1],
                "target_name": r[4],
                "phrase": r[9],
                "old_full_path": r[8],
                "sentiment": r[6],
                "sentiment_validation": r[13],
                "label_validation": r[14],
                "content": r[12],
            }
        )

    wb.close()
    OUT.write_text(
        json.dumps(sampled, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"wrote {OUT}: {len(sampled)} rows")


if __name__ == "__main__":
    main()
