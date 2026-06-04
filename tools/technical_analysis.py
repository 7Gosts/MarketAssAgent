from langchain_core.tools import tool
from typing import Dict, Any, Optional
from datetime import datetime
from memory.snapshot import snapshot_manager


@tool
def analyze_market(symbol: str, interval: str = "1d", force_refresh: bool = False) -> Dict[str, Any]:
    """【核心工具】全面技术分析 - 支持股票、加密货币、黄金
    
    Args:
        symbol: 标的代码 (e.g. BTCUSDT, NVDA, XAUUSD, 600519)
        interval: 时间周期 (1m, 5m, 15m, 1h, 4h, 1d, 1w)
        force_refresh: 是否强制刷新数据
    
    Returns:
        详细分析结果 + AnalysisSnapshot
    """
    print(f"🔍 开始分析 {symbol} {interval} 周期...")

    # 这里是模拟分析结果（后续替换为真实数据源 + 技术指标计算）
    analysis_result = {
        "symbol": symbol,
        "interval": interval,
        "timestamp": datetime.now().isoformat(),
        "trend": "偏多" if "BTC" in symbol or "NVDA" in symbol else "震荡",
        "key_levels": {
            "support": [62000, 60500, 59000],
            "resistance": [65000, 66500, 68000]
        },
        "structure": "123法则向上突破，均线多头排列，量价配合良好",
        "indicators": {
            "ma_trend": "MA5 > MA10 > MA20 > MA60",
            "volume": "放量上涨",
            "fib_levels": "0.618黄金分割位附近获得支撑"
        },
        "confidence": 78,
        "raw_insights": f"{symbol} 在 {interval} 周期呈现多头结构，关键支撑位稳固。"
    }

    # 生成并保存 Snapshot（解决追问上下文丢失的核心机制）
    snapshot = snapshot_manager.save_snapshot(
        session_id="default",  # 后续可改为真实 session_id
        snapshot_data=analysis_result
    )

    return {
        "status": "success",
        "symbol": symbol,
        "analysis": analysis_result,
        "snapshot": snapshot,
        "message": f"{symbol} {interval} 技术分析完成"
    }


@tool
def get_key_levels(symbol: str, interval: str = "1d") -> Dict[str, Any]:
    """获取关键支撑/阻力位"""
    return {
        "symbol": symbol,
        "support_levels": [60500, 59000, 57500],
        "resistance_levels": [65000, 66500, 68200],
        "message": "当前价格处于支撑位上方，短期关注阻力突破情况"
    }


@tool
def evaluate_structure(symbol: str, snapshot: Optional[Dict] = None) -> Dict[str, Any]:
    """评估当前市场结构（123法则、均线、量价等）"""
    if snapshot is None:
        snapshot = {"structure": "震荡整理"}
    
    return {
        "symbol": symbol,
        "structure_summary": snapshot.get("structure", "震荡"),
        "trend_strength": "中强",
        "message": "当前结构符合多头延续特征，但需警惕回调风险"
    }


def get_technical_tools():
    """返回技术分析相关工具"""
    return [analyze_market, get_key_levels, evaluate_structure]
