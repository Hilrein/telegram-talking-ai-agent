/* ── API client (auth + business endpoints) ───────────────── */
class ApiClient {
  constructor() {
    this.token = localStorage.getItem('tg_api_token') || '';
  }

  isAuthed() {
    return !!this.token;
  }

  setToken(t) {
    this.token = t || '';
    if (this.token) localStorage.setItem('tg_api_token', this.token);
    else localStorage.removeItem('tg_api_token');
  }

  logout() {
    this.setToken('');
  }

  async authFetch(path, opts = {}) {
    const headers = { ...(opts.headers || {}) };
    if (this.token) headers['Authorization'] = `Bearer ${this.token}`;
    const res = await fetch(path, { ...opts, headers });
    if (res.status === 401 || res.status === 403) {
      this.logout();
      showLogin();
      throw new Error('Unauthorized');
    }
    return res;
  }

  async login(token) {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    this.setToken(data.access_token);
    return data;
  }

  async getContacts() {
    const res = await this.authFetch('/api/contacts');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()).contacts || [];
  }

  async getHistory(chatId) {
    const res = await this.authFetch(`/api/history/${chatId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()).messages || [];
  }

  async importChat(chatId, file) {
    const form = new FormData();
    form.append('chat_id', chatId);
    form.append('file', file);
    const res = await this.authFetch('/api/import', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async getConnectionStatus() {
    const res = await this.authFetch('/api/connection/status');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async enableConnection() {
    const res = await this.authFetch('/api/connection/enable', { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async disableConnection() {
    const res = await this.authFetch('/api/connection/disable', { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async getStyle() {
    const res = await this.authFetch('/api/settings/style');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async setStyle(value) {
    const res = await this.authFetch('/api/settings/style', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  /* ── Agent API endpoints ── */
  
  async getAgentTasks() {
    const res = await this.authFetch('/api/agent/tasks');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async createAgentTask(taskData) {
    const res = await this.authFetch('/api/agent/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(taskData),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  async deleteAgentTask(taskId) {
    const res = await this.authFetch(`/api/agent/tasks/${taskId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async sendAgentMessage(message, sessionId = 'web_default') {
    const res = await this.authFetch('/api/agent/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async getAgentHistory(sessionId = 'web_default') {
    const res = await this.authFetch(`/api/agent/history?session_id=${sessionId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }
}

window.api = new ApiClient();

/* ── Login screen ──────────────────────────────────────────── */
function showLogin() {
  document.getElementById('login-screen').classList.remove('hidden');
  document.getElementById('app').classList.add('hidden');
}
function hideLogin() {
  document.getElementById('login-screen').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
}

/* ── Init ──────────────────────────────────────────────────── */
if (api.isAuthed()) {
  hideLogin();
} else {
  showLogin();
}

document.getElementById('btn-login').addEventListener('click', async () => {
  const input = document.getElementById('login-token');
  const errEl = document.getElementById('login-error');
  errEl.textContent = '';
  try {
    await api.login(input.value.trim());
    hideLogin();
    location.reload();
  } catch (e) {
    errEl.textContent = `Ошибка: ${e.message}`;
  }
});

document.getElementById('login-token').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('btn-login').click();
});
