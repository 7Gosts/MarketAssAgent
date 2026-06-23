# Codex 风格追问记忆改造执行计划

**日期**: 2026-06-18  
**适用范围**: `MarketAssAgent`（Direct Context 主链路）  
**目标**: 让追问体验更接近 Codex 风格（连续承接、少重复拉数、按需刷新事实），同时控制 token 成本与回复冗余。  
**前置约束**: 不回退 Planner/Orchestrator 主链路，不新增关键词路由，不在 Adapter 层做业务分流。

---

## 1. 目标与非目标

### 1.1 目标

1. 追问默认承接上一轮分析，不把每个问题当“新开题”。
2. 优先复用会话内结构化事实（`last_snapshot` / `tool_observation` / 最近结论），事实不足再刷新 `analyze_market`。
3. 压缩上下文与工具输出，显著降低 `prompt_tokens`，减少注意力分散。
4. 保持现有工具能力，新增字段以兼容方式演进。

### 1.2 非目标

1. 不新增“每次分析落独立文件”的语料沉淀方案。
2. 不引入意图分类器或硬编码路由。
3. 不做大规模 Prompt 重写。
4. 不更换模型网关或底层 SDK。

---

## 2. 当前问题（基于日志）

1. 单轮双周期分析出现高输入 token（`11k + 13k` 级别），说明上下文和工具结果体积偏大。
2. `last_snapshot` 在部分轮次为空，削弱追问复用能力。
3. `analyze_market` 现已补充交易字段，但返回体仍偏大，缺少明确“紧凑摘要优先”策略。
4. 回复文本篇幅偏长，影响效率与可执行性。

---

## 3. 总体改造策略

遵循“**先承接，再刷新；先摘要，再详情**”：

1. 默认复用上一轮结构化事实回答追问。
2. 仅在“时效性交易确认”或“事实缺失”时刷新工具。
3. 工具输出分层：
   - `compact_summary`（默认供 LLM消费）
   - `full_detail`（必要时展开）
4. 上下文注入设预算，超过阈值主动截断低价值字段。

---

## 4. 分阶段执行（可直接施工）

## Phase A：追问承接稳态化（低风险）

**目标**: 让追问先用已有事实，不盲目重拉行情。  
**改动文件**:

- `services/conversation_service.py`
- `core/agent_context.py`
- `core/prompt.py`
- `tests/test_direct_agent_context_flow.py`

**具体任务**:

1. 固化 `【最近对话结论】`（已接入）并限制摘要长度。
2. 在 Prompt 增加“追问先复用、事实不足再刷新”规则（已接入）。
3. 增加追问场景测试：
   - 有 `last_snapshot` 时不必强制二次分析；
   - 问“现在能不能开仓”允许触发刷新。

**验收标准**:

1. 追问场景能够在日志中看到 `has_recent_conclusion=True`。
2. 测试通过，且无主链路回归。
3. 不新增 adapter 侧路由代码。

---

## Phase B：工具输出分层（中风险，高收益）

**目标**: 减少工具结果对 prompt 的膨胀压力。  
**改动文件**:

- `tools/technical_analysis.py`
- `services/conversation_service.py`
- `core/graph.py`
- `tests/test_analysis_output_sanitize.py`

**具体任务**:

1. 在 `analyze_market` 新增 `compact_summary_v1`：
   - `symbol/interval/current_price/trend`
   - `nearest_support/nearest_resistance`
   - `actionability.bias/can_trade_now`
   - `risk_flags`
2. 保留现有 `analysis` 全量字段（兼容旧消费端）。
3. 在写入 `tool_observation` 时，优先抽取 `compact_summary_v1` 做 `summary/content`，限制 `content` 长度。
4. 为多标的返回新增 `comparison_brief_v1`（强弱、候选、等待条件）。

**验收标准**:

1. `analyze_market` 旧字段不丢失。
2. `tool_observation` 平均长度下降（可用日志观察）。
3. 双周期场景二次 reason 的 `prompt_tokens` 明显下降（目标：下降 20%+）。

---

## Phase C：上下文预算器（中风险）

**目标**: 控制 Direct Context 输入上限。  
**改动文件**:

- `core/agent_context.py`
- `services/conversation_service.py`
- `config/analysis_defaults.yaml`
- `tests/test_direct_agent_context_flow.py`

**具体任务**:

1. 新增配置：
   - `agent_context.max_chars`
   - `agent_context.max_recent_sources`
   - `agent_context.max_conclusion_chars`
2. `build_direct_agent_input()` 中按预算截断：
   - 优先保留 `last_snapshot` 关键字段；
   - 压缩 `user_profile` 低价值字段（如 `audit_log`）。
3. 日志输出预算命中情况（是否截断、截断字节数）。

**验收标准**:

1. Direct Context 生成可控，日志可见截断信息。
2. 关键事实字段不丢失。
3. 追问质量不下降（抽样人工检查）。

---

## Phase D：token 成本闭环（低风险）

**目标**: 建立“改造前后可量化”的成本观测。  
**改动文件**:

- `core/graph.py`（已支持 token usage）
- `scripts/replay_debug_output.py`
- `docs/09_LLM_INPUT_OUTPUT_TUNING_PLAN.md`

**具体任务**:

1. 记录每轮：
   - `prompt/completion/total`
   - `reasoning_tokens`
   - `cached_prompt_tokens`
2. 按 `session_id` 聚合单轮总 token。
3. 输出“高耗轮次 Top N”用于反推上下文优化。

**验收标准**:

1. 可以从日志直接估算单次对话成本。
2. 双周期分析场景能稳定追踪 token 变化。

---

## 5. 风险与回滚

### 5.1 风险点

1. 过度压缩导致事实丢失，回答变空泛。
2. 追问复用过强导致“该刷新时没刷新”。
3. 新增字段被旧消费方忽略，收益不明显。

### 5.2 回滚策略

1. 所有新字段采用“增量兼容”，不删旧字段。
2. 压缩策略由配置开关控制，可快速调大预算。
3. 关键逻辑在 `ConversationService` 内集中，回滚只需回退该层与 prompt 规则。

---

## 6. 执行顺序建议

1. 先做 Phase A（已部分完成）并补齐测试。
2. 再做 Phase B（收益最大）。
3. 然后做 Phase C（控输入上限）。
4. 最后做 Phase D（形成长期治理闭环）。

---

## 7. 开工前检查清单

1. `core/graph.py` token usage 日志可用。
2. `tests/test_direct_agent_context_flow.py` 覆盖最近结论注入。
3. `analyze_market` 统一入口稳定（单标的/多标的）。
4. 文档与实现口径一致（`08`/`09`/本文件）。

---

## 8. 当前实现进度（2026-06-18）

### Phase C 已落地（第一版）

已新增 `agent_context` 预算配置（`config/analysis_defaults.yaml`）：

- `max_chars: 13434`（初始值按近期高耗日志总量设置）
- `max_recent_sources: 3`
- `max_conclusion_chars: 240`

`ConversationService` 已在构建 Direct Context 时应用预算，并输出日志：

- `max_chars`
- `truncated`
- `dropped_chars`
- `input_chars`

### Phase D 已落地（第一版）

`core/graph.py` 已支持在 `MARKETASSAGENT_DEBUG_TOKEN_USAGE=1` 时把 token usage 落到：

- `~/.marketassagent/debug/llm_token_usage.jsonl`

新增回放脚本：

- `python3 scripts/replay_debug_output.py`
- 支持 `--session-id` 和 `--top` 聚合查看高耗轮次。




**✅ 以下是可直接复制给本地 Agent 执行的完整提示词（B. Trace / 执行日志持久化）**

---

**请严格按照以下要求实现 Orchestrator Trace 持久化功能**

### **目标**
把 `AssistantOrchestrator` 的执行 trace 持久化保存到数据库（使用现有 `FactStore`），方便后续分析每轮决策过程（task_type、工具使用、实际调用、耗时等）。

---

### **1. 新增 Trace 数据模型**

**新增文件**：`core/trace.py`

```python
# core/trace.py
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from datetime import datetime

class OrchestratorTrace(BaseModel):
    """Orchestrator 执行日志（持久化）"""
    
    trace_id: str = Field(default_factory=lambda: f"trace_{int(datetime.utcnow().timestamp())}")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    session_id: str
    user_id: str
    
    task_type: str
    user_message: str
    
    plan: Dict                      # ResponsePlan 的 dict 形式
    allowed_tools: List[str]
    actual_tools_called: List[str] = Field(default_factory=list)
    
    response_style: str
    key_focus: Optional[str] = None
    
    duration_ms: Optional[int] = None
    success: bool = True
    error_message: Optional[str] = None
    
    # 额外可扩展字段
    meta: Dict = Field(default_factory=dict)
    
    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
```

---

### **2. 在 MemoryAPI 中增加 Trace 存储方法**

在 `core/memory_api.py` 中新增：

```python
    async def save_orchestrator_trace(self, trace: OrchestratorTrace):
        """持久化 Orchestrator 执行日志"""
        await self.fact_store.write_fact(
            thread_id=f"trace_{trace.session_id}",
            fact_type="orchestrator_trace",
            payload=trace.dict(),
            tags=["trace", "orchestrator", trace.task_type],
            provenance={"source": "orchestrator", "trace_id": trace.trace_id}
        )
```

---

### **3. 修改 Orchestrator 执行逻辑**

在 `core/orchestrator.py` 的 `execute` 方法中，增加 trace 记录：

```python
    async def execute(self, plan: ResponsePlan, user_message: str, session):
        start_time = datetime.utcnow()
        
        trace = OrchestratorTrace(
            session_id=session.session_id,
            user_id=session.user_id,
            task_type=plan.task_type,
            user_message=user_message[:500],   # 防止过长
            plan=plan.dict(),
            allowed_tools=plan.required_tools,
            response_style=plan.response_style,
            key_focus=plan.key_focus,
        )

        try:
            # ... 原有执行逻辑 ...

            result = await self._handle_xxx(...)

            # 执行成功后回填
            trace.success = True
            trace.actual_tools_called = result.get("actual_tools_called", [])
            trace.duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            return result

        except Exception as e:
            trace.success = False
            trace.error_message = str(e)
            trace.duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            raise
        finally:
            # 持久化 trace
            await self.memory_api.save_orchestrator_trace(trace)
```

---

**请把以上全部内容直接复制给你的本地 Agent 执行**，并要求它完成以下工作：

- 创建 `core/trace.py`
- 在 `core/memory_api.py` 新增 `save_orchestrator_trace`
- 修改 `core/orchestrator.py` 的 `execute` 方法（增加 trace 记录）
- 更新相关测试文件
- 生成实现报告（包含使用示例）

执行完成后，把报告发给我，我再帮你 review 并给出下一阶段优化。

---

**额外建议**（可以加到提示词最后）：
- Trace 默认保存到 `facts` 表，`fact_type = "orchestrator_trace"`
- 后续可增加查询接口（按 session_id 或 task_type 查询最近 N 条 trace）
