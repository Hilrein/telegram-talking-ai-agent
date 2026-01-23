SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    is_user BOOLEAN DEFAULT TRUE,
    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_msg_id INTEGER NOT NULL,
    contact_id INTEGER NOT NULL REFERENCES contacts(telegram_id),
    text TEXT,
    is_outgoing BOOLEAN NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    UNIQUE(telegram_msg_id, contact_id)
);

CREATE TABLE IF NOT EXISTS style_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER UNIQUE NOT NULL REFERENCES contacts(telegram_id),
    style_json TEXT NOT NULL,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    provider TEXT PRIMARY KEY,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_contact ON messages(contact_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_outgoing ON messages(is_outgoing);
"""

