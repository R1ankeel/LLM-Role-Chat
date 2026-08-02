/**
 * AI Roleplay Chat — frontend application (vanilla JS).
 * State management, API calls, rendering, modals, typing indicator.
 */

// ===== API base =====
const API = "/api";

// ===== App State =====
const AppState = {
  chats: [],
  currentChatId: null,
  currentChat: null,
  characters: [],
  messages: [],
  sendingChatIds: new Set(),
  activeStream: null,
  chatLoadSeq: 0,
  generationPollTimer: null,
  models: [],
};

function abortActiveStream() {
  if (AppState.activeStream?.abortController) {
    AppState.activeStream.abortController.abort();
    AppState.activeStream = null;
  }
}

function setSendingUI(sending) {
  const sendBtn = document.getElementById("btn-send");
  const stopBtn = document.getElementById("btn-stop");
  if (sending) {
    sendBtn.classList.add("hidden");
    stopBtn.classList.remove("hidden");
  } else {
    sendBtn.classList.remove("hidden");
    stopBtn.classList.add("hidden");
  }
}

async function stopGeneration() {
  const chatId = AppState.currentChatId;
  if (!chatId) return;
  if (AppState.activeStream) {
    AppState.activeStream.abortController.abort();
    AppState.activeStream = null;
  }
  try {
    await fetch(`${API}/chats/${chatId}/stop-generation`, { method: "POST" });
  } catch (_) {}
  AppState.sendingChatIds.delete(chatId);
  clearGenerationPoll();
  setSendingUI(false);
  disableInput(false);
  hideTyping();
  sessionStorage.removeItem(GEN_STORAGE_KEY);
  await syncMessages(chatId);
  renderMessages();
}

const GEN_STORAGE_KEY = "rolellm_active_gen";

function saveGenerationState(chatId) {
  try {
    sessionStorage.setItem(GEN_STORAGE_KEY, JSON.stringify({ chatId, timestamp: Date.now() }));
  } catch (_) {}
}

function clearGenerationState() {
  try { sessionStorage.removeItem(GEN_STORAGE_KEY); } catch (_) {}
}

function clearGenerationPoll() {
  if (AppState.generationPollTimer) {
    clearTimeout(AppState.generationPollTimer);
    AppState.generationPollTimer = null;
  }
}

async function startGenerationPoll(chatId) {
  clearGenerationPoll();
  const poll = async () => {
    if (chatId !== AppState.currentChatId) return;
    try {
      await syncMessages(chatId);
      const status = await apiRequest("GET", `/chats/${chatId}/generation-status`);
      if (!status.active) {
        AppState.sendingChatIds.delete(chatId);
        setSendingUI(false);
        disableInput(false);
        hideTyping();
        clearGenerationState();
        clearGenerationPoll();
        renderMessages();
        return;
      }
      AppState.generationPollTimer = setTimeout(poll, 2000);
    } catch (_) {
      AppState.generationPollTimer = setTimeout(poll, 5000);
    }
  };
  AppState.generationPollTimer = setTimeout(poll, 2000);
}

// ===== Mobile detection =====
let isMobile = window.innerWidth < 768;

// ===== Update view based on mobile state =====
function updateView() {
  const appContainer = document.getElementById("app");
  if (isMobile) {
    // For mobile, switch between list and chat views
    if (AppState.currentChatId) {
      appContainer.className = "view-chat";
    } else {
      appContainer.className = "view-list";
    }
  } else {
    // For desktop, always show both sidebar and main area
    appContainer.className = "";
  }
  
  // Ensure input area is properly positioned on mobile
  if (isMobile) {
    const inputArea = document.getElementById("input-area");
    if (inputArea) {
      inputArea.style.position = "sticky";
      inputArea.style.bottom = "0";
      inputArea.style.zIndex = "10";
    }
  }
}

// ===== Add mobile back button functionality =====
function setupMobileBackButton() {
  const mobileBackBtn = document.getElementById('mobileBackBtn');
  if (mobileBackBtn) {
    mobileBackBtn.addEventListener('click', () => {
      // Hide chat-area, show sidebar
      const appContainer = document.getElementById("app");
      appContainer.classList.remove('view-chat');
      appContainer.classList.add('view-list');
      // Optional: reset active chat in state
      AppState.currentChatId = null;
// Update header title
document.getElementById('chat-title').textContent = '';
    });
  }
}

// ===== Show mobile back button when entering chat view =====
function showMobileBackButton() {
  const mobileBackBtn = document.getElementById('mobileBackBtn');
  if (mobileBackBtn && isMobile) {
    mobileBackBtn.style.display = 'inline-flex';
  }
}

// ===== Handle window resize =====
window.addEventListener("resize", () => {
  const wasMobile = isMobile;
  isMobile = window.innerWidth < 768;
  
  if (wasMobile !== isMobile) {
    updateView();
  }
});

// ===== Orientation change handling =====
window.addEventListener("orientationchange", () => {
  setTimeout(() => {
    const wasMobile = isMobile;
    isMobile = window.innerWidth < 768;
    
    if (wasMobile !== isMobile) {
      updateView();
    }
  }, 100);
});

// ===== localStorage helpers =====
const LS_KEY = "ai_roleplay_last_chat";

function saveLastChatId(id) {
  try { localStorage.setItem(LS_KEY, String(id)); } catch (_) {}
}

function loadLastChatId() {
  try { return parseInt(localStorage.getItem(LS_KEY)) || null; } catch (_) { return null; }
}

// ===== Hash helpers for avatar colors =====
function hashColor(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  const hue = Math.abs(hash % 360);
  return `hsl(${hue}, 60%, 50%)`;
}

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

// ===== Thinking mode helpers =====
function isThinkingMode(chat) {
  if (!chat || chat.thinking_mode === undefined || chat.thinking_mode === null) {
    return true;
  }
  return Boolean(chat.thinking_mode);
}

function setModeToggle(containerId, thinking) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.querySelectorAll(".mode-option").forEach((btn) => {
    const isOn = btn.dataset.thinking === "true";
    btn.classList.toggle("active", isOn === Boolean(thinking));
  });
}

function getModeToggle(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return true;
  const active = container.querySelector(".mode-option.active");
  if (!active) return true;
  return active.dataset.thinking === "true";
}

function bindModeToggle(containerId) {
  const container = document.getElementById(containerId);
  if (!container || container.dataset.bound === "1") return;
  container.dataset.bound = "1";
  container.querySelectorAll(".mode-option").forEach((btn) => {
    btn.addEventListener("click", () => {
      setModeToggle(containerId, btn.dataset.thinking === "true");
    });
  });
}

function updateThinkingBadge() {
  const btn = document.getElementById("btn-thinking-toggle");
  if (!btn) return;
  const thinking = isThinkingMode(AppState.currentChat);
  btn.textContent = thinking ? "🧠 Размышление" : "⚡ Мгновенно";
  btn.classList.toggle("mode-instant", !thinking);
  btn.classList.toggle("mode-thinking", thinking);
  btn.title = thinking
    ? "Режим: с размышлением (нажмите для быстрого)"
    : "Режим: быстрый (нажмите для размышления)";
}

async function toggleThinkingMode() {
  if (!AppState.currentChatId || !AppState.currentChat) return;
  if (AppState.sendingChatIds.has(AppState.currentChatId)) {
    showToast("Дождитесь окончания генерации");
    return;
  }
  const next = !isThinkingMode(AppState.currentChat);
  try {
    const updated = await apiRequest("PUT", `/chats/${AppState.currentChatId}`, {
      thinking_mode: next,
    });
    AppState.currentChat = { ...AppState.currentChat, ...updated };
    const idx = AppState.chats.findIndex((c) => c.id === AppState.currentChatId);
    if (idx >= 0) AppState.chats[idx] = { ...AppState.chats[idx], ...updated };
    updateThinkingBadge();
    setModeToggle("settings-mode", next);
    showToast(next ? "Режим: с размышлением" : "Режим: быстрый", "success");
  } catch (e) {
    showToast("Ошибка: " + e.message);
  }
}

// ===== Toast notifications =====
function showToast(message, type = "error") {
  const container = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => { el.remove(); }, 5000);
}

// ===== API helpers =====
async function apiRequest(method, path, body = null) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${API}${path}`, opts);
  if (res.status === 204) return null;
  const data = await res.json();
  if (!res.ok) {
    const msg = data.detail || `Ошибка ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

// ===== Load / Render Chats =====
async function loadChats() {
  try {
    AppState.chats = await apiRequest("GET", "/chats");
    renderChatList();
    // Restore last chat from localStorage
    const lastId = loadLastChatId();
    if (lastId && AppState.chats.some(c => c.id === lastId)) {
      await selectChat(lastId);
    }
  } catch (e) {
    showToast("Не удалось загрузить чаты: " + e.message);
  }
}

function renderChatList() {
  const container = document.getElementById("chat-list");
  container.innerHTML = "";
  for (const chat of AppState.chats) {
    const div = document.createElement("div");
    div.className = "chat-item" + (chat.id === AppState.currentChatId ? " active" : "");
    div.innerHTML = `<div class="chat-item-body">
        <div class="chat-item-name">${escapeHtml(chat.name)}</div>
        <div class="chat-item-preview">${escapeHtml(chat.general_prompt.slice(0, 60)) || "Нет описания"}</div>
      </div>
      <button class="btn btn-sm btn-danger chat-item-delete" title="Удалить чат">✕</button>`;
    div.addEventListener("click", () => selectChat(chat.id));
    div.querySelector(".chat-item-delete").addEventListener("click", (e) => {
      e.stopPropagation();
      deleteChat(chat.id);
    });
    container.appendChild(div);
  }
}

// ===== Select / Load Chat =====
async function selectChat(chatId) {
  const seq = ++AppState.chatLoadSeq;
  AppState.currentChatId = chatId;
  hideTyping();
  saveLastChatId(chatId);
  renderChatList();

  const chatIsSending = AppState.sendingChatIds.has(chatId);
  disableInput(chatIsSending);

  try {
    const detail = await apiRequest("GET", `/chats/${chatId}`);
    if (seq !== AppState.chatLoadSeq) return;
    AppState.currentChat = detail;
    AppState.characters = detail.characters || [];
    AppState.messages = detail.messages || [];
    renderHeader();
    renderMessages();
    if (AppState.sendingChatIds.has(chatId)) {
      showTyping("Ожидание ответа...");
      disableInput(true);
      setSendingUI(true);
      clearGenerationPoll();
    } else {
      // Check if generation is active on backend (page refresh recovery)
      try {
        const status = await apiRequest("GET", `/chats/${chatId}/generation-status`);
        if (status.active) {
          AppState.sendingChatIds.add(chatId);
          setSendingUI(true);
          disableInput(true);
          showTyping("Генерация продолжается...");
          startGenerationPoll(chatId);
        } else {
          enableInput(true);
        }
      } catch (_) {
        enableInput(true);
      }
    }
    scrollToBottom();
    
    // Update view for mobile
    if (isMobile) {
      updateView();
      showMobileBackButton(); // Show back button when entering chat view
    }
  } catch (e) {
    if (seq !== AppState.chatLoadSeq) return;
    showToast("Ошибка загрузки чата: " + e.message);
  }
}

// ===== Render Header =====
function renderHeader() {
  const chat = AppState.currentChat;
  if (!chat) return;
  document.getElementById("chat-header").classList.remove("hidden");
  document.getElementById("chat-title").textContent = chat.name;
  document.getElementById("input-area").classList.remove("hidden");
  // Show model name
  const modelEl = document.getElementById("chat-model");
  if (modelEl) {
    modelEl.textContent = chat.model_name || "";
  }
  updateThinkingBadge();
}

// ===== Render Messages =====
function getLastRoundMessageIds() {
  const msgs = AppState.messages;
  let lastUserIdx = -1;
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === "user") { lastUserIdx = i; break; }
  }
  const ids = new Set();
  for (let i = Math.max(0, lastUserIdx); i < msgs.length; i++) {
    if (msgs[i].id != null) ids.add(String(msgs[i].id));
  }
  return ids;
}

function createActionButton(label, title, onClick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "msg-action-btn";
  btn.textContent = label;
  btn.title = title;
  btn.addEventListener("click", onClick);
  return btn;
}

function createMessageElement(msg) {
  if (msg.role === "system") return null;

  const div = document.createElement("div");
  div.className = `message ${msg.role === "user" ? "user" : "character"}`;
  if (msg.id != null) {
    div.dataset.messageId = String(msg.id);
  }

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  if (msg.role === "user") {
    avatar.textContent = "Я";
    avatar.style.background = "var(--accent-blue)";
  } else {
    const charName = getCharacterName(msg.character_id);
    avatar.textContent = charName.charAt(0).toUpperCase();
    avatar.style.background = hashColor(charName);
  }

  const body = document.createElement("div");
  body.className = "message-body";

  const header = document.createElement("div");
  header.className = "message-header";

  const author = document.createElement("span");
  author.className = "message-author";
  author.textContent = msg.role === "user" ? "Игрок" : getCharacterName(msg.character_id);

  const time = document.createElement("span");
  time.className = "message-time";
  time.textContent = formatTime(msg.timestamp);

  const content = document.createElement("div");
  content.className = "message-content";
  content.textContent = msg.content;

  header.appendChild(author);
  header.appendChild(time);
  body.appendChild(header);
  body.appendChild(content);

  const lastRoundIds = getLastRoundMessageIds();
  const showActions =
    msg.id != null &&
    lastRoundIds.has(String(msg.id)) &&
    !AppState.sendingChatIds.has(AppState.currentChatId);

  if (showActions) {
    const actions = document.createElement("div");
    actions.className = "message-actions";
    if (msg.role === "user") {
      actions.appendChild(
        createActionButton("🗑", "Удалить сообщение", () => deleteMessage(msg))
      );
    } else {
      actions.appendChild(
        createActionButton("🔄", "Перегенерировать ответ", () => regenerateMessage(msg))
      );
      actions.appendChild(
        createActionButton("🗑", "Удалить сообщение", () => deleteMessage(msg))
      );
    }
    body.appendChild(actions);
  }

  div.appendChild(avatar);
  div.appendChild(body);
  return div;
}

function renderMessages() {
  const container = document.getElementById("messages");
  container.innerHTML = "";
  for (const msg of AppState.messages) {
    const el = createMessageElement(msg);
    if (el) container.appendChild(el);
  }
  scrollToBottom();
}

function appendMessage(msg) {
  if (msg.role === "system") return;
  const container = document.getElementById("messages");
  const el = createMessageElement(msg);
  if (el) {
    container.appendChild(el);
    scrollToBottom();
  }
}

function replaceOptimisticUserMessage(serverMsg, chatId) {
  if (chatId !== AppState.currentChatId) return;
  const idx = AppState.messages.findIndex(m => m.role === "user" && m.id == null);
  if (idx >= 0) {
    AppState.messages[idx] = serverMsg;
  } else if (!AppState.messages.some(m => m.id === serverMsg.id)) {
    AppState.messages.push(serverMsg);
  }

  const container = document.getElementById("messages");
  const optimisticEl = container.querySelector(".message.user:not([data-message-id])");
  const el = createMessageElement(serverMsg);
  if (optimisticEl && el) {
    optimisticEl.replaceWith(el);
  } else if (el && !container.querySelector(`[data-message-id="${serverMsg.id}"]`)) {
    container.appendChild(el);
  }
  scrollToBottom();
}

function addStreamedMessage(msg, chatId) {
  if (chatId !== AppState.currentChatId) return;
  if (msg.role === "user") {
    replaceOptimisticUserMessage(msg, chatId);
    return;
  }
  if (AppState.messages.some(m => m.id === msg.id)) return;
  AppState.messages.push(msg);
  appendMessage(msg);
}

function getSortedCharacters() {
  return [...AppState.characters].sort((a, b) => a.order_index - b.order_index);
}

function showNextCharacterTyping(repliedCount) {
  const chars = getSortedCharacters();
  if (repliedCount < chars.length) {
    showTyping(`${chars[repliedCount].name} печатает...`);
  } else {
    hideTyping();
  }
}

async function syncMessages(chatId = AppState.currentChatId) {
  if (chatId === null || chatId !== AppState.currentChatId) return;
  try {
    const detail = await apiRequest("GET", `/chats/${chatId}`);
    if (chatId !== AppState.currentChatId) return;
    const serverMessages = detail.messages || [];
    const knownIds = new Set(AppState.messages.filter(m => m.id != null).map(m => m.id));
    let added = false;

    for (const msg of serverMessages) {
      if (msg.id != null && !knownIds.has(msg.id)) {
        AppState.messages.push(msg);
        knownIds.add(msg.id);
        added = true;
      }
    }

    if (added) {
      AppState.messages.sort((a, b) => {
        const ta = new Date(a.timestamp).getTime();
        const tb = new Date(b.timestamp).getTime();
        return ta - tb || (a.id || 0) - (b.id || 0);
      });
      renderMessages();
    }
  } catch (e) {
    showToast("Не удалось синхронизировать сообщения: " + e.message);
  }
}

async function readSSEStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";

    for (const part of parts) {
      const line = part.split("\n").find(l => l.startsWith("data: "));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(6)));
      } catch (_) {}
    }
  }

  if (buffer.trim()) {
    const line = buffer.split("\n").find(l => l.startsWith("data: "));
    if (line) {
      try {
        onEvent(JSON.parse(line.slice(6)));
      } catch (_) {}
    }
  }
}

function getCharacterName(characterId) {
  if (!characterId) return "Персонаж";
  const char = AppState.characters.find(c => c.id === characterId);
  return char ? char.name : "Персонаж";
}

function scrollToBottom() {
  const container = document.getElementById("messages-container");
  setTimeout(() => { container.scrollTop = container.scrollHeight; }, 50);
}

// ===== Send Message =====
async function sendMessage() {
  if (AppState.currentChatId === null) return;
  if (AppState.sendingChatIds.has(AppState.currentChatId)) return;
  const input = document.getElementById("message-input");
  const text = input.value.trim();
  if (!text) return;

  const streamChatId = AppState.currentChatId;
  const abortController = new AbortController();
  AppState.activeStream = { chatId: streamChatId, abortController };
  AppState.sendingChatIds.add(streamChatId);

  input.value = "";
  disableInput(true);
  setSendingUI(true);
  showTyping("Ожидание ответа...");
  saveGenerationState(streamChatId);

  const userMsg = {
    role: "user",
    content: text,
    timestamp: new Date().toISOString(),
    character_id: null,
  };
  AppState.messages.push(userMsg);
  renderMessages();

  let characterReplies = 0;
  let streamCompleted = false;
  let streamError = null;
  const isActiveChat = () => streamChatId === AppState.currentChatId;
  
  // Track streaming message element per character
  let streamingMessageEl = null;
  let streamingCharId = null;

  try {
    const res = await fetch(`${API}/chats/${streamChatId}/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text }),
      signal: abortController.signal,
    });

    if (!res.ok) {
      let detail = `Ошибка ${res.status}`;
      try {
        const data = await res.json();
        detail = data.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }

    await readSSEStream(res, (event) => {
      if (event.type === "token") {
        if (isActiveChat()) {
          if (!streamingMessageEl || streamingCharId !== event.character_id) {
            // New character started streaming
            if (streamingMessageEl) {
              streamingMessageEl.remove();
            }
            streamingMessageEl = createStreamingMessageElement(event.character_id);
            streamingCharId = event.character_id;
          }
          appendToStreamingMessage(streamingMessageEl, event.text);
        }
      } else if (event.type === "message" && event.message) {
        if (isActiveChat()) {
          if (streamingMessageEl) {
            finalizeStreamingMessage(streamingMessageEl, event.message);
            streamingMessageEl = null;
            streamingCharId = null;
          } else {
            addStreamedMessage(event.message, streamChatId);
          }
          if (event.message.role === "user") {
            showNextCharacterTyping(0);
          } else if (event.message.role === "character") {
            characterReplies += 1;
            showNextCharacterTyping(characterReplies);
          }
        }
      } else if (event.type === "done") {
        streamCompleted = true;
        if (isActiveChat()) {
          if (streamingMessageEl) {
            streamingMessageEl.remove();
            streamingMessageEl = null;
          }
          hideTyping();
        }
      } else if (event.type === "error") {
        streamError = event.detail || "Ошибка генерации";
      }
    });

    if (isActiveChat()) {
      if (streamError) {
        showToast("Ошибка: " + streamError);
        await syncMessages(streamChatId);
      } else if (!streamCompleted) {
        await syncMessages(streamChatId);
      }
    }
  } catch (e) {
    if (e.name === "AbortError") return;
    if (isActiveChat()) {
      hideTyping();
      AppState.messages.pop();
      renderMessages();
      showToast("Ошибка: " + e.message);
      await syncMessages(streamChatId);
    }
  } finally {
    AppState.sendingChatIds.delete(streamChatId);
    if (AppState.activeStream?.abortController === abortController) {
      AppState.activeStream = null;
    }
    if (isActiveChat()) {
      disableInput(false);
      setSendingUI(false);
      hideTyping();
      renderMessages();
    }
    clearGenerationState();
    clearGenerationPoll();
  }
}

// ===== Delete Chat =====
async function deleteChat(chatId) {
  const chat = AppState.chats.find(c => c.id === chatId);
  const name = chat?.name || "чат";
  if (!confirm(`Удалить чат «${name}»? Все сообщения, персонажи и воспоминания будут удалены безвозвратно.`)) return;

  try {
    await apiRequest("DELETE", `/chats/${chatId}`);
    AppState.chats = AppState.chats.filter(c => c.id !== chatId);

    AppState.sendingChatIds.delete(chatId);
    if (AppState.currentChatId === chatId) {
      abortActiveStream();
      AppState.currentChatId = null;
      AppState.currentChat = null;
      AppState.messages = [];
      AppState.characters = [];
      localStorage.removeItem(LS_KEY);
      document.getElementById("chat-header").classList.add("hidden");
      document.getElementById("input-area").classList.add("hidden");
      document.getElementById("messages").innerHTML = "";
      hideTyping();
      updateView();
    }

    renderChatList();
    showToast("Чат удалён", "success");
  } catch (e) {
    showToast("Ошибка: " + e.message);
  }
}

// ===== Delete Message =====
async function deleteMessage(msg) {
  const chatId = AppState.currentChatId;
  if (chatId === null || msg.id == null) return;
  if (AppState.sendingChatIds.has(chatId)) return;

  const isUser = msg.role === "user";
  const label = isUser
    ? "это сообщение и все последующие ответы NPC"
    : "этот ответ персонажа";
  if (!confirm(`Удалить ${label}? Действие необратимо (удаляется из БД).`)) return;

  try {
    await apiRequest("DELETE", `/chats/${chatId}/messages/${msg.id}`);
    const delIdx = AppState.messages.findIndex(m => m.id === msg.id);
    if (delIdx >= 0) {
      if (isUser) {
        AppState.messages.splice(delIdx);
      } else {
        AppState.messages.splice(delIdx, 1);
      }
    }
    renderMessages();
    showToast("Сообщение удалено", "success");
  } catch (e) {
    showToast("Ошибка: " + e.message);
  }
}

// ===== Regenerate Message =====
async function regenerateMessage(msg) {
  const chatId = AppState.currentChatId;
  if (chatId === null || msg.id == null) return;
  if (AppState.sendingChatIds.has(chatId)) return;

  const abortController = new AbortController();
  AppState.activeStream = { chatId, abortController };
  AppState.sendingChatIds.add(chatId);

  disableInput(true);
  setSendingUI(true);
  showTyping(`${getCharacterName(msg.character_id)} перепечатывает...`);
  saveGenerationState(chatId);

  // Remove the old reply from state + DOM; stream a fresh one
  const oldIdx = AppState.messages.findIndex(m => m.id === msg.id);
  if (oldIdx >= 0) AppState.messages.splice(oldIdx, 1);
  renderMessages();

  let streamingEl = createStreamingMessageElement(msg.character_id);
  let streamError = null;
  let streamCompleted = false;

  try {
    const res = await fetch(
      `${API}/chats/${chatId}/messages/${msg.id}/regenerate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: abortController.signal,
      }
    );

    if (!res.ok) {
      let detail = `Ошибка ${res.status}`;
      try {
        const data = await res.json();
        detail = data.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }

    await readSSEStream(res, (event) => {
      if (event.type === "token" && event.character_id === msg.character_id) {
        if (streamingEl) appendToStreamingMessage(streamingEl, event.text);
      } else if (event.type === "message" && event.message) {
        if (AppState.messages.some(m => m.id === event.message.id)) return;
        AppState.messages.push(event.message);
        if (streamingEl) {
          finalizeStreamingMessage(streamingEl, event.message);
          streamingEl = null;
        }
      } else if (event.type === "done") {
        streamCompleted = true;
      } else if (event.type === "error") {
        streamError = event.detail || "Ошибка перегенерации";
      }
    });

    if (chatId === AppState.currentChatId) {
      if (streamError) {
        showToast("Ошибка: " + streamError);
        await syncMessages(chatId);
      } else if (!streamCompleted) {
        await syncMessages(chatId);
      }
    }
  } catch (e) {
    if (e.name === "AbortError") return;
    if (chatId === AppState.currentChatId) {
      showToast("Ошибка: " + e.message);
      await syncMessages(chatId);
    }
  } finally {
    if (streamingEl) streamingEl.remove();
    AppState.sendingChatIds.delete(chatId);
    if (AppState.activeStream?.abortController === abortController) {
      AppState.activeStream = null;
    }
    if (chatId === AppState.currentChatId) {
      disableInput(false);
      setSendingUI(false);
      hideTyping();
      renderMessages();
    }
    clearGenerationState();
    clearGenerationPoll();
  }
}

// ===== Clear History Modal =====
function showClearHistoryModal() {
  if (!AppState.currentChatId) return;
  
  // Remove existing modal if any
  const existing = document.getElementById("modal-clear-history");
  if (existing) existing.remove();
  
  const modal = document.createElement("div");
  modal.id = "modal-clear-history";
  modal.className = "modal-overlay";
  modal.innerHTML = `
    <div class="modal" style="max-width:400px;">
      <h3>Очистить историю</h3>
      <p style="color:var(--text-secondary);margin-bottom:16px;">Выберите, что удалить:</p>
      <div style="display:flex;flex-direction:column;gap:8px;">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
          <input type="radio" name="clear-scope" value="messages" checked>
          <span>Только сообщения (сохранить воспоминания и саммари)</span>
        </label>
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
          <input type="radio" name="clear-scope" value="messages_memories">
          <span>Сообщения + воспоминания (сохранить саммари)</span>
        </label>
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
          <input type="radio" name="clear-scope" value="full">
          <span>Полная очистка (сообщения + воспоминания + саммари)</span>
        </label>
      </div>
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px;">
        <button class="btn btn-secondary" id="btn-clear-cancel">Отмена</button>
        <button class="btn btn-danger" id="btn-clear-confirm">Очистить</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.classList.remove("hidden");
  
  document.getElementById("btn-clear-cancel").onclick = () => modal.remove();
  document.getElementById("btn-clear-confirm").onclick = async () => {
    const scope = document.querySelector('input[name="clear-scope"]:checked').value;
    modal.remove();
    await clearHistoryWithScope(scope);
  };
}

async function clearHistoryWithScope(scope) {
  if (!AppState.currentChatId) return;
  try {
    await apiRequest("DELETE", `/chats/${AppState.currentChatId}/messages?scope=${scope}`);
    AppState.messages = [];
    renderMessages();
    showToast(`История очищена (${scope})`, "success");
  } catch (e) {
    showToast("Ошибка: " + e.message);
  }
}

// ===== Typing Indicator =====
function showTyping(text) {
  const ind = document.getElementById("typing-indicator");
  ind.classList.remove("hidden");
  document.getElementById("typing-text").textContent = text;
}

function hideTyping() {
  document.getElementById("typing-indicator").classList.add("hidden");
}

// ===== Input helpers =====
function disableInput(disabled) {
  document.getElementById("message-input").disabled = disabled;
  document.getElementById("btn-send").disabled = disabled;
}

function enableInput(enabled) {
  document.getElementById("message-input").disabled = !enabled;
  document.getElementById("btn-send").disabled = !enabled;
}

// ===== Streaming message helpers =====
let streamingMessageEl = null;
let streamingCharId = null;

function createStreamingMessageElement(characterId) {
    const char = AppState.characters.find(c => c.id === characterId);
    const div = document.createElement("div");
    div.className = "message character streaming";
    div.dataset.streamingCharId = String(characterId);
    
    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = char?.name?.charAt(0).toUpperCase() || "?";
    avatar.style.background = hashColor(char?.name || "");
    
    const body = document.createElement("div");
    body.className = "message-body";
    
    const header = document.createElement("div");
    header.className = "message-header";
    
    const author = document.createElement("span");
    author.className = "message-author";
    author.textContent = char?.name || "Персонаж";
    
    const content = document.createElement("div");
    content.className = "message-content streaming-text";
    content.textContent = "";
    
    header.appendChild(author);
    body.appendChild(header);
    body.appendChild(content);
    div.appendChild(avatar);
    div.appendChild(body);
    
    document.getElementById("messages").appendChild(div);
    scrollToBottom();
    return div;
}

function appendToStreamingMessage(el, text) {
    const contentEl = el.querySelector(".streaming-text");
    if (contentEl) contentEl.textContent += text;
    scrollToBottom();
}

function finalizeStreamingMessage(streamingEl, serverMsg) {
    const realEl = createMessageElement(serverMsg);
    streamingEl.replaceWith(realEl);
}

// ===== Escape HTML =====
function escapeHtml(str) {
    if (!str) return "";
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
}

// ===== New Chat Modal =====
document.getElementById("btn-new-chat").addEventListener("click", () => {
  document.getElementById("modal-new-chat").classList.remove("hidden");
  document.getElementById("new-chat-name").value = "";
  document.getElementById("new-chat-prompt").value = "";
  document.getElementById("new-chat-model").value = "qwen3-coder:30b-a3b-q4_K_M";
  setModeToggle("new-chat-mode", true);
  document.getElementById("new-chat-name").focus();
});

document.getElementById("btn-new-chat-cancel").addEventListener("click", () => {
  document.getElementById("modal-new-chat").classList.add("hidden");
});

document.getElementById("btn-new-chat-confirm").addEventListener("click", async () => {
  const name = document.getElementById("new-chat-name").value.trim();
  if (!name) { showToast("Введите название чата"); return; }
  const prompt = document.getElementById("new-chat-prompt").value.trim();
  const model = document.getElementById("new-chat-model").value.trim();
  const thinking_mode = getModeToggle("new-chat-mode");
  try {
    const chat = await apiRequest("POST", "/chats", {
      name,
      general_prompt: prompt,
      model_name: model,
      thinking_mode,
    });
    document.getElementById("modal-new-chat").classList.add("hidden");
    AppState.chats.unshift(chat);
    renderChatList();
    await selectChat(chat.id);
    showToast("Чат создан", "success");
  } catch (e) {
    showToast("Ошибка: " + e.message);
  }
});

// ===== Settings Modal =====
async function loadOllamaModels() {
  try {
    const data = await apiRequest("GET", "/models");
    AppState.models = Array.isArray(data.models) ? data.models : [];
    if (data.error) {
      showToast(data.error, "info");
      return false;
    }
    return true;
  } catch (e) {
    AppState.models = [];
    showToast("Не удалось загрузить список моделей: " + e.message, "info");
    return false;
  }
}

function populateModelSelect(currentModel) {
  const select = document.getElementById("settings-model");
  select.innerHTML = "";
  const names = AppState.models.slice();
  const current = (currentModel || "").trim();
  if (current && !names.includes(current)) {
    names.unshift(current);
  }
  if (names.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = current || "Нет доступных моделей";
    select.appendChild(opt);
  } else {
    for (const name of names) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      if (name === current) opt.selected = true;
      select.appendChild(opt);
    }
  }
}

document.getElementById("btn-settings").addEventListener("click", async () => {
  if (!AppState.currentChat) return;
  const chat = AppState.currentChat;
  document.getElementById("settings-name").value = chat.name || "";
  document.getElementById("settings-prompt").value = chat.general_prompt || "";
  await loadOllamaModels();
  populateModelSelect(chat.model_name);
  document.getElementById("settings-history").value = chat.max_history_length || 30;
  // Load world locations from JSON array -> comma-separated string
  let locsStr = "";
  try {
    const locs = JSON.parse(chat.locations || "[]");
    if (Array.isArray(locs)) locsStr = locs.join(", ");
  } catch (_) { locsStr = chat.locations || ""; }
  document.getElementById("settings-locations").value = locsStr;
  setModeToggle("settings-mode", isThinkingMode(chat));
  document.getElementById("modal-settings").classList.remove("hidden");
  switchTab("general");
  renderCharactersTab();
  renderMemoriesTab();
  renderSceneTab();
  renderRelationshipsTab();
});

document.getElementById("btn-settings-close").addEventListener("click", () => {
  document.getElementById("modal-settings").classList.add("hidden");
});

document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    switchTab(tab.dataset.tab);
  });
});

function switchTab(tabId) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(t => t.classList.remove("active"));
  document.querySelector(`.tab[data-tab="${tabId}"]`).classList.add("active");
  document.getElementById(`tab-${tabId}`).classList.add("active");
}

document.getElementById("btn-save-settings").addEventListener("click", async () => {
  const chatId = AppState.currentChatId;
  const name = document.getElementById("settings-name").value.trim();
  const prompt = document.getElementById("settings-prompt").value.trim();
  const model = document.getElementById("settings-model").value;
  const history = parseInt(document.getElementById("settings-history").value) || 30;
  const thinking_mode = getModeToggle("settings-mode");
  // Convert comma-separated locations to JSON array
  const locsRaw = document.getElementById("settings-locations").value.trim();
  const locations = JSON.stringify(
    locsRaw.split(",").map(s => s.trim()).filter(s => s)
  );
  try {
    const updated = await apiRequest("PUT", `/chats/${chatId}`, {
      name,
      general_prompt: prompt,
      model_name: model,
      max_history_length: history,
      thinking_mode,
      locations,
    });
    AppState.currentChat = { ...AppState.currentChat, ...updated };
    document.getElementById("chat-title").textContent = updated.name;
    updateThinkingBadge();
    const idx = AppState.chats.findIndex(c => c.id === chatId);
    if (idx >= 0) AppState.chats[idx] = updated;
    renderChatList();
    showToast("Настройки сохранены", "success");
  } catch (e) {
    showToast("Ошибка: " + e.message);
  }
});

// ===== Characters Tab =====
async function renderCharactersTab() {
  const container = document.getElementById("character-list");
  if (!AppState.currentChatId) { container.innerHTML = ""; return; }
  try {
    const chars = await apiRequest("GET", `/chats/${AppState.currentChatId}/characters`);
    AppState.characters = chars;
    container.innerHTML = "";
    // Show player name as editable
    const allChars = await apiRequest("GET", `/chats/${AppState.currentChatId}/characters?include_player=true`);
    const player = allChars.find(c => c.is_player);
    if (player) {
      const info = document.createElement("div");
      info.className = "char-card player-info";
      info.innerHTML = `
        <div class="char-card-info player-name-edit">
          <span class="player-name-icon">👤</span>
          <input type="text" id="player-name-input" value="${escapeHtml(player.name)}" class="player-name-input">
          <span class="player-name-label">(игрок)</span>
          <button id="btn-save-player-name" class="btn btn-sm btn-primary">Сохранить</button>
        </div>`;
      container.appendChild(info);
      document.getElementById("btn-save-player-name").addEventListener("click", async () => {
        const newName = document.getElementById("player-name-input").value.trim();
        if (!newName) { showToast("Имя не может быть пустым"); return; }
        try {
          await apiRequest("PUT", `/chats/${AppState.currentChatId}/player`, { name: newName });
          showToast("Имя игрока обновлено", "success");
          renderCharactersTab();
        } catch (e) {
          showToast("Ошибка: " + e.message);
        }
      });
    }
    for (const c of chars) {
      const card = document.createElement("div");
      card.className = "char-card";
      card.innerHTML = `
        <div class="char-card-info">
          <div class="char-card-name">${escapeHtml(c.name)}</div>
          <div class="char-card-detail">${escapeHtml(c.speech_style || c.personality) || "Нет описания"} · Порядок: ${c.order_index}</div>
        </div>
        <div class="char-card-actions">
          <button class="char-order-btn" data-id="${c.id}" data-dir="up" title="Вверх">▲</button>
          <button class="char-order-btn" data-id="${c.id}" data-dir="down" title="Вниз">▼</button>
          <button class="btn btn-sm" data-id="${c.id}" data-action="edit" title="Редактировать">✏️</button>
          <button class="btn btn-sm btn-danger" data-id="${c.id}" data-action="delete" title="Удалить">✕</button>
        </div>`;
      container.appendChild(card);
    }
    container.querySelectorAll("[data-action='edit']").forEach(btn => {
      btn.addEventListener("click", () => openCharacterEditor(parseInt(btn.dataset.id)));
    });
    container.querySelectorAll("[data-action='delete']").forEach(btn => {
      btn.addEventListener("click", () => deleteCharacter(parseInt(btn.dataset.id)));
    });
    container.querySelectorAll("[data-dir]").forEach(btn => {
      btn.addEventListener("click", () => reorderCharacter(parseInt(btn.dataset.id), btn.dataset.dir));
    });
  } catch (e) {
    showToast("Ошибка загрузки персонажей: " + e.message);
  }
}

async function deleteCharacter(charId) {
  if (!confirm("Удалить персонажа?")) return;
  try {
    await apiRequest("DELETE", `/characters/${charId}`);
    showToast("Персонаж удалён", "success");
    renderCharactersTab();
  } catch (e) {
    showToast("Ошибка: " + e.message);
  }
}

async function reorderCharacter(charId, direction) {
  const char = AppState.characters.find(c => c.id === charId);
  if (!char) return;
  const siblings = [...AppState.characters].sort((a, b) => a.order_index - b.order_index);
  const idx = siblings.findIndex(c => c.id === charId);
  if (direction === "up" && idx > 0) {
    const swap = siblings[idx - 1];
    try {
      await apiRequest("PUT", `/characters/${charId}`, { order_index: swap.order_index });
      await apiRequest("PUT", `/characters/${swap.id}`, { order_index: char.order_index });
      showToast("Порядок изменён", "success");
      renderCharactersTab();
    } catch (e) {
      showToast("Ошибка: " + e.message);
    }
  } else if (direction === "down" && idx < siblings.length - 1) {
    const swap = siblings[idx + 1];
    try {
      await apiRequest("PUT", `/characters/${charId}`, { order_index: swap.order_index });
      await apiRequest("PUT", `/characters/${swap.id}`, { order_index: char.order_index });
      showToast("Порядок изменён", "success");
      renderCharactersTab();
    } catch (e) {
      showToast("Ошибка: " + e.message);
    }
  }
}

document.getElementById("btn-add-character").addEventListener("click", () => {
  openCharacterEditor(null);
});

// ===== Character Editor Modal =====
function clearCharacterEditorFields() {
  document.getElementById("char-editor-id").value = "";
  document.getElementById("char-editor-name").value = "";
  document.getElementById("char-editor-personality").value = "";
  document.getElementById("char-editor-traits").value = "";
  document.getElementById("char-editor-background").value = "";
  document.getElementById("char-editor-speech-style").value = "";
  document.getElementById("char-editor-examples").value = "";
  document.getElementById("char-editor-boundaries").value = "";
  document.getElementById("char-editor-temperature").value = "";
  document.getElementById("char-editor-order").value = AppState.characters.length;
  document.getElementById("char-editor-init-rels").innerHTML = "";
}

function readCharacterEditorPayload() {
  const tempRaw = document.getElementById("char-editor-temperature").value.trim();
  let temperature = null;
  if (tempRaw !== "") {
    const parsed = parseFloat(tempRaw);
    if (!Number.isNaN(parsed)) {
      temperature = parsed;
    }
  }

  const payload = {
    name: document.getElementById("char-editor-name").value.trim(),
    personality: document.getElementById("char-editor-personality").value.trim(),
    traits: document.getElementById("char-editor-traits").value.trim(),
    background: document.getElementById("char-editor-background").value.trim(),
    speech_style: document.getElementById("char-editor-speech-style").value.trim(),
    example_messages: document.getElementById("char-editor-examples").value.trim(),
    boundaries: document.getElementById("char-editor-boundaries").value.trim(),
    temperature,
    order_index: parseInt(document.getElementById("char-editor-order").value) || 0,
  };

  // Gather initial relationships (only those with a type selected)
  const initRels = [];
  document.querySelectorAll("#char-editor-init-rels .init-rel-row").forEach(row => {
    const targetId = parseInt(row.dataset.targetId);
    const type = row.querySelector(".init-rel-type").value;
    if (!type) return;  // skip unset
    const affection = parseInt(row.querySelector("[data-metric='affection']").value) || 50;
    const trust = parseInt(row.querySelector("[data-metric='trust']").value) || 50;
    const attraction = parseInt(row.querySelector("[data-metric='attraction']").value) || 0;
    const resentment = parseInt(row.querySelector("[data-metric='resentment']").value) || 0;
    const jealousy = parseInt(row.querySelector("[data-metric='jealousy']").value) || 0;
    initRels.push({ target_id: targetId, relationship_type: type, affection, trust, attraction, resentment, jealousy, description: "" });
  });
  if (initRels.length) payload.initial_relationships = initRels;

  return payload;
}

async function openCharacterEditor(charId) {
  document.getElementById("modal-character").classList.remove("hidden");
  const container = document.getElementById("char-editor-init-rels");
  if (charId) {
    const char = AppState.characters.find(c => c.id === charId);
    if (!char) return;
    document.getElementById("char-editor-title").textContent = "Редактировать персонажа";
    document.getElementById("char-editor-id").value = char.id;
    document.getElementById("char-editor-name").value = char.name;
    document.getElementById("char-editor-personality").value = char.personality || "";
    document.getElementById("char-editor-traits").value = char.traits || "";
    document.getElementById("char-editor-background").value = char.background || "";
    document.getElementById("char-editor-speech-style").value = char.speech_style || "";
    document.getElementById("char-editor-examples").value = char.example_messages || "";
    document.getElementById("char-editor-boundaries").value = char.boundaries || "";
    document.getElementById("char-editor-temperature").value =
      char.temperature != null ? char.temperature : "";
    document.getElementById("char-editor-order").value = char.order_index;

    // Load existing outgoing relationships for this character
    try {
      const rels = await apiRequest("GET", `/chats/${AppState.currentChatId}/characters/${charId}/relationships`);
      const allChars = await apiRequest("GET", `/chats/${AppState.currentChatId}/characters?include_player=true`);
      container.innerHTML = "";
      for (const rel of rels) {
        const target = allChars.find(c => c.id === rel.target_character_id);
        if (!target) continue;
        const isPlayer = target.is_player ? " 👤" : "";
        const row = document.createElement("div");
        row.className = "init-rel-row";
        row.dataset.targetId = rel.target_character_id;
        row.innerHTML = `
          <span class="init-rel-target">${escapeHtml(target.name)}${isPlayer}</span>
          <select class="init-rel-type">${RELATIONSHIP_TYPES.map(t =>
            `<option value="${t}"${t === rel.relationship_type ? " selected" : ""}>${relTypeLabel(t)}</option>`
          ).join("")}</select>
          <div class="init-rel-metrics">
            ${["affection","trust","attraction","resentment","jealousy"].map(m =>
              `<label>${m[0].toUpperCase()}<input type="number" class="init-rel-metric" data-metric="${m}"
                min="0" max="100" value="${rel[m]}"></label>`
            ).join("")}
          </div>`;
        container.appendChild(row);
      }
    } catch (e) {
      container.innerHTML = `<p class="field-hint">Ошибка загрузки отношений: ${escapeHtml(e.message)}</p>`;
    }
  } else {
    document.getElementById("char-editor-title").textContent = "Новый персонаж";
    clearCharacterEditorFields();
    // Populate with all existing NPCs + player as potential targets
    try {
      const allChars = await apiRequest("GET", `/chats/${AppState.currentChatId}/characters?include_player=true`);
      container.innerHTML = "";
      for (const target of allChars) {
        const isPlayer = target.is_player ? " 👤" : "";
        const row = document.createElement("div");
        row.className = "init-rel-row";
        row.dataset.targetId = target.id;
        row.innerHTML = `
          <span class="init-rel-target">${escapeHtml(target.name)}${isPlayer}</span>
          <select class="init-rel-type">
            <option value="">— не задано —</option>
            ${RELATIONSHIP_TYPES.map(t => `<option value="${t}">${relTypeLabel(t)}</option>`).join("")}
          </select>
          <div class="init-rel-metrics">
            ${["affection","trust","attraction","resentment","jealousy"].map(m =>
              `<label>${m[0].toUpperCase()}<input type="number" class="init-rel-metric" data-metric="${m}"
                min="0" max="100" value="50"></label>`
            ).join("")}
          </div>`;
        container.appendChild(row);
      }
    } catch (e) {
      container.innerHTML = `<p class="field-hint">Ошибка: ${escapeHtml(e.message)}</p>`;
    }
  }
}

document.getElementById("btn-char-cancel").addEventListener("click", () => {
  document.getElementById("modal-character").classList.add("hidden");
});

document.getElementById("btn-char-save").addEventListener("click", async () => {
  const charId = document.getElementById("char-editor-id").value;
  const payload = readCharacterEditorPayload();
  if (!payload.name) { showToast("Введите имя персонажа"); return; }

  try {
    if (charId) {
      // Update character only (relationships edited via relationships tab)
      const { initial_relationships, ...charUpdate } = payload;
      await apiRequest("PUT", `/characters/${charId}`, charUpdate);
      showToast("Персонаж обновлён", "success");
    } else {
      await apiRequest("POST", `/chats/${AppState.currentChatId}/characters`, payload);
      showToast("Персонаж добавлен", "success");
    }
    document.getElementById("modal-character").classList.add("hidden");
    renderCharactersTab();
  } catch (e) {
    showToast("Ошибка: " + e.message);
  }
});

// ===== Memories Tab =====
let memoriesFilterCategory = "all";
let memoriesData = {}; // { characterId: memories[] }

async function renderMemoriesTab() {
  const container = document.getElementById("memories-list");
  container.innerHTML = "";
  if (!AppState.currentChatId || !AppState.characters.length) {
    container.innerHTML = "<p style='color:var(--text-muted);padding:8px;'>Нет персонажей</p>";
    return;
  }

  // Category filter dropdown
  const filterWrapper = document.createElement("div");
  filterWrapper.style.cssText = "margin-bottom:12px;";
  filterWrapper.innerHTML = `
    <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text-secondary);">
      Фильтр категории:
      <select id="memories-category-filter" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--bg-secondary);color:var(--text);font-size:13px;">
        <option value="all">Все</option>
        <option value="событие">Событие</option>
        <option value="отношения">Отношения</option>
        <option value="локация">Локация</option>
        <option value="предмет">Предмет</option>
        <option value="другое">Другое</option>
      </select>
    </label>
  `;
  container.appendChild(filterWrapper);
  document.getElementById("memories-category-filter").value = memoriesFilterCategory;
  document.getElementById("memories-category-filter").addEventListener("change", (e) => {
    memoriesFilterCategory = e.target.value;
    renderMemoriesFromData();
  });

  // Fetch all memories
  let allMemories = [];
  for (const char of AppState.characters) {
    try {
      const mems = await apiRequest("GET", `/characters/${char.id}/memories`);
      if (mems && mems.length) {
        memoriesData[char.id] = mems;
        allMemories.push(...mems.map(m => ({ ...m, characterName: char.name, characterId: char.id })));
      } else {
        memoriesData[char.id] = [];
      }
    } catch (_) {
      memoriesData[char.id] = [];
    }
  }

  // Character tabs
  const tabsWrapper = document.createElement("div");
  tabsWrapper.style.cssText = "display:flex;gap:4px;margin-bottom:8px;flex-wrap:wrap;";
  for (const char of AppState.characters) {
    const mems = memoriesData[char.id] || [];
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "tab" + (char.id === Object.keys(memoriesData)[0] ? " active" : "");
    tab.style.cssText = "padding:4px 10px;font-size:12px;border-radius:4px;border:1px solid var(--border);background:var(--bg-secondary);color:var(--text);cursor:pointer;";
    tab.textContent = `${char.name} (${mems.length})`;
    tab.dataset.charId = char.id;
    tab.addEventListener("click", () => switchMemoryTab(char.id));
    tabsWrapper.appendChild(tab);
  }
  container.appendChild(tabsWrapper);

  // Memory list container
  const listContainer = document.createElement("div");
  listContainer.id = "memories-list-inner";
  container.appendChild(listContainer);

  renderMemoriesFromData();
}

function switchMemoryTab(characterId) {
  document.querySelectorAll("#memories-list .tab").forEach(t => t.classList.remove("active"));
  const activeTab = document.querySelector(`#memories-list .tab[data-char-id="${characterId}"]`);
  if (activeTab) activeTab.classList.add("active");
  renderMemoriesFromData();
}

function getImportanceClass(importance) {
  if (importance >= 0.7) return "badge-green";
  if (importance >= 0.4) return "badge-yellow";
  return "badge-red";
}

function renderMemoriesFromData() {
  const container = document.getElementById("memories-list-inner");
  if (!container) return;
  container.innerHTML = "";

  // Determine active character tab
  const activeTab = document.querySelector("#memories-list .tab.active");
  const activeCharId = activeTab ? parseInt(activeTab.dataset.charId) : (AppState.characters[0]?.id);
  if (!activeCharId) return;

  const mems = memoriesData[activeCharId] || [];
  const char = AppState.characters.find(c => c.id === activeCharId);
  if (!char) return;

  // Add memory button
  const addBtn = document.createElement("button");
  addBtn.className = "btn btn-primary btn-sm";
  addBtn.style.cssText = "margin-bottom:8px;";
  addBtn.textContent = "+ Добавить воспоминание";
  addBtn.addEventListener("click", () => openMemoryEditor(null, activeCharId));
  container.appendChild(addBtn);

  // Filter memories by category
  const filtered = memoriesFilterCategory === "all"
    ? mems
    : mems.filter(m => (m.category || "событие") === memoriesFilterCategory);

  if (!filtered.length) {
    const p = document.createElement("p");
    p.style.cssText = "color:var(--text-muted);padding:8px;font-size:13px;";
    p.textContent = memoriesFilterCategory === "all" ? "Нет воспоминаний" : `Нет воспоминаний категории "${memoriesFilterCategory}"`;
    container.appendChild(p);
    return;
  }

  for (const mem of filtered) {
    const importance = mem.importance ?? 0.5;
    const category = mem.category || "событие";
    const importanceClass = getImportanceClass(importance);

    const item = document.createElement("div");
    item.className = "memory-item";
    item.style.cssText = "display:flex;align-items:flex-start;gap:8px;padding:8px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px;background:var(--bg-secondary);";
    item.innerHTML = `
      <div style="flex:1;min-width:0;">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;flex-wrap:wrap;">
          <span class="memory-badge ${importanceClass}" style="font-size:10px;padding:2px 6px;border-radius:999px;font-weight:600;">${(importance * 100).toFixed(0)}%</span>
          <span class="memory-badge badge-category" style="font-size:10px;padding:2px 6px;border-radius:999px;background:var(--accent-blue);color:white;">${escapeHtml(memoryCategoryLabel(category))}</span>
        </div>
        <div class="memory-item-text" style="font-size:13px;line-height:1.5;word-break:break-word;">${escapeHtml(mem.content)}</div>
      </div>
      <div style="display:flex;gap:4px;flex-shrink:0;">
        <button class="btn btn-sm mem-edit-btn" data-mem-id="${mem.id}" title="Редактировать" style="padding:4px 8px;">✏️</button>
        <button class="btn btn-sm btn-danger mem-delete-btn" data-mem-id="${mem.id}" title="Удалить" style="padding:4px 8px;">✕</button>
      </div>
    `;
    container.appendChild(item);

    item.querySelector(".mem-edit-btn").addEventListener("click", () => openMemoryEditor(mem.id, activeCharId));
    item.querySelector(".mem-delete-btn").addEventListener("click", async () => {
      try {
        await apiRequest("DELETE", `/memories/${mem.id}`);
        showToast("Воспоминание удалено", "success");
        // Refresh from server
        const fresh = await apiRequest("GET", `/characters/${activeCharId}/memories`);
        memoriesData[activeCharId] = fresh || [];
        renderMemoriesFromData();
        // Update tab count
        const tabBtn = document.querySelector(`#memories-list .tab[data-char-id="${activeCharId}"]`);
        if (tabBtn) tabBtn.textContent = `${char.name} (${memoriesData[activeCharId].length})`;
      } catch (e) {
        showToast("Ошибка: " + e.message);
      }
    });
  }
}

function openMemoryEditor(memoryId, characterId) {
  const modal = document.getElementById("modal-memory");
  const title = document.getElementById("mem-editor-title");
  const idInput = document.getElementById("mem-editor-id");
  const charIdInput = document.getElementById("mem-editor-character-id");
  const contentInput = document.getElementById("mem-editor-content");
  const categoryInput = document.getElementById("mem-editor-category");
  const importanceInput = document.getElementById("mem-editor-importance");

  idInput.value = memoryId || "";
  charIdInput.value = characterId;
  contentInput.value = "";
  categoryInput.value = "событие";
  importanceInput.value = "0.5";

  if (memoryId) {
    title.textContent = "Редактировать воспоминание";
    const mem = memoriesData[characterId]?.find(m => m.id === memoryId);
    if (mem) {
      contentInput.value = mem.content;
      categoryInput.value = mem.category || "событие";
      importanceInput.value = String(mem.importance ?? 0.5);
    }
  } else {
    title.textContent = "Новое воспоминание";
  }

  modal.classList.remove("hidden");
  contentInput.focus();
}

async function saveMemory() {
  const memoryId = document.getElementById("mem-editor-id").value;
  const characterId = parseInt(document.getElementById("mem-editor-character-id").value);
  const content = document.getElementById("mem-editor-content").value.trim();
  const category = document.getElementById("mem-editor-category").value;
  const importance = parseFloat(document.getElementById("mem-editor-importance").value);

  if (!content) { showToast("Введите содержимое"); return; }
  if (!characterId) { showToast("Персонаж не выбран"); return; }

  try {
    if (memoryId) {
      await apiRequest("PUT", `/memories/${memoryId}`, { content, importance, category });
      showToast("Воспоминание обновлено", "success");
    } else {
      await apiRequest("POST", `/characters/${characterId}/memories`, { chat_id: AppState.currentChatId, character_id: characterId, content, importance, category });
      showToast("Воспоминание добавлено", "success");
    }
    document.getElementById("modal-memory").classList.add("hidden");
    // Refresh
    const fresh = await apiRequest("GET", `/characters/${characterId}/memories`);
    memoriesData[characterId] = fresh || [];
    renderMemoriesFromData();
    // Update tab count
    const char = AppState.characters.find(c => c.id === characterId);
    if (char) {
      const tabBtn = document.querySelector(`#memories-list .tab[data-char-id="${characterId}"]`);
      if (tabBtn) tabBtn.textContent = `${char.name} (${memoriesData[characterId].length})`;
    }
  } catch (e) {
    showToast("Ошибка: " + e.message);
  }
}

function deleteMemory(memoryId) {
  // This is now handled inline in renderMemoriesFromData
}

// ===== Scene Tracker Tab (P3) =====
async function renderSceneTab() {
  if (!AppState.currentChatId) return;
  try {
    const [scene, characters] = await Promise.all([
      apiRequest("GET", `/chats/${AppState.currentChatId}/scene`),
      apiRequest("GET", `/chats/${AppState.currentChatId}/characters`),
    ]);
    // Fill form fields
    document.getElementById("scene-time").value = scene.time_of_day || "";
    document.getElementById("scene-weather").value = scene.custom_state?.weather || "";
    document.getElementById("scene-mood").value = scene.custom_state?.mood || "";
    document.getElementById("scene-tension").value = scene.custom_state?.tension ?? 0;
    document.getElementById("scene-tension-value").textContent = (scene.custom_state?.tension ?? 0).toFixed(1);
    document.getElementById("scene-goal").value = scene.custom_state?.active_goal || "";
    document.getElementById("scene-objects").value = (scene.custom_state?.important_objects || []).join(", ");
    document.getElementById("scene-events").value = (scene.custom_state?.active_events || []).join(", ");
    document.getElementById("scene-time-progression").value = scene.custom_state?.time_progression || "";
    document.getElementById("scene-plot-flags").value = (scene.custom_state?.plot_flags || []).join(", ");

    // Player location
    document.getElementById("scene-player-location").value = scene.player_location || "";

    // Per-character location inputs
    const container = document.getElementById("scene-char-locations");
    container.innerHTML = "";
    for (const c of characters) {
      const row = document.createElement("div");
      row.className = "char-location-row";
      row.innerHTML = `
        <span class="char-location-name">${escapeHtml(c.name)}</span>
        <input type="text" class="char-location-input" data-char-id="${c.id}"
               value="${escapeHtml(c.location || '')}"
               placeholder="Локация (пусто = общая)">
      `;
      container.appendChild(row);
    }

    // Present characters (read-only)
    const presentNames = (scene.present_character_ids || []).map(id => {
      const char = characters.find(c => c.id === id);
      return char ? char.name : `ID:${id}`;
    });
    document.getElementById("scene-present-characters").textContent = presentNames.length
      ? presentNames.join(", ")
      : "(нет персонажей в сцене)";
  } catch (e) {
    console.warn("Failed to load scene state:", e);
    // Reset form on error
    resetSceneForm();
  }
}

function resetSceneForm() {
  document.getElementById("scene-time").value = "";
  document.getElementById("scene-weather").value = "";
  document.getElementById("scene-mood").value = "";
  document.getElementById("scene-tension").value = 0;
  document.getElementById("scene-tension-value").textContent = "0.0";
  document.getElementById("scene-goal").value = "";
  document.getElementById("scene-objects").value = "";
  document.getElementById("scene-events").value = "";
  document.getElementById("scene-time-progression").value = "";
  document.getElementById("scene-plot-flags").value = "";
  document.getElementById("scene-char-locations").innerHTML = "";
  document.getElementById("scene-player-location").value = "";
  document.getElementById("scene-present-characters").textContent = "(ошибка загрузки)";
}

// Tension slider update
document.getElementById("scene-tension").addEventListener("input", (e) => {
  document.getElementById("scene-tension-value").textContent = parseFloat(e.target.value).toFixed(1);
});

// Save scene state
document.getElementById("btn-save-scene").addEventListener("click", async () => {
  if (!AppState.currentChatId) return;
  const tension = parseFloat(document.getElementById("scene-tension").value) || 0;
  const custom_state = {
    weather: document.getElementById("scene-weather").value.trim(),
    mood: document.getElementById("scene-mood").value.trim(),
    tension: tension,
    plot_flags: document.getElementById("scene-plot-flags").value.split(",").map(s => s.trim()).filter(s => s),
    active_goal: document.getElementById("scene-goal").value.trim(),
    important_objects: document.getElementById("scene-objects").value.split(",").map(s => s.trim()).filter(s => s),
    active_events: document.getElementById("scene-events").value.split(",").map(s => s.trim()).filter(s => s),
    time_progression: document.getElementById("scene-time-progression").value.trim(),
  };
  try {
    // Save per-character locations + build name-keyed dict for scene state
    const locationInputs = document.querySelectorAll("#scene-char-locations .char-location-input");
    const charLocations = {};
    for (const input of locationInputs) {
      const charId = parseInt(input.dataset.charId);
      const loc = input.value.trim();
      await apiRequest("PATCH", `/characters/${charId}/location`, { location: loc });
      const nameSpan = input.closest(".char-location-row")?.querySelector(".char-location-name");
      if (nameSpan) {
        charLocations[nameSpan.textContent.trim()] = loc;
      }
    }
    // Save player location
    const playerLoc = document.getElementById("scene-player-location").value.trim();
    await apiRequest("PUT", `/chats/${AppState.currentChatId}`, {
      player_location: playerLoc,
    });
    // Save scene state (including character locations for prompt builder)
    await apiRequest("PATCH", `/chats/${AppState.currentChatId}/scene`, {
      time_of_day: document.getElementById("scene-time").value.trim(),
      character_locations: charLocations,
      custom_state,
    });
    showToast("Состояние сцены сохранено", "success");
  } catch (e) {
    showToast("Ошибка сохранения: " + e.message);
  }
});

// Refresh scene state from server
document.getElementById("btn-refresh-scene").addEventListener("click", renderSceneTab);

// ===== Relationships Tab =====
const RELATIONSHIP_TYPES = [
  "нейтральное", "друг", "близкий_друг", "лучший_друг",
  "союзник", "верный_союзник",
  "соперник", "враг", "заклятый_враг",
  "симпатия", "романтика", "возлюбленные",
  "наставник", "ученик",
  "семья", "родитель", "брат_сестра",
  "незнакомец", "знакомый",
];

const RELATIONSHIP_TYPE_LABELS = {
  "нейтральное": "Нейтральные",
  "друг": "Друг",
  "близкий_друг": "Близкий друг",
  "лучший_друг": "Лучший друг",
  "союзник": "Союзник",
  "верный_союзник": "Верный союзник",
  "соперник": "Соперник",
  "враг": "Враг",
  "заклятый_враг": "Заклятый враг",
  "симпатия": "Симпатия",
  "романтика": "Романтика",
  "возлюбленные": "Возлюбленные",
  "наставник": "Наставник",
  "ученик": "Ученик",
  "семья": "Семья",
  "родитель": "Родитель",
  "брат_сестра": "Брат/сестра",
  "незнакомец": "Незнакомец",
  "знакомый": "Знакомый",
};

const MEMORY_CATEGORY_LABELS = {
  "отношения": "Отношения",
  "событие": "Событие",
  "локация": "Локация",
  "предмет": "Предмет",
  "другое": "Другое",
};

function relTypeLabel(t) { return RELATIONSHIP_TYPE_LABELS[t] || t; }
function memoryCategoryLabel(c) { return MEMORY_CATEGORY_LABELS[c] || c; }

const METRIC_LABELS = {
  affection: "Привязанность",
  trust: "Доверие",
  attraction: "Влечение",
  resentment: "Обида",
  jealousy: "Ревность",
};

function createRelTypeDropdown(currentType) {
  let html = `<select class="rel-type-select">`;
  for (const t of RELATIONSHIP_TYPES) {
    const sel = t === currentType ? " selected" : "";
    html += `<option value="${t}"${sel}>${relTypeLabel(t)}</option>`;
  }
  html += `</select>`;
  return html;
}

function createMetricSlider(metric, value) {
  const label = METRIC_LABELS[metric] || metric;
  return `<div class="rel-metric-row">
    <span class="rel-metric-label">${label}</span>
    <input type="range" class="rel-metric-slider" data-metric="${metric}"
      min="0" max="100" value="${value}">
    <span class="rel-metric-value" id="rel-val-${metric}">${value}</span>
  </div>`;
}

function bindRelSliders(scope) {
  scope.querySelectorAll(".rel-metric-slider").forEach(slider => {
    slider.addEventListener("input", () => {
      const valSpan = slider.closest(".rel-metric-row").querySelector(".rel-metric-value");
      if (valSpan) valSpan.textContent = slider.value;
    });
  });
}

function bindRelSave(scope) {
  scope.querySelectorAll(".rel-save-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const item = btn.closest(".rel-editable");
      const sourceId = parseInt(item.dataset.source);
      const targetId = parseInt(item.dataset.target);
      const type = item.querySelector(".rel-type-select").value;
      const metrics = {};
      item.querySelectorAll(".rel-metric-slider").forEach(sl => {
        metrics[sl.dataset.metric] = parseInt(sl.value);
      });
      const descEl = item.querySelector(".rel-desc-edit");
      const description = descEl ? descEl.value.trim() : undefined;
      try {
        await apiRequest("PUT", `/chats/${AppState.currentChatId}/relationships/${sourceId}/${targetId}`, {
          relationship_type: type,
          affection: metrics.affection,
          trust: metrics.trust,
          attraction: metrics.attraction,
          resentment: metrics.resentment,
          jealousy: metrics.jealousy,
          description,
        });
        showToast("Отношение обновлено", "success");
      } catch (e) {
        showToast("Ошибка: " + e.message);
      }
    });
  });
}

// Shared "manual overrides" list renderer (used by the settings tab and the
// relationships modal). Supports metric sliders, type dropdown, editable
// description, "add relationship" and a link to the pair timeline.
function renderRelationshipList(container) {
  if (!AppState.currentChatId) { container.innerHTML = ""; return Promise.resolve(); }
  container.innerHTML = `<p class="field-hint">Загрузка…</p>`;
  return (async () => {
    try {
      const chars = await apiRequest("GET", `/chats/${AppState.currentChatId}/characters?include_player=true`);
      const npcs = chars.filter(c => !c.is_player);
      container.innerHTML = "";
      if (!npcs.length) {
        container.innerHTML = `<p class="field-hint">Нет персонажей.</p>`;
        return;
      }

      // "Add relationship" form
      const addForm = document.createElement("div");
      addForm.className = "rel-add-form";
      addForm.innerHTML = `
        <span class="field-hint" style="margin:0">Добавить отношение:</span>
        <select class="rel-add-source">
          ${npcs.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("")}
        </select>
        <span>→</span>
        <select class="rel-add-target">
          ${chars.map(c => `<option value="${c.id}">${escapeHtml(c.name)}${c.is_player ? " (игрок)" : ""}</option>`).join("")}
        </select>
        <button class="btn btn-sm btn-primary rel-add-btn">Добавить</button>`;
      addForm.querySelector(".rel-add-btn").addEventListener("click", async () => {
        const s = parseInt(addForm.querySelector(".rel-add-source").value);
        const t = parseInt(addForm.querySelector(".rel-add-target").value);
        if (s === t) { showToast("Нельзя создать отношение персонажа к самому себе"); return; }
        try {
          await apiRequest("PUT", `/chats/${AppState.currentChatId}/relationships/${s}/${t}`, {
            relationship_type: "нейтральное",
            affection: 50, trust: 50, attraction: 0, resentment: 0, jealousy: 0,
            description: "",
          });
          showToast("Отношение создано", "success");
          renderRelationshipList(container);
        } catch (e) { showToast("Ошибка: " + e.message); }
      });
      container.appendChild(addForm);

      for (const source of npcs) {
        const rels = await apiRequest("GET", `/chats/${AppState.currentChatId}/characters/${source.id}/relationships`);
        if (!rels.length) continue;
        const section = document.createElement("div");
        section.className = "rel-section";
        let html = `<h4>${escapeHtml(source.name)} →</h4><div class="rel-list">`;
        for (const r of rels) {
          const target = chars.find(c => c.id === r.target_character_id);
          const targetName = target?.name || `ID:${r.target_character_id}`;
          const targetLabel = target?.is_player ? `👤 ${targetName}` : targetName;
          html += `<div class="rel-item rel-editable" data-rel-id="${r.id}"
            data-source="${source.id}" data-target="${r.target_character_id}">
            <div class="rel-header">
              <span class="rel-target">${escapeHtml(targetLabel)}</span>
              ${createRelTypeDropdown(r.relationship_type)}
              <button class="btn btn-sm btn-primary rel-save-btn" title="Сохранить">💾</button>
              <button class="btn btn-sm rel-timeline-btn" title="Таймлайн">🕘</button>
            </div>
            <div class="rel-metrics-grid">
              ${createMetricSlider("affection", r.affection)}
              ${createMetricSlider("trust", r.trust)}
              ${createMetricSlider("attraction", r.attraction)}
              ${createMetricSlider("resentment", r.resentment)}
              ${createMetricSlider("jealousy", r.jealousy)}
            </div>
            <textarea class="rel-desc-edit" placeholder="Описание">${escapeHtml(r.description || "")}</textarea>
          </div>`;
        }
        html += `</div>`;
        section.innerHTML = html;
        bindRelSliders(section);
        bindRelSave(section);
        section.querySelectorAll(".rel-timeline-btn").forEach(btn => {
          btn.addEventListener("click", () => {
            const item = btn.closest(".rel-editable");
            openRelDetail(parseInt(item.dataset.source), parseInt(item.dataset.target));
          });
        });
        container.appendChild(section);
      }
    } catch (e) {
      container.innerHTML = `<p class="field-hint">Ошибка загрузки отношений: ${escapeHtml(e.message)}</p>`;
    }
  })();
}

function renderRelationshipsTab() {
  return renderRelationshipList(document.getElementById("relationships-view"));
}
document.getElementById("btn-refresh-relationships").addEventListener("click", renderRelationshipsTab);

// ===== Send button + Enter + Stop =====
document.getElementById("btn-send").addEventListener("click", sendMessage);
document.getElementById("btn-stop").addEventListener("click", stopGeneration);
document.getElementById("message-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// ===== Clear history button =====
document.getElementById("btn-clear-history")?.addEventListener("click", showClearHistoryModal);

// ===== Thinking mode toggle =====
bindModeToggle("new-chat-mode");
bindModeToggle("settings-mode");
document.getElementById("btn-thinking-toggle")?.addEventListener("click", toggleThinkingMode);

// ===== Memory Modal =====
document.getElementById("btn-mem-cancel")?.addEventListener("click", () => {
  document.getElementById("modal-memory").classList.add("hidden");
});
document.getElementById("btn-mem-save")?.addEventListener("click", saveMemory);

// ===== Beforeunload — сохраняем состояние генерации =====
window.addEventListener("beforeunload", () => {
  if (AppState.sendingChatIds.size > 0 && AppState.currentChatId) {
    saveGenerationState(AppState.currentChatId);
  }
});

// ===== Relationships modal (Sprint 4: graph / timeline / issues / overrides) =====

const ISSUE_TYPE_LABELS = {
  broken_promise: "Невыполненное обещание",
  debt: "Долг",
  unfulfilled_request: "Невыполненная просьба",
  lie: "Ложь",
  unresolved_conflict: "Нерешённый конфликт",
  suspicion: "Подозрение",
  hidden_secret: "Скрытая тайна",
  missing_apology: "Нет извинений",
  unreturned_favor: "Неотвеченная услуга",
  emotional_grievance: "Обида",
};
function issueTypeLabel(t) { return ISSUE_TYPE_LABELS[t] || t; }

const REL_KIND_LABELS = {
  llm: "LLM",
  decay: "Затухание",
  manual: "Вручную",
  archive: "Архив",
};
function relKindLabel(k) { return REL_KIND_LABELS[k] || k; }

const relGraphState = {
  data: null,          // { characters, edges }
  selectedEdge: null,
  selectedChar: null,
  drag: null,          // { charId }
};

const relDetailState = {
  sourceId: null,
  targetId: null,
  timelineOffset: 0,
  timelineTotal: 0,
};

let relActiveTab = "rel-graph";

function openRelModal() {
  if (!AppState.currentChatId) { showToast("Выберите чат"); return; }
  document.getElementById("modal-relationships").classList.remove("hidden");
  switchRelTab(relActiveTab);
}

function closeRelModal() {
  document.getElementById("modal-relationships").classList.add("hidden");
  closeRelDetail();
}

function switchRelTab(tabId) {
  relActiveTab = tabId;
  document.querySelectorAll("#modal-relationships .rel-tabs .tab").forEach(t => {
    t.classList.toggle("active", t.dataset.rtab === tabId);
  });
  document.querySelectorAll("#modal-relationships .tab-content").forEach(t => t.classList.remove("active"));
  const target = document.getElementById(`rtab-${tabId}`);
  if (target) target.classList.add("active");
  document.getElementById("rel-detail-panel").classList.add("hidden");
  if (tabId === "rel-graph") { renderRelationshipGraph(); return Promise.resolve(); }
  if (tabId === "rel-list") return renderRelationshipList(document.getElementById("rel-list-view"));
  if (tabId === "rel-issues") { renderOpenIssues(); return Promise.resolve(); }
  return Promise.resolve();
}

document.getElementById("btn-relationships").addEventListener("click", openRelModal);
document.getElementById("btn-relationships-close").addEventListener("click", closeRelModal);
document.querySelectorAll("#modal-relationships .rel-tabs .tab").forEach(tab => {
  tab.addEventListener("click", () => switchRelTab(tab.dataset.rtab));
});
document.getElementById("btn-refresh-rel-graph").addEventListener("click", renderRelationshipGraph);
document.getElementById("btn-refresh-rel-list").addEventListener("click", () =>
  renderRelationshipList(document.getElementById("rel-list-view")));
document.getElementById("btn-refresh-rel-issues").addEventListener("click", renderOpenIssues);

// ---- Edge / metric styling ----
function relEdgeClass(r) {
  const neg = (r.resentment + r.jealousy) - (r.affection + r.trust);
  if (r.attraction >= 60) return "edge-rom";
  if (neg >= 40) return "edge-neg";
  if (r.affection + r.trust >= 120) return "edge-pos";
  return "edge-neu";
}
function relMetricClass(r, metric) {
  if (metric === "resentment" || metric === "jealousy") {
    return r[metric] >= 50 ? "mb-neg" : (r[metric] >= 25 ? "mb-neu" : "mb-pos");
  }
  if (metric === "attraction") return r[metric] >= 50 ? "mb-rom" : "mb-neu";
  return r[metric] >= 60 ? "mb-pos" : (r[metric] >= 35 ? "mb-neu" : "mb-neg");
}

// ---- Graph (vanilla SVG) ----
const REL_NODE_R = 26;
const REL_GRAPH_W = 720;
const REL_GRAPH_H = 460;

function relCircularLayout(count) {
  const cx = REL_GRAPH_W / 2, cy = REL_GRAPH_H / 2;
  const r = Math.min(REL_GRAPH_W, REL_GRAPH_H) / 2 - 70;
  const pts = [];
  for (let i = 0; i < count; i++) {
    const angle = (2 * Math.PI * i) / count - Math.PI / 2;
    pts.push({ x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) });
  }
  return pts;
}

function relArrow(tip, angle, ux, uy) {
  const nx = -uy, ny = ux;
  const p1 = { x: tip.x - ux * 14 + nx * 5, y: tip.y - uy * 14 + ny * 5 };
  const p2 = { x: tip.x - ux * 14 - nx * 5, y: tip.y - uy * 14 - ny * 5 };
  return `${tip.x.toFixed(1)},${tip.y.toFixed(1)} ${p1.x.toFixed(1)},${p1.y.toFixed(1)} ${p2.x.toFixed(1)},${p2.y.toFixed(1)}`;
}

function relStraightGeometry(s, t) {
  const dx = t.x - s.x, dy = t.y - s.y;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  return {
    x1: s.x + ux * REL_NODE_R,
    y1: s.y + uy * REL_NODE_R,
    x2: t.x - ux * REL_NODE_R,
    y2: t.y - uy * REL_NODE_R,
    tip: { x: t.x - ux * REL_NODE_R, y: t.y - uy * REL_NODE_R },
    angle: Math.atan2(dy, dx),
    ux, uy,
    labelX: (s.x + t.x) / 2 + ux * 10,
    labelY: (s.y + t.y) / 2 + uy * 10,
  };
}

function relQuadPoint(p0, c, p1, t) {
  const u = 1 - t;
  return {
    x: u * u * p0.x + 2 * u * t * c.x + t * t * p1.x,
    y: u * u * p0.y + 2 * u * t * c.y + t * t * p1.y,
  };
}

function relCurvedGeometry(s, t, dir) {
  const mx = (s.x + t.x) / 2, my = (s.y + t.y) / 2;
  let nx = -(t.y - s.y), ny = t.x - s.x;
  const nlen = Math.hypot(nx, ny) || 1;
  nx /= nlen; ny /= nlen;
  const off = 26;
  const c = { x: mx + nx * off * dir, y: my + ny * off * dir };
  const tip = relQuadPoint(s, c, t, 0.94);
  const just = relQuadPoint(s, c, t, 0.90);
  const ux = tip.x - just.x, uy = tip.y - just.y;
  const ulen = Math.hypot(ux, uy) || 1;
  return {
    d: `M ${s.x.toFixed(1)} ${s.y.toFixed(1)} Q ${c.x.toFixed(1)} ${c.y.toFixed(1)} ${t.x.toFixed(1)} ${t.y.toFixed(1)}`,
    tip,
    angle: Math.atan2(uy, ux),
    ux: ux / ulen, uy: uy / ulen,
    labelX: c.x + nx * 10, labelY: c.y + ny * 10,
  };
}

function renderRelationshipGraph() {
  const container = document.getElementById("rel-graph-view");
  if (!AppState.currentChatId) { container.innerHTML = ""; return; }
  container.innerHTML = `<p class="field-hint">Загрузка…</p>`;
  apiRequest("GET", `/chats/${AppState.currentChatId}/relationships/graph`)
    .then(data => { relGraphState.data = data; drawRelGraph(container); })
    .catch(e => { container.innerHTML = `<p class="field-hint">Ошибка: ${escapeHtml(e.message)}</p>`; });
}

function drawRelGraph(container) {
  const { characters, edges } = relGraphState.data || { characters: [], edges: [] };
  if (!characters.length) { container.innerHTML = `<p class="field-hint">Нет персонажей.</p>`; return; }
  const pos = relCircularLayout(characters.length);
  const byId = {};
  characters.forEach((c, i) => { c._pos = c._pos || pos[i]; byId[c.id] = c; });

  let svg = `<svg class="rel-graph-svg" viewBox="0 0 ${REL_GRAPH_W} ${REL_GRAPH_H}" xmlns="http://www.w3.org/2000/svg">`;

  for (const e of edges) {
    const s = byId[e.source_character_id], t = byId[e.target_character_id];
    if (!s || !t) continue;
    const rev = edges.find(x =>
      x.source_character_id === e.target_character_id &&
      x.target_character_id === e.source_character_id);
    const cls = "rel-edge " + relEdgeClass(e) + (relGraphState.selectedEdge === e.id ? " selected" : "");
    const geo = rev ? relCurvedGeometry(s._pos, t._pos, rev.id < e.id ? 1 : -1) : relStraightGeometry(s._pos, t._pos);
    const label = escapeHtml(relTypeLabel(e.relationship_type));
    svg += `<g class="${cls}" data-rel-id="${e.id}">
      ${geo.d
        ? `<path d="${geo.d}" fill="none" stroke="currentColor"/>`
        : `<line x1="${geo.x1}" y1="${geo.y1}" x2="${geo.x2}" y2="${geo.y2}" stroke="currentColor"/>`}
      <polygon points="${relArrow(geo.tip, geo.angle, geo.ux, geo.uy)}"/>
      <text class="rel-edge-label" x="${geo.labelX.toFixed(1)}" y="${geo.labelY.toFixed(1)}">${label}${e.open_issue_count ? " ⚠" : ""}</text>
    </g>`;
  }

  for (const c of characters) {
    const cls = `rel-node ${c.is_player ? "rel-node-player" : "rel-node-npc"}` +
      (relGraphState.selectedChar === c.id ? " selected" : "");
    const name = escapeHtml(c.name.length > 12 ? c.name.slice(0, 12) + "…" : c.name);
    svg += `<g class="${cls}" data-char-id="${c.id}" transform="translate(${c._pos.x.toFixed(1)}, ${c._pos.y.toFixed(1)})">
      <circle r="${REL_NODE_R}"></circle>
      <text y="4">${name}</text>
    </g>`;
  }

  svg += `</svg>`;
  container.innerHTML = svg;

  // Edge click
  container.querySelectorAll(".rel-edge").forEach(g => {
    g.addEventListener("click", (ev) => {
      ev.stopPropagation();
      relGraphState.selectedEdge = parseInt(g.dataset.relId);
      relGraphState.selectedChar = null;
      drawRelGraph(container);
      const e = relGraphState.data.edges.find(x => x.id === parseInt(g.dataset.relId));
      if (e) openRelDetail(e.source_character_id, e.target_character_id);
    });
  });

  // Node click
  container.querySelectorAll(".rel-node").forEach(g => {
    g.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const charId = parseInt(g.dataset.charId);
      relGraphState.selectedChar = relGraphState.selectedChar === charId ? null : charId;
      relGraphState.selectedEdge = null;
      drawRelGraph(container);
    });
  });

  // Drag nodes
  container.addEventListener("mousedown", (ev) => {
    const g = ev.target.closest(".rel-node");
    if (!g) return;
    ev.preventDefault();
    relGraphState.drag = { charId: parseInt(g.dataset.charId) };
  });
  container.addEventListener("mousemove", (ev) => {
    if (!relGraphState.drag) return;
    const svgEl = container.querySelector("svg");
    if (!svgEl) return;
    const rect = svgEl.getBoundingClientRect();
    const c = relGraphState.data.characters.find(x => x.id === relGraphState.drag.charId);
    if (!c) return;
    const sx = REL_GRAPH_W / rect.width, sy = REL_GRAPH_H / rect.height;
    c._pos = {
      x: Math.max(REL_NODE_R, Math.min(REL_GRAPH_W - REL_NODE_R, (ev.clientX - rect.left) * sx)),
      y: Math.max(REL_NODE_R, Math.min(REL_GRAPH_H - REL_NODE_R, (ev.clientY - rect.top) * sy)),
    };
    drawRelGraph(container);
  });
  container.addEventListener("mouseup", () => { relGraphState.drag = null; });
  container.addEventListener("mouseleave", () => { relGraphState.drag = null; });
}

// ---- Detail panel: edge info + issues + timeline ----
function openRelDetail(sourceId, targetId) {
  relDetailState.sourceId = sourceId;
  relDetailState.targetId = targetId;
  relDetailState.timelineOffset = 0;
  relDetailState.timelineTotal = 0;
  document.querySelectorAll("#modal-relationships .tab-content").forEach(t => t.classList.remove("active"));
  document.getElementById("rel-detail-panel").classList.remove("hidden");
  const body = document.getElementById("rel-detail-body");
  const title = document.getElementById("rel-detail-title");
  title.textContent = "Загрузка…";
  body.innerHTML = `<p class="field-hint">Загрузка…</p>`;

  const chatId = AppState.currentChatId;
  Promise.all([
    apiRequest("GET", `/chats/${chatId}/relationships/${sourceId}/${targetId}`),
    apiRequest("GET", `/chats/${chatId}/relationships/${sourceId}/${targetId}/issues?state=all`),
    apiRequest("GET", `/chats/${chatId}/characters?include_player=true`),
  ]).then(([edge, issues, chars]) => {
    const nameOf = id => (chars.find(c => c.id === id)?.name) || `ID:${id}`;
    title.textContent = `${nameOf(sourceId)} → ${nameOf(targetId)}`;
    let html = "";

    // Metrics bars
    html += `<div class="rel-metrics-bars">`;
    for (const m of ["affection", "trust", "attraction", "resentment", "jealousy"]) {
      html += `<div class="mb-row">
        <span class="mb-label">${METRIC_LABELS[m]}</span>
        <div class="mb-track"><div class="mb-fill ${relMetricClass(edge, m)}" style="width:${edge[m]}%"></div></div>
        <span class="mb-val">${edge[m]}</span>
      </div>`;
    }
    html += `</div>`;
    html += `<div><span class="rel-type">${escapeHtml(relTypeLabel(edge.relationship_type))}</span></div>`;
    if (edge.description) html += `<p class="rel-desc">${escapeHtml(edge.description)}</p>`;

    // Pair issues
    if (issues.length) {
      html += `<h5 style="margin-top:10px">Вопросы пары</h5>`;
      for (const issue of issues) {
        html += issueCard(issue, sourceId, targetId);
      }
    }

    body.innerHTML = html;
    bindIssueActions(body, sourceId, targetId);
    renderRelTimeline(body);
  }).catch(e => {
    title.textContent = "Ошибка";
    body.innerHTML = `<p class="field-hint">Ошибка: ${escapeHtml(e.message)}</p>`;
  });
}

function renderRelTimeline(body) {
  const chatId = AppState.currentChatId;
  const { sourceId, targetId, timelineOffset } = relDetailState;
  const url = `/chats/${chatId}/relationships/${sourceId}/${targetId}/timeline?limit=50&offset=${timelineOffset}`;
  apiRequest("GET", url).then(data => {
    const events = data.events || [];
    const firstPage = timelineOffset === 0;
    let html = (firstPage ? `<h5 style="margin-top:12px">Таймлайн</h5>` : "") +
      `<div class="rel-timeline">`;

    if (!events.length) {
      html += `<p class="field-hint">Событий пока нет.</p>`;
    }
    for (const ev of events) {
      html += `<div class="rel-tl-event kind-${escapeHtml(ev.kind || "llm")}">
        <div class="rel-tl-top">
          <span class="rel-tl-kind">${relKindLabel(ev.kind)}</span>
          <span class="rel-tl-time">${ev.timestamp ? formatTime(ev.timestamp) : ""}${ev.round_id ? " · " + escapeHtml(ev.round_id) : ""}</span>
        </div>
        <div>${escapeHtml(ev.description || ev.reason || "")}</div>`;
      const deltas = [];
      for (const m of ["affection", "trust", "attraction", "resentment", "jealousy"]) {
        const d = ev["delta_" + m];
        if (d) {
          deltas.push(`<span class="rel-tl-delta ${d > 0 ? "positive" : "negative"}">${METRIC_LABELS[m]} ${d > 0 ? "+" : ""}${d}</span>`);
        }
      }
      if (deltas.length) html += `<div class="rel-tl-deltas">${deltas.join("")}</div>`;
      html += `<div class="rel-tl-snapshot">После: ${escapeHtml(
        ["affection", "trust", "attraction", "resentment", "jealousy"]
          .map(m => `${METRIC_LABELS[m]} ${ev[m + "_after"]}`).join(" · "))}</div>`;
      for (const msg of ev.source_messages || []) {
        html += `<div class="rel-tl-msg"><b>${escapeHtml(msg.role === "user" ? "Игрок" : "Персонаж")}</b>: ${escapeHtml(msg.content)}</div>`;
      }
      html += `</div>`;
    }
    html += `</div>`;

    // Sparklines (only on first page)
    if (firstPage && events.length >= 2) {
      html += `<h5 style="margin-top:10px">Динамика метрик</h5>`;
      for (const m of ["affection", "trust", "attraction", "resentment", "jealousy"]) {
        html += `<div style="margin-bottom:6px"><span class="mb-label">${METRIC_LABELS[m]}</span>${relSpark(m, events)}</div>`;
      }
    }

    relDetailState.timelineTotal = data.pagination?.total || events.length;
    relDetailState.timelineOffset = timelineOffset + events.length;

    if (relDetailState.timelineOffset < relDetailState.timelineTotal) {
      html += `<button class="btn btn-secondary btn-sm btn-load-more" id="rel-tl-more">Загрузить ещё</button>`;
    }

    body.querySelector("#rel-tl-more")?.remove();
    body.insertAdjacentHTML("beforeend", html);
    document.getElementById("rel-tl-more")?.addEventListener("click", () => renderRelTimeline(body));
  }).catch(e => {
    body.insertAdjacentHTML("beforeend", `<p class="field-hint">Таймлайн: ${escapeHtml(e.message)}</p>`);
  });
}

function relSpark(metric, events) {
  const series = events.map(e => e[metric + "_after"]).filter(v => v != null);
  if (series.length < 2) return "";
  const W = 200, H = 46, pad = 3;
  const min = Math.min(...series), max = Math.max(...series);
  const span = (max - min) || 1;
  const pts = series.map((v, i) => {
    const x = pad + (i * (W - 2 * pad)) / (series.length - 1);
    const y = H - pad - ((v - min) / span) * (H - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `<svg class="rel-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="var(--accent-blue)" stroke-width="2"/></svg>`;
}

function issueCard(issue, sourceId, targetId) {
  const imp = issue.importance || 5;
  const impCls = imp >= 7 ? "imp-high" : (imp >= 4 ? "imp-med" : "imp-low");
  const time = issue.created_at ? formatTime(issue.created_at) : "";
  return `<div class="rel-issue-card ${issue.state === "resolved" ? "resolved" : ""}"
    data-source-id="${sourceId}" data-target-id="${targetId}">
    <div class="rel-issue-top">
      <span class="rel-issue-type">${issueTypeLabel(issue.issue_type)}</span>
      <span class="rel-issue-importance ${impCls}">важность ${imp}/10</span>
      <span class="rel-issue-meta">${time}${issue.rounds_since_last_mention ? ` · не упоминалось ${issue.rounds_since_last_mention} раунд(ов)` : ""}</span>
    </div>
    <div class="rel-issue-text">${escapeHtml(issue.text)}</div>
    ${issue.state === "resolved"
      ? `<div class="rel-issue-meta">Решено${issue.resolved_round_id ? " в " + escapeHtml(issue.resolved_round_id) : ""}</div>`
      : `<div class="rel-issue-actions"><button class="btn btn-sm rel-issue-resolve-btn" data-issue-id="${issue.id}">Решить</button></div>`}
  </div>`;
}

function bindIssueActions(scope, sourceId, targetId) {
  scope.querySelectorAll(".rel-issue-resolve-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const card = btn.closest(".rel-issue-card");
      const reasonWrap = card.querySelector(".rel-issue-reason");
      if (reasonWrap) { reasonWrap.classList.remove("hidden"); return; }
      const wrap = document.createElement("div");
      wrap.className = "rel-issue-reason";
      wrap.innerHTML = `<input type="text" placeholder="Причина решения (необязательно)">
        <button class="btn btn-sm btn-primary">OK</button>
        <button class="btn btn-sm rel-issue-cancel">Отмена</button>`;
      card.appendChild(wrap);
      wrap.querySelector(".btn-primary").addEventListener("click", async () => {
        const reason = wrap.querySelector("input").value.trim();
        try {
          await apiRequest("POST", `/chats/${AppState.currentChatId}/relationships/${sourceId}/${targetId}/issues/${btn.dataset.issueId}/resolve`, { reason });
          showToast("Вопрос решён", "success");
          openRelDetail(sourceId, targetId);
        } catch (e) { showToast("Ошибка: " + e.message); }
      });
      wrap.querySelector(".rel-issue-cancel").addEventListener("click", () => wrap.remove());
    });
  });
}

function bindIssueActionsFromCard(scope) {
  scope.querySelectorAll(".rel-issue-resolve-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const card = btn.closest(".rel-issue-card");
      const sourceId = parseInt(card.dataset.sourceId);
      const targetId = parseInt(card.dataset.targetId);
      const reasonWrap = card.querySelector(".rel-issue-reason");
      if (reasonWrap) { reasonWrap.classList.remove("hidden"); return; }
      const wrap = document.createElement("div");
      wrap.className = "rel-issue-reason";
      wrap.innerHTML = `<input type="text" placeholder="Причина решения (необязательно)">
        <button class="btn btn-sm btn-primary">OK</button>
        <button class="btn btn-sm rel-issue-cancel">Отмена</button>`;
      card.appendChild(wrap);
      wrap.querySelector(".btn-primary").addEventListener("click", async () => {
        const reason = wrap.querySelector("input").value.trim();
        try {
          await apiRequest("POST", `/chats/${AppState.currentChatId}/relationships/${sourceId}/${targetId}/issues/${btn.dataset.issueId}/resolve`, { reason });
          showToast("Вопрос решён", "success");
          renderOpenIssues();
        } catch (e) { showToast("Ошибка: " + e.message); }
      });
      wrap.querySelector(".rel-issue-cancel").addEventListener("click", () => wrap.remove());
    });
  });
}

function closeRelDetail() {
  document.getElementById("rel-detail-panel").classList.add("hidden");
  document.querySelectorAll("#modal-relationships .tab-content").forEach(t => t.classList.remove("active"));
  const active = document.getElementById(`rtab-${relActiveTab}`);
  if (active) active.classList.add("active");
}
document.getElementById("btn-rel-detail-close").addEventListener("click", closeRelDetail);
document.getElementById("btn-rel-detail-edit").addEventListener("click", () => {
  if (relDetailState.sourceId == null) return;
  const { sourceId, targetId } = relDetailState;
  closeRelDetail();
  switchRelTab("rel-list").then(() => {
    const item = document.querySelector(`.rel-editable[data-source="${sourceId}"][data-target="${targetId}"]`);
    if (item) {
      item.scrollIntoView({ block: "center" });
      item.style.outline = "2px solid var(--accent)";
      setTimeout(() => { item.style.outline = ""; }, 2000);
    }
  });
});

// ---- Open issues view (chat-wide) ----
function renderOpenIssues() {
  const container = document.getElementById("rel-issues-view");
  if (!AppState.currentChatId) { container.innerHTML = ""; return; }
  container.innerHTML = `<p class="field-hint">Загрузка…</p>`;
  const chatId = AppState.currentChatId;
  Promise.all([
    apiRequest("GET", `/chats/${chatId}/relationships/issues?state=open`),
    apiRequest("GET", `/chats/${chatId}/relationships/issues?state=resolved`),
  ]).then(([open, resolved]) => {
    const groupByPair = issues => {
      const map = {};
      for (const issue of issues) {
        const key = `${issue.source_character_id}:${issue.target_character_id}`;
        const label = `${issue.source_name || "?"} → ${issue.target_name || "?"}`;
        (map[key] = map[key] || { label, items: [] }).items.push(issue);
      }
      return map;
    };
    let html = "";
    const openGroups = groupByPair(open);
    if (!open.length) html = `<p class="field-hint">Открытых вопросов нет.</p>`;
    for (const key of Object.keys(openGroups)) {
      const g = openGroups[key];
      html += `<div class="rel-issues-group"><h5>${escapeHtml(g.label)}</h5>`;
      for (const issue of g.items) {
        html += issueCard(issue, issue.source_character_id, issue.target_character_id);
      }
      html += `</div>`;
    }
    if (resolved.length) {
      html += `<details style="margin-top:8px"><summary>Решённые (${resolved.length})</summary><div style="margin-top:6px">`;
      for (const issue of resolved) {
        html += issueCard(issue, issue.source_character_id, issue.target_character_id);
      }
      html += `</div></details>`;
    }
    container.innerHTML = html;
    bindIssueActionsFromCard(container);
  }).catch(e => {
    container.innerHTML = `<p class="field-hint">Ошибка: ${escapeHtml(e.message)}</p>`;
  });
}

// ===== Init =====
// Show Ollama hint on first visit
if (!localStorage.getItem("ai_roleplay_visited")) {
  showToast("Убедитесь, что Ollama запущена: ollama serve", "info");
  localStorage.setItem("ai_roleplay_visited", "1");
}

// Restore generation state after page refresh
(async function checkGenerationAfterReload() {
  const stored = sessionStorage.getItem(GEN_STORAGE_KEY);
  if (stored) {
    try {
      const { chatId } = JSON.parse(stored);
      if (chatId) {
        const status = await apiRequest("GET", `/chats/${chatId}/generation-status`);
        if (status.active) {
          AppState.sendingChatIds.add(chatId);
        }
      }
    } catch (_) {}
    sessionStorage.removeItem(GEN_STORAGE_KEY);
  }
})();

loadChats();
setupMobileBackButton();
