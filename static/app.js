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
  sending: false,
};

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
    div.innerHTML = `<div class="chat-item-name">${escapeHtml(chat.name)}</div>
      <div class="chat-item-preview">${escapeHtml(chat.general_prompt.slice(0, 60)) || "Нет описания"}</div>`;
    div.addEventListener("click", () => selectChat(chat.id));
    container.appendChild(div);
  }
}

// ===== Select / Load Chat =====
async function selectChat(chatId) {
  AppState.currentChatId = chatId;
  AppState.sending = false;
  saveLastChatId(chatId);
  renderChatList();
  document.getElementById("btn-send").disabled = false;
  document.getElementById("message-input").disabled = false;

  try {
    const detail = await apiRequest("GET", `/chats/${chatId}`);
    AppState.currentChat = detail;
    AppState.characters = detail.characters || [];
    AppState.messages = detail.messages || [];
    renderHeader();
    renderMessages();
    enableInput(true);
    scrollToBottom();
    
    // Update view for mobile
    if (isMobile) {
      updateView();
    }
  } catch (e) {
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
}

// ===== Render Messages =====
function renderMessages() {
  const container = document.getElementById("messages");
  container.innerHTML = "";
  for (const msg of AppState.messages) {
    if (msg.role === "system") continue;
    const div = document.createElement("div");
    div.className = `message ${msg.role === "user" ? "user" : "character"}`;

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
    if (msg.role === "user") {
      author.textContent = "Игрок";
    } else {
      author.textContent = getCharacterName(msg.character_id);
    }

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
    div.appendChild(avatar);
    div.appendChild(body);
    container.appendChild(div);
  }
  scrollToBottom();
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
  if (AppState.sending || AppState.currentChatId === null) return;
  const input = document.getElementById("message-input");
  const text = input.value.trim();
  if (!text) return;

  input.value = "";
  AppState.sending = true;
  disableInput(true);
  showTyping("Ожидание ответа...");

  const userMsg = {
    role: "user",
    content: text,
    timestamp: new Date().toISOString(),
    character_id: null,
  };
  AppState.messages.push(userMsg);
  renderMessages();

  try {
    const newMessages = await apiRequest("POST", `/chats/${AppState.currentChatId}/message`, { content: text });
    if (newMessages && newMessages.length) {
      AppState.messages.pop();
      for (const m of newMessages) {
        AppState.messages.push(m);
      }
    }
    hideTyping();
    renderMessages();
  } catch (e) {
    hideTyping();
    AppState.messages.pop();
    renderMessages();
    showToast("Ошибка: " + e.message);
  } finally {
    AppState.sending = false;
    disableInput(false);
  }
}

// ===== Clear History =====
async function clearHistory() {
  if (!AppState.currentChatId) return;
  if (!confirm("Очистить всю историю сообщений? Персонажи и настройки сохранятся.")) return;
  try {
    await apiRequest("DELETE", `/chats/${AppState.currentChatId}/messages`);
    AppState.messages = [];
    renderMessages();
    showToast("История очищена", "success");
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
  try {
    const chat = await apiRequest("POST", "/chats", { name, general_prompt: prompt, model_name: model });
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
document.getElementById("btn-settings").addEventListener("click", () => {
  if (!AppState.currentChat) return;
  const chat = AppState.currentChat;
  document.getElementById("settings-name").value = chat.name || "";
  document.getElementById("settings-prompt").value = chat.general_prompt || "";
  document.getElementById("settings-model").value = chat.model_name || "";
  document.getElementById("settings-history").value = chat.max_history_length || 30;
  document.getElementById("modal-settings").classList.remove("hidden");
  switchTab("general");
  renderCharactersTab();
  renderMemoriesTab();
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
  const model = document.getElementById("settings-model").value.trim();
  const history = parseInt(document.getElementById("settings-history").value) || 30;
  try {
    const updated = await apiRequest("PUT", `/chats/${chatId}`, {
      name, general_prompt: prompt, model_name: model, max_history_length: history,
    });
    AppState.currentChat = { ...AppState.currentChat, ...updated };
    document.getElementById("chat-title").textContent = updated.name;
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
    for (const c of chars) {
      const card = document.createElement("div");
      card.className = "char-card";
      card.innerHTML = `
        <div class="char-card-info">
          <div class="char-card-name">${escapeHtml(c.name)}</div>
          <div class="char-card-detail">${escapeHtml(c.personality) || "Нет описания"} · Порядок: ${c.order_index}</div>
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
function openCharacterEditor(charId) {
  document.getElementById("modal-character").classList.remove("hidden");
  if (charId) {
    const char = AppState.characters.find(c => c.id === charId);
    if (!char) return;
    document.getElementById("char-editor-title").textContent = "Редактировать персонажа";
    document.getElementById("char-editor-id").value = char.id;
    document.getElementById("char-editor-name").value = char.name;
    document.getElementById("char-editor-personality").value = char.personality || "";
    document.getElementById("char-editor-traits").value = char.traits || "";
    document.getElementById("char-editor-order").value = char.order_index;
  } else {
    document.getElementById("char-editor-title").textContent = "Новый персонаж";
    document.getElementById("char-editor-id").value = "";
    document.getElementById("char-editor-name").value = "";
    document.getElementById("char-editor-personality").value = "";
    document.getElementById("char-editor-traits").value = "";
    document.getElementById("char-editor-order").value = AppState.characters.length;
  }
}

document.getElementById("btn-char-cancel").addEventListener("click", () => {
  document.getElementById("modal-character").classList.add("hidden");
});

document.getElementById("btn-char-save").addEventListener("click", async () => {
  const charId = document.getElementById("char-editor-id").value;
  const name = document.getElementById("char-editor-name").value.trim();
  if (!name) { showToast("Введите имя персонажа"); return; }
  const personality = document.getElementById("char-editor-personality").value.trim();
  const traits = document.getElementById("char-editor-traits").value.trim();
  const order_index = parseInt(document.getElementById("char-editor-order").value) || 0;

  try {
    if (charId) {
      await apiRequest("PUT", `/characters/${charId}`, { name, personality, traits, order_index });
      showToast("Персонаж обновлён", "success");
    } else {
      await apiRequest("POST", `/chats/${AppState.currentChatId}/characters`, { name, personality, traits, order_index });
      showToast("Персонаж добавлен", "success");
    }
    document.getElementById("modal-character").classList.add("hidden");
    renderCharactersTab();
  } catch (e) {
    showToast("Ошибка: " + e.message);
  }
});

// ===== Memories Tab =====
async function renderMemoriesTab() {
  const container = document.getElementById("memories-list");
  container.innerHTML = "";
  if (!AppState.currentChatId || !AppState.characters.length) {
    container.innerHTML = "<p style='color:var(--text-muted);padding:8px;'>Нет персонажей</p>";
    return;
  }
  for (const char of AppState.characters) {
    try {
      const mems = await apiRequest("GET", `/characters/${char.id}/memories`);
      if (!mems || !mems.length) {
        const p = document.createElement("p");
        p.style.cssText = "color:var(--text-muted);padding:4px 8px;font-size:13px;";
        p.textContent = `${char.name}: нет воспоминаний`;
        container.appendChild(p);
        continue;
      }
      const label = document.createElement("div");
      label.style.cssText = "font-weight:600;font-size:13px;margin:8px 0 4px;color:var(--text-secondary);";
      label.textContent = `${char.name} (${mems.length})`;
      container.appendChild(label);
      for (const mem of mems) {
        const item = document.createElement("div");
        item.className = "memory-item";
        item.innerHTML = `
          <div class="memory-item-text">${escapeHtml(mem.content)}</div>
          <button class="btn btn-sm btn-danger" data-mem-id="${mem.id}" title="Удалить">✕</button>`;
        container.appendChild(item);
        item.querySelector("button").addEventListener("click", async () => {
          try {
            await apiRequest("DELETE", `/memories/${mem.id}`);
            showToast("Воспоминание удалено", "success");
            renderMemoriesTab();
          } catch (e) {
            showToast("Ошибка: " + e.message);
          }
        });
      }
    } catch (_) {}
  }
}

// ===== Send button + Enter =====
document.getElementById("btn-send").addEventListener("click", sendMessage);
document.getElementById("message-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// ===== Clear history button =====
document.getElementById("btn-clear-history")?.addEventListener("click", clearHistory);

// ===== Init =====
// Show Ollama hint on first visit
if (!localStorage.getItem("ai_roleplay_visited")) {
  showToast("Убедитесь, что Ollama запущена: ollama serve", "info");
  localStorage.setItem("ai_roleplay_visited", "1");
}

loadChats();