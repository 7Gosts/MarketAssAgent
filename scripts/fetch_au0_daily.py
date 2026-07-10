"""使用 AKShare 拉取沪金期货连续 (AU0) 日线数据

用法示例：
    python scripts/fetch_au0_daily.py          # 默认最近 20 根
    python scripts/fetch_au0_daily.py --count 50
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import akshare as ak
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "runtime", ROOT / "src", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from utils.runtime_paths import get_output_dir


def fetch_au0_daily(count: int = 20) -> pd.DataFrame:
    """拉取 AU0 日线，返回最近 count 根（按日期倒序）"""
    print("[AKShare] 正在拉取 AU0 日线 (symbol='AU0') ...")
    df = ak.futures_zh_daily_sina(symbol="AU0")
    if df is None or df.empty:
        raise RuntimeError("AKShare 返回空数据")

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date", ascending=False)
    else:
        for col in ["trade_date", "datetime", "time"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
                df = df.sort_values(col, ascending=False)
                break

    recent = df.head(count).copy()
    print(
        f"[AKShare] 成功获取 {len(recent)} 根日线，"
        f"范围: {recent['date'].min().date()} ~ {recent['date'].max().date()}"
    )
    return recent


def main():
    parser = argparse.ArgumentParser(description="拉取 AU0 日线（最近 N 根）")
    parser.add_argument("--count", type=int, default=20, help="最近多少根（默认 20）")
    args = parser.parse_args()

    df = fetch_au0_daily(count=args.count)

    cols = [c for c in ["date", "open", "high", "low", "close", "volume", "hold"] if c in df.columns]
    print("\n前 5 行预览：")
    print(df[cols].head().to_string(index=False))

    out_dir = get_output_dir(repo_root=ROOT)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"au0_daily_last{args.count}.csv"
    df.to_csv(out_file, index=False, encoding="utf-8-sig")
    print(f"\n已保存到: {out_file.resolve()}")


if __name__ == "__main__":
    main()
