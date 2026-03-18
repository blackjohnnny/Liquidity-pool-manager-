# Liquidity Pool Manager

An automated bot that manages PancakeSwap V3 concentrated liquidity positions on Binance Smart Chain. It monitors live pool data, allocates capital based on your risk preferences, auto-rebalances when conditions shift, and compounds rewards — all controlled through Telegram.

## Features

- **Automated LP Management** — Monitors pools every 15 seconds and rebalances when better opportunities arise.
- **Risk-Based Allocation** — Choose low, medium, or high risk. The bot filters and ranks pools accordingly.
- **Auto-Compounding** — Optionally harvests and reinvests rewards to maximise returns.
- **Fail-Safe Protection** — On critical errors, the bot automatically converts positions to stablecoin and locks itself until you manually reset.
- **Telegram Interface** — Full control via chat commands and inline buttons. No web UI needed.
- **Live Data** — Pulls real-time APR, TVL, and price data from DeFiLlama, Binance, and on-chain sources.

## Prerequisites

- **Python 3.10+**
- **A Telegram account** and a bot token (created via [BotFather](https://t.me/BotFather))
- **A BSC wallet** with a private key and some BNB for gas fees

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/blackjohnnny/Liquidity-pool-manager-.git
cd Liquidity-pool-manager-
```

### 2. Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate        # Linux / macOS
venv\Scripts\activate           # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set up environment variables

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

Then edit `.env`:

```env
# Get your bot token from @BotFather on Telegram
TELEGRAM_BOT_TOKEN=your_bot_token_here

# BSC RPC endpoint (default works fine, or use Ankr/QuickNode for higher rate limits)
BSC_RPC_URL=https://bsc-dataseed.binance.org/
```

> **Security note:** Never commit your `.env` file. It is already in `.gitignore`. Your private key is entered via Telegram at runtime and held in memory only — it is never saved to disk.

### 5. Create your Telegram bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts to name your bot.
3. Copy the bot token into your `.env` file.

## Usage

### Start the bot

```bash
python main.py
```

The bot starts listening for Telegram commands.

### First-Time Setup

Send `/start` to your bot in Telegram. It will walk you through:

1. **Private key** — Paste your BSC wallet key (the message is deleted immediately for security)
2. **Risk level** — Choose Low, Medium, or High via buttons
3. **Compounding** — Enable or disable auto-compounding
4. **Confirm** — Review and confirm your settings

### Telegram Commands

| Command | Description |
|---|---|
| `/start` | Set up the bot (wallet, risk, compounding) |
| `/allocate` | Trigger fund allocation into the best-matching LP |
| `/update` | View current status (balance, position, PnL) |
| `/reset` | Wipe your session and clear safety-lock state |

### Inline Controls

The bot provides inline buttons for:

- **Compounding** — Toggle auto-compounding on/off
- **Risk Profile** — Switch between low, medium, and high risk
- **Pause / Resume** — Temporarily halt or restart the bot cycle
- **Safety Lock** — Clear safety-lock after a fail-safe event

### How It Works

1. **Every 15 seconds**, the bot fetches live LP data from PancakeSwap and price feeds.
2. It **validates** all data — if anything is stale or missing, the cycle is skipped safely.
3. It **compares** current data against the previous cycle to detect meaningful changes.
4. It **filters** pools by your selected risk level, then **scores** remaining pools on APR, TVL, and stability.
5. If a better pool exceeds the rebalance threshold vs your current position, the bot **executes the switch** on-chain.
6. If compounding is enabled and no rebalance is needed, it **harvests and reinvests** rewards.
7. If nothing needs to happen, it **takes no action** — no unnecessary transactions.
8. **PnL and state** are saved locally after every cycle.

### Fail-Safe Behaviour

If the bot encounters a critical error (RPC failure, bad data, failed transaction):

1. All processing is **immediately halted**.
2. Active positions are **swapped to stablecoin** to protect capital.
3. Safety lock activates — **no further cycles run**.
4. You receive a **Telegram alert** with error details.
5. Use the **Clear Safety Lock** button or `/reset` to resume.

## Risk Levels

| Level | Pool Type | Description |
|---|---|---|
| Low | Stablecoin ↔ Stablecoin | Minimal volatility, consistent but lower returns |
| Medium | Stablecoin ↔ Large-cap | Moderate exposure with reasonable yield |
| High | Large-cap ↔ Large-cap | Higher volatility, higher potential returns |

## Project Structure

```
Liquidity-pool-manager-/
├── main.py                          # Entry point
├── requirements.txt                 # Python dependencies
├── .env.example                     # Template — copy to .env and fill in
├── .gitignore
│
├── config/
│   ├── settings.py                  # Constants, contract addresses, defaults
│   └── abi/                         # Smart contract ABIs
│
├── modules/
│   ├── scheduler.py                 # 15-second cycle controller
│   ├── dispatcher.py                # Module orchestration
│   ├── config_manager.py            # User config + risk classification
│   ├── market_fetcher.py            # Pool data + price feeds + validation
│   ├── comparator.py                # Delta calculation vs previous cycle
│   ├── logic_engine.py              # Decision: rebalance / compound / no action
│   ├── execution_engine.py          # On-chain transaction execution
│   ├── pnl_tracker.py              # Profit/loss tracking
│   ├── safety_controller.py         # Fail-safe and error handling
│   └── notifier.py                  # Telegram notifications
│
├── telegram_bot/
│   ├── bot.py                       # Bot setup and handler registration
│   ├── handlers.py                  # /allocate, /update, /reset commands
│   ├── onboarding.py                # /start conversation flow
│   ├── keyboards.py                 # Inline button layouts
│   └── callbacks.py                 # Button press handlers
│
├── utils/
│   ├── web3_helper.py               # Blockchain connection and helpers
│   ├── state_store.py               # Read/write state.json
│   ├── validation.py                # Key and address validation
│   └── formatting.py                # Message formatting
│
└── tests/                           # Unit tests
```

## Hosting

The bot is lightweight and can run on minimal hardware:

| | Minimum | Recommended |
|---|---|---|
| **OS** | Any (Windows, Linux, macOS) | Linux |
| **CPU** | 1 core | 2+ cores |
| **RAM** | 1 GB | 2–4 GB |
| **Storage** | 500 MB | 1 GB |
| **Network** | Stable internet | Same |

Suitable for local development, a VPS (DigitalOcean, AWS, Linode), or a Raspberry Pi.

## Dependencies

- `python-telegram-bot` — Telegram interface
- `web3` — BSC blockchain interaction
- `APScheduler` — Cycle timing
- `python-dotenv` — Environment variable loading
- `requests` — API calls

## License

This project is licensed under the [MIT License](LICENSE).

## Disclaimer

This software interacts with real blockchain contracts and can execute financial transactions. **Use at your own risk.** Always test with small amounts first. The authors are not responsible for any financial losses incurred through the use of this tool.
