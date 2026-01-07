# Telegram Talking AI Agent

A command-line interface (CLI) application that leverages the Telegram Client API and Qwen AI to analyze communication patterns and generate style-mimicking responses.

## Overview

This application serves as an intelligent agent capable of learning a user's specific communication style based on historical Telegram data. By analyzing message history (vocabulary, sentence structure, emoji usage), it creates a personalized style profile and suggests context-aware responses during live conversations.

All data processing and storage occur locally using SQLite, ensuring privacy. The only data transmitted to the AI provider (Qwen/DashScope) is the specific conversation context required for response generation.

## Prerequisites

- **Python 3.10** or higher.
- A **Telegram ID and Hash** (obtainable from [my.telegram.org](https://my.telegram.org)).
- An active Telegram account.

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
   Open the `.env` file and enter your Telegram API credentials:
   ```env
   TG_API_ID=12345678
   TG_API_HASH=your_api_hash_here
   ```

## Usage

To start the application, run:

```bash
tg-agent
```
Or alternatively:
```bash
python -m src.main
```

### Operational Flow

1. **Authentication**: On the first run, authorize the client using your phone number or QR code.
2. **AI Authorization**: The application will prompt you to authenticate with the Qwen AI service via a browser.
3. **Contact Selection**: Select a chat from your history to analyze.
4. **Analysis**: The agent processes historical messages to build a style profile.
5. **Interactive Mode**:
   - The agent monitors the selected chat for new messages.
   - Press **Enter** to generate a response based on the context.
   - Options are available to regenerate, edit, or select alternative phrasing.

## License

This project is licensed under the MIT License.