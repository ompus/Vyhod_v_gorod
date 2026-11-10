// app.js - Main UI controller

import { api, auth } from "./api.js";

// State
let currentConversationId = null;
let currentEventSource = null;
let conversations = [];

// DOM Elements
const authModal = document.getElementById("auth-modal");
const authForm = document.getElementById("auth-form");
const authTitle = document.getElementById("auth-title");
const authSubmitBtn = document.getElementById("auth-submit-btn");
const authError = document.getElementById("auth-error");
const tabLogin = document.getElementById("tab-login");
const tabRegister = document.getElementById("tab-register");
const btnGuest = document.getElementById("btn-guest");

const userAvatar = document.getElementById("user-avatar");
const userName = document.getElementById("user-name");
const btnLogout = document.getElementById("btn-logout");

const conversationsList = document.getElementById("conversations-list");
const btnNewChat = document.getElementById("btn-new-chat");

const messagesWrapper = document.getElementById("messages-wrapper");
const chatInput = document.getElementById("chat-input");
const btnSend = document.getElementById("btn-send");

let isRegisterMode = false;

// Initialize
document.addEventListener("DOMContentLoaded", async () => {
  setupEventListeners();
  checkAuth();
});

function setupEventListeners() {
  // Auth tabs
  tabLogin.addEventListener("click", () => switchAuthMode(false));
  tabRegister.addEventListener("click", () => switchAuthMode(true));
  authForm.addEventListener("submit", handleAuthSubmit);
  btnGuest.addEventListener("click", handleGuestLogin);
  btnLogout.addEventListener("click", handleLogout);

  window.addEventListener("auth:required", () => showAuthModal(true));

  // Chat controls
  btnNewChat.addEventListener("click", () => startNewChat());
  btnSend.addEventListener("click", () => sendMessage());

  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // Auto-resize textarea
  chatInput.addEventListener("input", () => {
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + "px";
  });

  // Welcome screen suggestion cards
  document.querySelectorAll(".suggestion-card").forEach((card) => {
    card.addEventListener("click", () => {
      const prompt = card.getAttribute("data-prompt");
      if (prompt) {
        chatInput.value = prompt;
        sendMessage();
      }
    });
  });
}

function switchAuthMode(isRegister) {
  isRegisterMode = isRegister;
  authError.textContent = "";
  if (isRegister) {
    tabRegister.classList.add("active");
    tabLogin.classList.remove("active");
    authTitle.textContent = "Регистрация";
    authSubmitBtn.textContent = "Зарегистрироваться";
  } else {
    tabLogin.classList.add("active");
    tabRegister.classList.remove("active");
    authTitle.textContent = "Вход в систему";
    authSubmitBtn.textContent = "Войти";
  }
}

function showAuthModal(show) {
  if (show) {
    authModal.classList.remove("hidden");
  } else {
    authModal.classList.add("hidden");
  }
}

async function checkAuth() {
  if (!auth.isAuthenticated()) {
    showAuthModal(true);
    return;
  }

  try {
    const user = await api.getMe();
    auth.setUser(user);
    renderUser(user);
    showAuthModal(false);
    await loadConversations();
  } catch (err) {
    console.warn("Auth check failed", err);
    showAuthModal(true);
  }
}

function renderUser(user) {
  userName.textContent = user.username;
  userAvatar.textContent = user.username[0].toUpperCase();
}

async function handleAuthSubmit(e) {
  e.preventDefault();
  authError.textContent = "";

  const username = document.getElementById("input-username").value.trim();
  const password = document.getElementById("input-password").value.trim();

  if (!username || !password) {
    authError.textContent = "Заполните все поля";
    return;
  }

  try {
    let data;
    if (isRegisterMode) {
      data = await api.register(username, password);
    } else {
      data = await api.login(username, password);
    }
    renderUser(data.user);
    showAuthModal(false);
    await loadConversations();
  } catch (err) {
    authError.textContent = err.message || "Ошибка входа";
  }
}

async function handleGuestLogin() {
  const guestUser = `user_${Math.floor(100 + Math.random() * 900)}`;
  const guestPass = "user_123";
  try {
    const data = await api.register(guestUser, guestPass);
    renderUser(data.user);
    showAuthModal(false);
    await loadConversations();
  } catch (err) {
    authError.textContent = err.message || "Ошибка гостевого входа";
  }
}

function handleLogout() {
  if (currentEventSource) {
    currentEventSource.close();
  }
  auth.removeToken();
  currentConversationId = null;
  conversations = [];
  renderConversations();
  renderWelcomeScreen();
  showAuthModal(true);
}

// Conversation Management
async function loadConversations() {
  try {
    conversations = await api.getConversations();
    renderConversations();
    if (conversations.length > 0) {
      await selectConversation(conversations[0].id);
    } else {
      renderWelcomeScreen();
    }
  } catch (err) {
    console.error("Failed to load conversations", err);
  }
}

function renderConversations() {
  conversationsList.innerHTML = "";
  conversations.forEach((conv) => {
    const item = document.createElement("div");
    item.className = `conversation-item ${conv.id === currentConversationId ? "active" : ""}`;
    item.innerHTML = `
      <span class="conversation-title" title="${escapeHtml(conv.title)}">${escapeHtml(conv.title)}</span>
      <button class="conversation-delete" title="Удалить">✕</button>
    `;

    item.querySelector(".conversation-title").addEventListener("click", () => selectConversation(conv.id));
    item.querySelector(".conversation-delete").addEventListener("click", (e) => {
      e.stopPropagation();
      deleteConversation(conv.id);
    });

    conversationsList.appendChild(item);
  });
}

async function selectConversation(convId) {
  if (currentEventSource) {
    currentEventSource.close();
  }
  currentConversationId = convId;
  renderConversations();

  try {
    const messages = await api.getMessages(convId);
    renderMessages(messages);
  } catch (err) {
    console.error("Failed to load messages", err);
  }
}

async function deleteConversation(convId) {
  if (!confirm("Удалить диалог? Восстановить его будет нельзя.")) return;
  try {
    await api.deleteConversation(convId);
    conversations = conversations.filter((c) => c.id !== convId);
    if (currentConversationId === convId) {
      currentConversationId = conversations.length > 0 ? conversations[0].id : null;
      if (currentConversationId) {
        await selectConversation(currentConversationId);
      } else {
        renderWelcomeScreen();
      }
    }
    renderConversations();
  } catch (err) {
    alert("Ошибка при удалении: " + err.message);
  }
}

function startNewChat() {
  if (currentEventSource) {
    currentEventSource.close();
  }
  currentConversationId = null;
  renderConversations();
  renderWelcomeScreen();
  chatInput.focus();
}

// Message Rendering
function renderWelcomeScreen() {
  messagesWrapper.innerHTML = `
    <div class="welcome-screen">
      <div class="welcome-title">Ростов-на-Дону: справочный агент по городу</div>
      <div class="welcome-subtitle">Примеры запросов:</div>
      <div class="suggestions-grid">
        <div class="suggestion-card" data-prompt="Надо с Озона забрать заказ. Не подскажешь пункт выдачи недалеко от Малиновского?">
          <div class="suggestion-icon">🏤</div>
          <div class="suggestion-text">«Пункт выдачи заказов недалеко от Малиновского»</div>
        </div>
        <div class="suggestion-card" data-prompt="Где находится круглосуточная аптека на Большой Садовой?">
          <div class="suggestion-icon">💊</div>
          <div class="suggestion-text">«Круглосуточная аптека на Большой Садовой»</div>
        </div>
        <div class="suggestion-card" data-prompt="Найди-ка мне по-быренькому ресторанчик на Ворошиловском. Чёрт его знает, что за ресторан, знакомый говорил, что с вкусными пельменЯми">
          <div class="suggestion-icon">🥟</div>
          <div class="suggestion-text">«Ресторан, где подают пельмени. Название не помню...»</div>
        </div>
        <div class="suggestion-card" data-prompt="Хочу деньжат в Сбербанке снять. В центре Ростова. Какие есть точки?">
          <div class="suggestion-icon">🏧</div>
          <div class="suggestion-text">«Банк ********* в центре города»</div>
        </div>
      </div>
    </div>
  `;

  document.querySelectorAll(".suggestion-card").forEach((card) => {
    card.addEventListener("click", () => {
      const prompt = card.getAttribute("data-prompt");
      if (prompt) {
        chatInput.value = prompt;
        sendMessage();
      }
    });
  });
}

function renderMessages(messages) {
  if (!messages || messages.length === 0) {
    renderWelcomeScreen();
    return;
  }

  messagesWrapper.innerHTML = "";
  messages.forEach((msg) => {
    appendMessageElement(msg.role, msg.content, msg.thoughts, msg.places);
  });
  scrollToBottom();
}

function appendMessageElement(role, content, thoughts = [], places = []) {
  const row = document.createElement("div");
  row.className = `message-row ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = role === "user" ? "👀" : "🛴";

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";

  // If assistant message has thoughts, add thinking accordion
  if (role === "assistant" && thoughts && thoughts.length > 0) {
    bubble.appendChild(createThinkingAccordion(thoughts, false));
  }

  // Text content
  const contentEl = document.createElement("div");
  contentEl.className = "message-text";
  contentEl.innerHTML = formatMarkdown(content);
  bubble.appendChild(contentEl);

  // Place cards
  if (places && places.length > 0) {
    bubble.appendChild(createPlaceCardsGrid(places));
  }

  row.appendChild(avatar);
  row.appendChild(bubble);
  messagesWrapper.appendChild(row);
  return row;
}

function createThinkingAccordion(thoughts, isLive = false) {
  const accordion = document.createElement("div");
  accordion.className = "thinking-accordion";

  const header = document.createElement("div");
  header.className = "thinking-header";

  const status = document.createElement("div");
  status.className = "thinking-status";
  status.innerHTML = `
    ${isLive ? '<div class="pulse-indicator"></div>' : '<span>🧠</span>'}
    <span>${isLive ? "Ход мыслей агента (выполняется)..." : "Ход мыслей и инструменты агента"}</span>
  `;

  const toggle = document.createElement("span");
  toggle.className = "thinking-toggle";
  toggle.textContent = isLive ? "▼" : "▶";

  header.appendChild(status);
  header.appendChild(toggle);

  const body = document.createElement("div");
  body.className = "thinking-body";
  if (!isLive) {
    body.style.display = "none";
  }

  header.addEventListener("click", () => {
    const isHidden = body.style.display === "none";
    body.style.display = isHidden ? "flex" : "none";
    toggle.textContent = isHidden ? "▼" : "▶";
  });

  thoughts.forEach((t) => {
    body.appendChild(createThoughtStepElement(t));
  });

  accordion.appendChild(header);
  accordion.appendChild(body);
  return accordion;
}

function createThoughtStepElement(t) {
  const stepEl = document.createElement("div");
  stepEl.className = "thought-step";

  let badgeClass = "plan";
  let badgeLabel = "ШАГ";
  if (t.tool) {
    badgeClass = t.tool.includes("semantic") ? "vector" : "tool";
    badgeLabel = t.tool;
  }

  stepEl.innerHTML = `
    <span class="thought-badge ${badgeClass}">${badgeLabel}</span>
    <div>
      <strong>${escapeHtml(t.title || "")}</strong>
      ${t.detail ? `<div style="color: #94a3b8; font-size: 11.5px; margin-top: 2px;">${escapeHtml(t.detail)}</div>` : ""}
    </div>
  `;
  return stepEl;
}

function createPlaceCardsGrid(places) {
  const container = document.createElement("div");
  container.className = "place-cards-container";

  places.forEach((p) => {
    const card = document.createElement("div");
    card.className = "place-card";

    const scoreBadge = p.distance_m != null
      ? `<span class="place-score" style="background: rgba(16, 185, 129, 0.2); color: #34d399;">${p.distance_m} м</span>`
      : "";
    const cuisineTag = p.cuisine ? `<span class="place-tag cuisine">🍽️ ${escapeHtml(p.cuisine)}</span>` : "";
    const open24Tag = p.is_24_7 ? `<span class="place-tag open-24">24/7 Круглосуточно</span>` : "";

    let addr = "Ростов-на-Дону";
    if (p.street && p.street !== "None" && p.street !== "—") {
      const hasPrefix = /(улица|проспект|переулок|бульвар|площадь|линия|набережная|^ул\.?\s|^пр\.?\s|^просп\.?\s)/i.test(st);
      const house = p.housenumber && p.housenumber !== "None" ? `, д. ${p.housenumber}` : "";
      addr = `${hasPrefix ? "" : "ул. "}${st}${house}`;
    }

    card.innerHTML = `
      <div class="place-card-header">
        <div class="place-card-name">${escapeHtml(p.name || "Без названия")}</div>
        ${scoreBadge}
      </div>
      <div class="place-card-tags">
        <span class="place-tag">${escapeHtml(p.kind || "место")}</span>
        ${cuisineTag}
        ${open24Tag}
      </div>
      <div class="place-card-address">📍 ${addr}</div>
      <div class="place-card-actions">
        <button class="btn-card-action btn-ask-details" data-id="${p.id}" data-name="${escapeHtml(p.name || "")}">Подробнее</button>
        <button class="btn-card-action btn-ask-route" data-id="${p.id}" data-name="${escapeHtml(p.name || "")}">Как добраться</button>
      </div>
    `;

    card.querySelector(".btn-ask-details").addEventListener("click", () => {
      chatInput.value = `Расскажи подробнее про заведение ${p.name || ""} (ID ${p.id}): часы работы, телефон и отзывы.`;
      sendMessage();
    });

    card.querySelector(".btn-ask-route").addEventListener("click", () => {
      chatInput.value = `Как добраться до ${p.name || ""} (ул. ${p.street || ""}) и сколько до него метров от центра?`;
      sendMessage();
    });

    container.appendChild(card);
  });

  return container;
}

// Sending Messages & Handling SSE Stream
async function sendMessage() {
  const text = chatInput.value.trim();
  if (!text) return;

  // Clear input
  chatInput.value = "";
  chatInput.style.height = "auto";
  btnSend.disabled = true;

  // Clear welcome screen if present
  if (messagesWrapper.querySelector(".welcome-screen")) {
    messagesWrapper.innerHTML = "";
  }

  // Append user message to UI
  appendMessageElement("user", text);
  scrollToBottom();

  // Create empty assistant message placeholder with live thinking
  const assistantRow = document.createElement("div");
  assistantRow.className = "message-row assistant";
  assistantRow.innerHTML = `
    <div class="message-avatar">🛴</div>
    <div class="message-bubble">
      <div class="message-text"></div>
    </div>
  `;
  messagesWrapper.appendChild(assistantRow);
  scrollToBottom();

  const bubble = assistantRow.querySelector(".message-bubble");
  const textContainer = assistantRow.querySelector(".message-text");

  // Live thinking state
  const liveThoughts = [];
  const thinkingAccordion = createThinkingAccordion(liveThoughts, true);
  bubble.insertBefore(thinkingAccordion, textContainer);
  const thinkingBody = thinkingAccordion.querySelector(".thinking-body");

  let accumulatedText = "";
  let receivedPlaces = [];

  currentEventSource = api.streamChat({
    message: text,
    conversationId: currentConversationId,
    onSession: (data) => {
      if (data.conversation_id && !currentConversationId) {
        currentConversationId = data.conversation_id;
        api.getConversations().then((list) => {
          conversations = list;
          renderConversations();
        });
      }
    },
    onThinking: (thought) => {
      liveThoughts.push(thought);
      thinkingBody.appendChild(createThoughtStepElement(thought));
      scrollToBottom();
    },
    onToolCall: (data) => {
      if (data.thought) {
        liveThoughts.push(data.thought);
        thinkingBody.appendChild(createThoughtStepElement(data.thought));
        scrollToBottom();
      }
    },
    onToolResult: (data) => {
      if (data.thought) {
        liveThoughts.push(data.thought);
        thinkingBody.appendChild(createThoughtStepElement(data.thought));
        scrollToBottom();
      }
    },
    onPlaces: (places) => {
      receivedPlaces = places;
    },
    onToken: (chunk) => {
      accumulatedText += chunk;
      textContainer.innerHTML = formatMarkdown(accumulatedText);
      scrollToBottom();
    },
    onDone: (data) => {
      btnSend.disabled = false;
      const pulse = thinkingAccordion.querySelector(".pulse-indicator");
      if (pulse) pulse.remove();
      const statusText = thinkingAccordion.querySelector(".thinking-status span:last-child");
      if (statusText) statusText.textContent = "Ход мыслей и инструменты агента";

      textContainer.innerHTML = formatMarkdown(data.content || accumulatedText);

      // Render place cards if any
      const placesToRender = data.places && data.places.length > 0 ? data.places : receivedPlaces;
      if (placesToRender && placesToRender.length > 0) {
        bubble.appendChild(createPlaceCardsGrid(placesToRender));
      }

      scrollToBottom();
    },
    onError: (err) => {
      btnSend.disabled = false;
      const pulse = thinkingAccordion.querySelector(".pulse-indicator");
      if (pulse) pulse.remove();
      textContainer.innerHTML += `<div style="color: var(--accent-rose); margin-top: 8px;">Ошибка потока данных: ${escapeHtml(err.message || "Связь прервана")}</div>`;
      scrollToBottom();
    },
  });
}

function scrollToBottom() {
  messagesWrapper.scrollTop = messagesWrapper.scrollHeight;
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatMarkdown(text) {
  if (!text) return "";
  let html = escapeHtml(text);

  // Markdown Tables (| Header 1 | Header 2 |)
  html = html.replace(/((?:^\|.+?\|(?:\r?\n|$))+)/gm, (match) => {
    const rawLines = match.trim().split(/\r?\n/).map(l => l.trim()).filter(l => l.startsWith("|") && l.endsWith("|"));
    if (rawLines.length < 2) return match;
    const isSep = /^\|(?:\s*:?-+:?\s*\|)+$/.test(rawLines[1]);
    let tableHtml = '<div class="table-responsive"><table class="markdown-table">';
    let startIdx = 0;
    if (isSep) {
      const headers = rawLines[0].split("|").slice(1, -1).map(c => `<th>${c.trim()}</th>`).join("");
      tableHtml += `<thead><tr>${headers}</tr></thead><tbody>`;
      startIdx = 2;
    } else {
      tableHtml += "<tbody>";
    }
    for (let i = startIdx; i < rawLines.length; i++) {
      if (isSep && rawLines[i] === rawLines[1]) continue;
      const cells = rawLines[i].split("|").slice(1, -1).map(c => `<td>${c.trim()}</td>`).join("");
      tableHtml += `<tr>${cells}</tr>`;
    }
    tableHtml += "</tbody></table></div>";
    return tableHtml;
  });

  // Bold
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  // Italic
  html = html.replace(/\*(.*?)\*/g, "<em>$1</em>");
  // Lists
  html = html.replace(/^\s*•\s*(.*)$/gm, "<li>$1</li>");
  html = html.replace(/^\s*\*\s*(.*)$/gm, "<li>$1</li>");
  html = html.replace(/(<li>.*<\/li>)/s, "<ul>$1</ul>");
  // Linebreaks outside table tags
  html = html.replace(/(?<!<\/tr>|<\/thead>|<\/tbody>|<\/table>|<\/div>)\n/g, "<br>");
  return html;
}
