// api.js - Client API and SSE Stream Manager

const API_BASE = "";

export const auth = {
  getToken() {
    return localStorage.getItem("rostov_token");
  },
  setToken(token) {
    localStorage.setItem("rostov_token", token);
  },
  removeToken() {
    localStorage.removeItem("rostov_token");
    localStorage.removeItem("rostov_user");
  },
  getUser() {
    const raw = localStorage.getItem("rostov_user");
    return raw ? JSON.parse(raw) : null;
  },
  setUser(user) {
    localStorage.setItem("rostov_user", JSON.stringify(user));
  },
  isAuthenticated() {
    return !!this.getToken();
  },
};

async function request(endpoint, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...options.headers,
  };

  const token = auth.getToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers,
  });

  if (res.status === 401) {
    auth.removeToken();
    window.dispatchEvent(new CustomEvent("auth:required"));
    throw new Error("Необходима авторизация");
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Ошибка сервера" }));
    throw new Error(err.detail || "Произошла ошибка");
  }

  return res.json();
}

export const api = {
  // Auth API
  async register(username, password) {
    const data = await request("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    auth.setToken(data.access_token);
    auth.setUser(data.user);
    return data;
  },

  async login(username, password) {
    const data = await request("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    auth.setToken(data.access_token);
    auth.setUser(data.user);
    return data;
  },

  async getMe() {
    return request("/api/auth/me");
  },

  // Conversations API
  async getConversations() {
    return request("/api/conversations");
  },

  async createConversation(title = "Новый диалог") {
    return request("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ title }),
    });
  },

  async deleteConversation(convId) {
    return request(`/api/conversations/${convId}`, {
      method: "DELETE",
    });
  },

  async getMessages(convId) {
    return request(`/api/conversations/${convId}/messages`);
  },

  // SSE Chat Stream
  streamChat({ message, conversationId, onSession, onThinking, onToolCall, onToolResult, onPlaces, onToken, onDone, onError }) {
    const token = auth.getToken();
    if (!token) {
      onError(new Error("Не авторизован"));
      return null;
    }

    const params = new URLSearchParams({
      message,
      token,
    });
    if (conversationId) {
      params.append("conversation_id", conversationId);
    }

    const url = `/api/chat/stream?${params.toString()}`;
    const eventSource = new EventSource(url);

    eventSource.addEventListener("session", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (onSession) onSession(data);
      } catch (err) {
        console.error("Error parsing session event", err);
      }
    });

    eventSource.addEventListener("thinking", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (onThinking) onThinking(data);
      } catch (err) {
        console.error("Error parsing thinking event", err);
      }
    });

    eventSource.addEventListener("tool_call", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (onToolCall) onToolCall(data);
      } catch (err) {
        console.error("Error parsing tool_call event", err);
      }
    });

    eventSource.addEventListener("tool_result", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (onToolResult) onToolResult(data);
      } catch (err) {
        console.error("Error parsing tool_result event", err);
      }
    });

    eventSource.addEventListener("places", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (onPlaces) onPlaces(data);
      } catch (err) {
        console.error("Error parsing places event", err);
      }
    });

    eventSource.addEventListener("token", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (onToken) onToken(data.text);
      } catch (err) {
        console.error("Error parsing token event", err);
      }
    });

    eventSource.addEventListener("done", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (onDone) onDone(data);
      } catch (err) {
        console.error("Error parsing done event", err);
      }
      eventSource.close();
    });

    eventSource.onerror = (err) => {
      console.error("SSE stream error", err);
      if (onError) onError(err);
      eventSource.close();
    };

    return eventSource;
  },
};
