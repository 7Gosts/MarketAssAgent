# Frontend / Transport 收敛方案

**版本**: v1  
**日期**: 2026-06-08

## 1. 背景

当前项目已经有两种与 LLM 核心交互的方式：

- `cli/feishu_bot.py`：飞书长连接入口
- `cli/api_server.py` + `/api/agent/run`：HTTP API 入口

后续如果要增加前端，目标不应是再引入一套重型前端工程，而应把前端看成和 CLI、飞书长连接并列的**第三个 transport**。

## 2. 总体原则

### 2.1 保持“单核心，多入口”

项目应保持以下结构：

- `core/`：纯 LLM / Agent 核心
- `application/` 或服务层：统一的会话编排入口
- `transports/`：
  - CLI
  - Feishu long connection
  - HTTP API
  - Web UI

避免让前端、飞书、CLI 各自直接拼装一套 agent 调用链。

### 2.2 前端只做 UI，不做业务编排

Web 前端的职责应限制为：

- 展示消息列表
- 发送用户输入
- 接收流式/非流式响应
- 渲染工具调用、阶段状态、最终答复

不要把 symbol 识别、周期选择、session 状态机搬到前端。这些逻辑在 **`core/prompt.py` + `core/prompts.py` + Planner/Orchestrator** 中统一处理（旧 Router/Writer 层已删除，见 [`00_PROJECT_ARCHITECTURE.md`](00_PROJECT_ARCHITECTURE.md)）。

## 3. 推荐路线

### 方案 A：最小可用 Web Chat（推荐）

技术栈：

- FastAPI 静态文件
- 单页 HTML
- 少量原生 JS / Alpine.js
- 直接调用现有 `/api/agent/run`

适用场景：

- 目标是快速提供一个网页入口
- 不希望引入 Vite / Vue / React / 状态管理 / UI 框架
- 当前重点仍然是 Agent 核心而不是前端工程

优点：

- 依赖极少
- 启动链简单
- 维护成本低
- 更适合当前仓库体量

建议功能边界：

- 输入框 + 发送按钮
- 会话消息区
- `session_id` 管理
- 错误提示
- Markdown 渲染

后续可选增强：

- SSE 流式输出
- 工具调用状态展示
- `localStorage` 会话缓存

### 方案 B：轻量前后端分离

技术栈：

- Vite
- Vue 3 或 React
- 不引重量级 UI 框架
- 只保留一个 chat page

适用场景：

- 你明确需要后续扩展 richer UI
- 计划加入图表、卡片、对话侧栏、会话列表

约束：

- 不复用 `/home/yangtongliu/code/Stock_Analysis/frontend`
- 不提前引状态库、设计系统、复杂路由

### 当前不推荐

- 直接吸收 `Stock_Analysis/frontend`
- 先做多页面管理后台
- 在前端复刻 agent 路由与上下文逻辑

原因：

- 对当前项目来说过重
- 会把 transport 层和核心层再次耦合
- 会显著提高维护和调试成本

## 4. 可以借鉴的外部思路

### 4.1 Pi：保留“统一核心 + 多个界面”

可借鉴点：

- 一个统一 agent runtime，对外暴露 CLI、TUI、Web UI 等不同入口
- 前端/UI 是上层消费方，而不是第二套业务核心

对本项目的启发：

- `MarketReActAgent` 与后续服务层应保持稳定
- Feishu、CLI、Web UI 都只是 transport

### 4.2 AG-UI：把前后端交互协议化

可借鉴点：

- 用事件流而不是“一次请求只返一段最终文本”
- 让前端消费结构化事件：文本、工具调用、阶段状态、最终结果

对本项目的启发：

- 即便短期不完整接入 AG-UI，也可以先定义自己的轻量事件协议
- 例如：
  - `message_start`
  - `message_delta`
  - `tool_call`
  - `tool_result`
  - `final_recommendation`
  - `error`

### 4.3 FastAPI + SSE + 极简前端

可借鉴点：

- 后端保持 FastAPI
- 前端用最薄的聊天页
- 使用 SSE 接收 token 流或事件流

对本项目的启发：

- 这是当前最合适的 Web UI 第一阶段实现
- 先把 transport 打通，再考虑复杂交互

### 4.4 LangChain 社区的经验

可借鉴点：

- 自定义 FastAPI 后端并不要求前端绑定某个专用平台
- 只要 SSE 事件格式稳定，前端就可以用统一 transport 消费
- 如果要展示 reasoning / tool calls，后端必须主动输出这些结构化事件，而不仅是最终文本

对本项目的启发：

- 若未来接流式前端，后端接口设计应优先考虑“事件类型”
- 不应只暴露一个最终 `recommendation.text`

## 5. 建议的演进顺序

### Phase 1：先把服务层抽出来

建议新增一个统一服务入口，例如：

- `application/conversation_service.py`

负责：

- 接收用户输入
- 装载历史
- 调用 `Router`
- 调用 `MarketReActAgent`
- 调用 `Writer`
- 返回统一结构

这样：

- CLI 调它
- Feishu 调它
- API 调它
- Web UI 也调它

### Phase 2：做一个极轻网页入口

新增：

- `web/index.html`
- `web/app.js`
- `web/styles.css`

先只支持：

- 非流式问答
- 基础 Markdown
- 简单历史展示

### Phase 3：升级为 SSE 事件流

新增一个流式接口，例如：

- `/api/agent/stream`

事件建议：

- `status`
- `delta`
- `tool_call`
- `tool_result`
- `final`
- `error`

### Phase 4：若需要，再考虑协议标准化

如果后面出现：

- 多前端入口
- 移动端
- 更复杂可视化
- 第三方界面接入

再评估：

- AG-UI
- CopilotKit / 类似协议层

不要在当前阶段过早引入。

## 6. 当前结论

对 MarketAssAgent 来说，最合适的下一步是：

1. 保持单核心、多 transport
2. 新增统一服务层，削薄 transport
3. 做一个极轻 Web Chat，而不是搬运重前端
4. Web 第一阶段优先用 FastAPI + 单页 HTML/JS
5. 第二阶段再升级 SSE 和结构化事件

这条路线比直接复用 `Stock_Analysis/frontend` 更符合当前项目规模与维护成本。
