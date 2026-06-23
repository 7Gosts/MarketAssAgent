from typing import Optional, Dict
from datetime import datetime
from core.state import AnalysisSnapshot


class SnapshotManager:
    """Analysis Snapshot 管理器 - 解决多轮追问上下文丢失问题"""
    
    def __init__(self):
        self.snapshots: Dict[str, AnalysisSnapshot] = {}  # session_id -> snapshot
    
    def save_snapshot(self, session_id: str, snapshot_data: Dict) -> AnalysisSnapshot:
        """保存分析快照"""
        snapshot: AnalysisSnapshot = {
            "symbol": snapshot_data.get("symbol"),
            "interval": snapshot_data.get("interval", "1d"),
            "trend": snapshot_data.get("trend", "震荡"),
            "key_levels": snapshot_data.get("key_levels", {}),
            "structure": snapshot_data.get("structure", ""),
            "structure_signals": snapshot_data.get("structure_signals", {}),
            "timestamp": datetime.now().isoformat(),
            "raw_insights": snapshot_data.get("raw_insights", "")
        }
        
        self.snapshots[session_id] = snapshot
        return snapshot
    
    def get_latest_snapshot(self, session_id: str) -> Optional[AnalysisSnapshot]:
        """获取最新快照（追问时使用）"""
        return self.snapshots.get(session_id)


# 全局单例
snapshot_manager = SnapshotManager()
