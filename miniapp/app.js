/* ── Tab routing ──────────────────────────────────────────── */
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const target = btn.dataset.tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`panel-${target}`).classList.add('active');
  });
});

/* ── Contacts ─────────────────────────────────────────────── */
async function loadContacts() {
  const list = document.getElementById('contacts-list');
  list.innerHTML = `
    <div class="loading">
      <div class="spinner"></div>
      <span>Загрузка контактов…</span>
    </div>`;

  try {
    const res = await fetch('/api/contacts');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderContacts(data.contacts || []);
  } catch (e) {
    list.innerHTML = `
      <div class="empty-state">
        <span class="empty-icon">⚠️</span>
        <span>Ошибка загрузки: ${e.message}</span>
      </div>`;
  }
}

function renderContacts(contacts) {
  const list = document.getElementById('contacts-list');
  if (!contacts.length) {
    list.innerHTML = `
      <div class="empty-state">
        <span class="empty-icon">💬</span>
        <span>Нет контактов. Подождите первых сообщений.</span>
      </div>`;
    return;
  }

  list.innerHTML = '';
  contacts.forEach((c, i) => {
    const initials = (c.sender_name || '?').charAt(0).toUpperCase();
    const lastDate = c.last_message_at
      ? new Date(c.last_message_at).toLocaleDateString('ru', { day: 'numeric', month: 'short' })
      : '';

    const card = document.createElement('div');
    card.className = 'contact-card';
    card.style.animationDelay = `${i * 40}ms`;
    card.innerHTML = `
      <div class="avatar">${initials}</div>
      <div class="contact-info">
        <div class="contact-name">${esc(c.sender_name || 'Неизвестный')}</div>
        <div class="contact-meta">${c.total_messages} сообщений ${lastDate ? '· ' + lastDate : ''}</div>
      </div>
      <div class="badges">
        <button class="btn-history" data-chat-id="${c.chat_id}" title="Открыть историю">История</button>
        <button class="contact-import-btn" data-chat-id="${c.chat_id}" title="Импортировать чат">+ импорт</button>
      </div>`;

    // Quick-import: pre-fill chat_id and switch to import tab
    card.querySelector('.contact-import-btn').addEventListener('click', e => {
      e.stopPropagation();
      document.getElementById('input-chat-id').value = c.chat_id;
      document.querySelector('[data-tab="import"]').click();
    });

    // Open History
    card.querySelector('.btn-history').addEventListener('click', e => {
      e.stopPropagation();
      openHistory(c.chat_id);
    });

    list.appendChild(card);
  });
}

/* ── History Modal ────────────────────────────────────────── */
async function openHistory(chatId) {
  const modal = document.getElementById('history-modal');
  const list = document.getElementById('history-list');
  modal.classList.remove('hidden');
  list.innerHTML = '<div class="loading"><div class="spinner"></div><span>Загрузка истории…</span></div>';

  try {
    const res = await fetch(`/api/history/${chatId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderHistory(data.messages || []);
  } catch (e) {
    list.innerHTML = `<div class="empty-state"><span class="empty-icon">⚠️</span><span>Ошибка загрузки: ${e.message}</span></div>`;
  }
}

function renderHistory(messages) {
  const list = document.getElementById('history-list');
  if (!messages.length) {
    list.innerHTML = `<div class="empty-state"><span class="empty-icon">💬</span><span>Нет сообщений.</span></div>`;
    return;
  }

  list.innerHTML = '';
  messages.forEach(m => {
    const bubble = document.createElement('div');
    bubble.className = `msg-bubble ${m.role === 'user' ? 'user' : 'assistant222'}`;
    const date = new Date(m.created_at).toLocaleString('ru', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
    });
    const sender = m.sender_name || (m.role === 'user' ? 'Пользователь' : 'Агент');
    
    bubble.innerHTML = `
      <div class="msg-meta">${esc(sender)} • ${date}</div>
      <div class="msg-content">${esc(m.content).replace(/\n/g, '<br/>')}</div>
    `;
    list.appendChild(bubble);
  });
  
  // Scroll to bottom
  list.scrollTop = list.scrollHeight;
}

document.getElementById('btn-close-history').addEventListener('click', () => {
  document.getElementById('history-modal').classList.add('hidden');
});

document.getElementById('btn-refresh').addEventListener('click', loadContacts);

/* ── File drop zone ───────────────────────────────────────── */
const dropZone = document.getElementById('file-drop');
const fileInput = document.getElementById('input-file');
const fileLabel = document.getElementById('file-label');
let selectedFile = null;

fileInput.addEventListener('change', () => {
  if (fileInput.files.length) handleFile(fileInput.files[0]);
});

dropZone.addEventListener('dragover', e => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
});
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});

function handleFile(file) {
  if (!file.name.endsWith('.json')) {
    showStatus('Пожалуйста, выберите файл .json', 'error');
    return;
  }
  selectedFile = file;
  fileLabel.textContent = `📄 ${file.name}`;
  dropZone.classList.add('has-file');
  hideStatus();
}

/* ── Import form ──────────────────────────────────────────── */
document.getElementById('btn-import').addEventListener('click', async () => {
  const chatId = document.getElementById('input-chat-id').value.trim();
  const btn = document.getElementById('btn-import');

  if (!chatId) { showStatus('Введите Chat ID', 'error'); return; }
  if (!selectedFile) { showStatus('Выберите файл result.json', 'error'); return; }

  btn.disabled = true;
  btn.textContent = 'Импортирую…';
  hideStatus();

  try {
    const form = new FormData();
    form.append('chat_id', chatId);
    form.append('file', selectedFile);

    const res = await fetch('/api/import', { method: 'POST', body: form });
    const data = await res.json();

    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

    showStatus(`✅ Импортировано ${data.imported} сообщений для chat_id ${data.chat_id}`, 'success');
    selectedFile = null;
    fileInput.value = '';
    fileLabel.textContent = 'Выберите или перетащите файл';
    dropZone.classList.remove('has-file');
  } catch (e) {
    showStatus(`❌ Ошибка: ${e.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Импортировать';
  }
});

/* ── Helpers ──────────────────────────────────────────────── */
function showStatus(msg, type) {
  const el = document.getElementById('import-status');
  el.textContent = msg;
  el.className = `import-status ${type}`;
}
function hideStatus() {
  const el = document.getElementById('import-status');
  el.className = 'import-status hidden';
}
function esc(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/* ── Init ─────────────────────────────────────────────────── */
loadContacts();
