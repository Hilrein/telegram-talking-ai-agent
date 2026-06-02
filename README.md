# Telegram Talking AI Agent

A command-line interface (CLI) application that leverages the Telegram Client API and NVIDIA NIM to analyze communication patterns and generate style-mimicking responses.

## Overview

This application serves as an intelligent agent capable of automating communication for your Telegram Business account. By leveraging NVIDIA NIM models, it generates context-aware, style-mimicking responses that you can review and approve directly within a control chat.

All data processing and storage occur locally using SQLite, ensuring privacy. The only data transmitted to the AI provider (NVIDIA NIM) is the specific conversation context required for response generation.

## Prerequisites

- **Python 3.10+**
- **NVIDIA NIM API Key**
- **Telegram Bot Token** (from [@BotFather](https://t.me/BotFather))
- **Telegram Business Account** (Premium required to use Business bots)

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Hilrein/telegram-talking-ai-agent.git
   cd telegram-talking-ai-agent
   ```

2. **Set up a virtual environment (optional but recommended):**
   ```bash
   python -m venv venv
   # Windows
   .\venv\Scripts\activate
   # Linux/macOS
   source venv/bin/activate
   ```

3. **Install the package and dependencies:**
   ```bash
   pip install -e .
   ```

## Configuration

1. **Create the environment file:**
   Copy the example configuration file to `.env`:
   ```bash
   # Windows
   copy .env.example .env
   # Linux/macOS
   cp .env.example .env
   ```

2. **Configure credentials:**
   Open the `.env` file and enter your credentials:
   ```env
   NVIDIA_API_KEY=your_nvidia_api_key
   # Bot token from @BotFather
   BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11

   # Your personal chat ID for receiving notifications
   BUSINESS_OWNER_CHAT_ID=123456789

   # Timeout for pending approvals in minutes (default: 10)
   PENDING_TIMEOUT_MINUTES=10

   # AI model for response generation (default: nvidia/llama-3.1-nemotron-ultra-253b-v1)
   BUSINESS_AI_MODEL=nvidia/llama-3.1-nemotron-ultra-253b-v1

   # Optional: custom style prompt
   BUSINESS_STYLE_PROMPT=Отвечай кратко и дружелюбно.

   # API token for the Mini App dashboard (any random string)
   BUSINESS_API_TOKEN=change-me-to-a-long-random-string
   ```

## Usage

#### Setup

1. **Bot Creation**: Create a bot via [@BotFather](https://t.me/BotFather) and copy the token to your `.env` file as `BOT_TOKEN`.
2. **Business Connection**: Go to Telegram Settings -> Telegram Business -> Chat Bots and add your bot.
3. **Owner Chat ID**: Get your personal Telegram Chat ID (e.g., from @userinfobot) and set it as `BUSINESS_OWNER_CHAT_ID` in `.env`.

#### Running

```bash
tg-agent
```
Or alternatively:
```bash
python -m src.main
```


# Timeout for pending approvals in minutes (default: 10)
PENDING_TIMEOUT_MINUTES=10

# AI model for response generation (default: nvidia/llama-3.1-nemotron-ultra-253b-v1)
BUSINESS_AI_MODEL=nvidia/llama-3.1-nemotron-ultra-253b-v1

# Optional: custom style prompt
BUSINESS_STYLE_PROMPT=Отвечай кратко и дружелюбно.
```

#### Running

```bash
tg-business
```
Or alternatively:
```bash
python -m src.business.runner
```

#### How It Works

1. **Connection**: When a Business account connects your bot, you receive a notification.
2. **Monitoring**: Every incoming business message is forwarded to your control chat as a summary.
3. **AI Response**: The bot generates a proposed reply and sends it to you with approval buttons:
   - ✅ **Принять** — sends the reply on behalf of your business account
   - ❌ **Отклонить** — discards the reply (nothing is sent)
   - ✏️ **Переписать** — prompts you for instructions, then regenerates the reply
4. **Timeout**: If you don't respond within the configured timeout, the pending reply expires automatically.

All actions are logged in the SQLite database for audit purposes.

## License

This project is licensed under the MIT License.