const sessionInput = document.querySelector("#session-id");
const newSessionButton = document.querySelector("#new-session");
const clearButton = document.querySelector("#clear-chat");
const messagesEl = document.querySelector("#messages");
const formEl = document.querySelector("#chat-form");
const inputEl = document.querySelector("#message-input");
const sendButton = document.querySelector("#send-button");
const statusText = document.querySelector("#status-text");
const template = document.querySelector("#message-template");

const SESSION_STORAGE_KEY = "marketassagent:web:session_id";

function generateSessionId() {
  const stamp = Date.now().toString(36);
  const random = Math.random().toString(36).slice(2, 8);
  return `web_${stamp}_${random}`;
}

function loadSessionId() {
  const saved = window.localStorage.getItem(SESSION_STORAGE_KEY);
  if (saved) {
    return saved;
  }
  const fresh = generateSessionId();
  window.localStorage.setItem(SESSION_STORAGE_KEY, fresh);
  return fresh;
}

function saveSessionId(value) {
  const trimmed = String(value || "").trim();
  if (!trimmed) {
    return;
  }
  window.localStorage.setItem(SESSION_STORAGE_KEY, trimmed);
}

function nowLabel() {
  return new Date().toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function setStatus(text, isError = false) {
  statusText.textContent = text;
  statusText.dataset.error = isError ? "1" : "0";
}

function setBusy(isBusy) {
  sendButton.disabled = isBusy;
  inputEl.disabled = isBusy;
  sessionInput.disabled = isBusy;
  newSessionButton.disabled = isBusy;
}

function appendMessage(role, text) {
  const fragment = template.content.cloneNode(true);
  const article = fragment.querySelector(".message");
  const roleEl = fragment.querySelector(".message-role");
  const timeEl = fragment.querySelector(".message-time");
  const bodyEl = fragment.querySelector(".message-body");

  article.dataset.role = role;
  roleEl.textContent = role === "user" ? "你" : "助手";
  timeEl.textContent = nowLabel();

  if (role === "assistant" && window.marked) {
    // assistant 消息用 Markdown 渲染
    bodyEl.innerHTML = window.marked.parse(text);
  } else {
    // user 消息或没有 marked 时用纯文本
    bodyEl.textContent = text;
  }

  messagesEl.appendChild(fragment);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function runAgent(text, sessionId) {
  const response = await fetch("/api/agent/run", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      text,
      session_id: sessionId,
    }),
  });

  if (!response.ok) {
    const raw = await response.text();
    throw new Error(raw || `HTTP ${response.status}`);
  }

  return response.json();
}

function clearMessages() {
  messagesEl.innerHTML = "";
}

sessionInput.value = loadSessionId();

sessionInput.addEventListener("change", () => {
  const next = String(sessionInput.value || "").trim() || generateSessionId();
  sessionInput.value = next;
  saveSessionId(next);
});

newSessionButton.addEventListener("click", () => {
  const next = generateSessionId();
  sessionInput.value = next;
  saveSessionId(next);
  clearMessages();
  setStatus("已创建新会话");
});

clearButton.addEventListener("click", () => {
  clearMessages();
  setStatus("界面已清空");
});

formEl.addEventListener("submit", async (event) => {
  event.preventDefault();

  const text = String(inputEl.value || "").trim();
  if (!text) {
    return;
  }

  const sessionId = String(sessionInput.value || "").trim() || generateSessionId();
  sessionInput.value = sessionId;
  saveSessionId(sessionId);

  appendMessage("user", text);
  inputEl.value = "";
  setBusy(true);
  setStatus("请求处理中...");

  try {
    const data = await runAgent(text, sessionId);
    appendMessage("assistant", data.reply || "分析完成");
    setStatus("完成");
  } catch (error) {
    const message = error instanceof Error ? error.message : "未知错误";
    appendMessage("assistant", `请求失败：${message}`);
    setStatus("请求失败", true);
  } finally {
    setBusy(false);
    inputEl.focus();
  }
});

inputEl.focus();
