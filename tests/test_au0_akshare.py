"""使用 AKShare 拉取沪金期货连续 (AU0) 日线数据

用法示例：
    python tests/test_au0_akshare.py          # 默认最近 20 根
    python tests/test_au0_akshare.py --count 50
"""

import argparse
from pathlib import Path

import akshare as ak
import pandas as pd


def fetch_au0_daily(count: int = 20) -> pd.DataFrame:
    """拉取 AU0 日线，返回最近 count 根（按日期倒序）"""
    print(f"[AKShare] 正在拉取 AU0 日线 (symbol='AU0') ...")
    df = ak.futures_zh_daily_sina(symbol="AU0")
    if df is None or df.empty:
        raise RuntimeError("AKShare 返回空数据")

    # 确保日期列存在并排序
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date", ascending=False)
    else:
        # 某些版本可能用 trade_date
        for col in ["trade_date", "datetime", "time"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
                df = df.sort_values(col, ascending=False)
                break

    # 取最近 N 根
    recent = df.head(count).copy()
    print(f"[AKShare] 成功获取 {len(recent)} 根日线，范围: {recent['date'].min().date()} ~ {recent['date'].max().date()}")
    return recent


def main():
    parser = argparse.ArgumentParser(description="拉取 AU0 日线（最近 N 根）")
    parser.add_argument("--count", type=int, default=20, help="最近多少根（默认 20）")
    args = parser.parse_args()

    df = fetch_au0_daily(count=args.count)

    # 打印前 5 行关键列
    cols = [c for c in ["date", "open", "high", "low", "close", "volume", "hold"] if c in df.columns]
    print("\n前 5 行预览：")
    print(df[cols].head().to_string(index=False))

    # 保存 CSV
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"au0_daily_last{args.count}.csv"
    df.to_csv(out_file, index=False, encoding="utf-8-sig")
    print(f"\n已保存到: {out_file.resolve()}")


if __name__ == "__main__":
    main()