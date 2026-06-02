SCHEMA = """
CREATE TABLE IF NOT EXISTS business_connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    user_name TEXT,
    is_enabled BOOLEAN DEFAULT TRUE,
    can_reply BOOLEAN DEFAULT FALSE,
    connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS business_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id TEXT NOT NULL,
    action TEXT NOT NULL,
    sender_name TEXT,
    message_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    connection_id TEXT NOT NULL,
    sender_name   TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content       TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'live' CHECK(source IN ('live', 'import')),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_business_connections_user ON business_connections(user_id);
CREATE INDEX IF NOT EXISTS idx_business_logs_connection ON business_logs(connection_id);
CREATE INDEX IF NOT EXISTS idx_business_logs_action ON business_logs(action);
CREATE INDEX IF NOT EXISTS idx_chat_messages_chat ON chat_messages(chat_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_messages_connection ON chat_messages(connection_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

