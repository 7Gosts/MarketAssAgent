# MarketReActAgent

基于 LangGraph + ReAct 架构的金融市场智能 Agent，支持股票、加密货币、黄金的技术分析、多轮对话、条件化建议和纸账户模拟。

## 核心特性
- LangGraph 状态机驱动的多轮 ReAct 流程
- AnalysisSnapshot 机制（解决追问上下文丢失）
- 条件化交易建议 + 严格免责
- 支持飞书 + Web 多入口

## 快速启动

```bash
cp .env.example .env
pip install -r requirements.txt
python main.py
```

访问 `http://localhost:8000` 测试。
