import json
import argparse
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Register a new asset into config/market_config.json")
    parser.add_argument("--symbol", required=True, help="Canonical symbol, e.g., XPEV, BILI")
    parser.add_argument("--name", required=True, help="Display name, e.g., 小鹏汽车, 哔哩哔哩")
    parser.add_argument("--market", required=True, choices=["US", "CN", "CRYPTO", "COMMODITY"], help="Market type")
    parser.add_argument("--data_symbol", help="Data provider symbol if different from symbol")
    parser.add_argument("--research_keyword", help="Keyword for research reports, defaults to name")
    parser.add_argument("--tags", help="Comma separated tags, e.g., 'EV,自动驾驶'")
    parser.add_argument("--is_default", action="store_true", help="Add to default_symbols")

    args = parser.parse_args()

    config_path = Path("config/market_config.json")
    if not config_path.exists():
        print(f"Error: {config_path} not found.")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Check if already exists
    if any(a.get("symbol") == args.symbol for a in config["assets"]):
        print(f"Error: Asset with symbol {args.symbol} already exists.")
        sys.exit(1)

    new_asset = {
        "symbol": args.symbol,
        "name": args.name,
        "market": args.market,
        "data_symbol": args.data_symbol or args.symbol,
        "research_keyword": args.research_keyword or args.name,
        "tags": [t.strip() for t in args.tags.split(",")] if args.tags else []
    }

    config["assets"].append(new_asset)

    if args.is_default and args.symbol not in config.get("default_symbols", []):
        config.setdefault("default_symbols", []).append(args.symbol)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"Successfully registered new asset: {args.symbol} ({args.name})")

if __name__ == "__main__":
    main()
