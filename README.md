# EQ Autopilot

The desktop app that executes EdgeQuant signals on your own broker accounts.
This is the **full source of the app we ship** — published so you can check that the
program does what the screen says it does.

---

## Why this repository exists

A trading app asks for a lot of trust. It holds broker credentials and places real orders.
"Trust us" is not an answer, so the source is here instead.

With this repository you can:

- **read** every line that touches your keys and your orders,
- **build** the app yourself and run that build instead of ours,
- **verify** that the binary you downloaded was built from this source.

## Verify the download you already have

Each release is published with a SHA-256 hash on the download page. Compare it yourself:

```bash
# macOS
shasum -a 256 EQ-Autopilot-macOS.zip
```
```powershell
# Windows
Get-FileHash .\EQ-Autopilot-Windows.zip -Algorithm SHA256
```

If the value matches the one shown next to the download button, the file you received is the
file we published. The app also shows both values on its own screen, side by side.

## What is here, and what is not

**Here** — everything that runs on your machine:

| | |
|---|---|
| `eqgui.py` | the app itself: screens, signal handling, order placement, stop management |
| `eqexec/` | sizing, scheduling, consent, broker adapters |
| `eqexec/broker/` | Topstep (ProjectX), Tradovate, Interactive Brokers, Bybit, Bitget, OKX, NinjaTrader |
| `nt8_addon/` | the NinjaTrader 8 add-on used for brokers without an API |
| `test_*.py` | offline tests, including the stop-protection suite |

**Not here** — the signal method. The app receives a direction and a stop price; it does not
compute them. Features, model and research stay private. This repository is about *execution*:
what happens to your account after a signal arrives.

## How your credentials are handled

- Broker keys are stored in the operating system's own secure store
  (macOS Keychain, Windows Credential Manager) — never in this repo. The app writes a key to
  the store, reads it back to confirm, and only then removes it from its config file
  (`_save_full` / `_kc_save` in `eqgui.py`). If the store is unavailable on your machine
  (locked keychain, headless VM, no keyring backend), the app never writes the key in plain
  text: it asks you for a PIN and keeps the key encrypted with a PIN-derived key
  (PBKDF2-HMAC-SHA256, 1.2M rounds, AES-256-GCM) in the config file, then asks for that PIN
  once at each launch (`_enc_ask_pin` / `_enc_restore`). If you cancel the PIN, the key is
  not saved at all and the app tells you to re-enter it.
- Signal and heartbeat files are encrypted per member with AES-256-GCM, key derived from your
  token (`autopilot_crypto.py`). Builds before 2026.09.23b used an HMAC-authenticated stream
  cipher; the server keeps sending that format to those builds, and this build reads both.
- Keys are sent to your **broker** only. They are never transmitted to EdgeQuant.
  `grep` for the network calls and check for yourself.
- What the app does send to EdgeQuant (`app.edgequant.app`), so you can see exactly what leaves
  your machine (all in `eqgui.py`):
  - when it downloads your encrypted signal and status files: a hash of your token in the file
    name (`_heartbeat`, `_sig_loop`); the same hash and the instrument when it asks for the current
    price to show an open position's profit and loss (`_pos_px_fetch`);
  - a keep-alive while the app runs: your membership token, the app version, a random device ID,
    the app language, which assets are connected and armed, the broker name per asset, the
    NinjaTrader bridge state, for an account recognised as a 150K prop account its broker, prop
    firm and last four characters, and your consent version and time, plus status events such as
    a failed pre-entry check with its error text (`_alive_ping`, `_send_ev`);
  - after each automated entry and each close: the instrument, total quantity, number of
    accounts, the quantity ordered and filled per account (broker and the account's last four
    characters only), and the reference price against the fill price (`_send_fill`, `_send_gap`),
    plus a one-way hash so two machines cannot enter twice on one account: of the account name
    for futures, of the API key ID or the exchange account number for crypto, never the key
    itself (`_claim_entry`);
  - for Autopilot members, an encrypted summary per day and asset: the result in R, a size
    multiple against your own baseline, the broker names, the entry and exit price when there is
    a single one, and how the app closed the trade. No dollar amounts or balances
    (`push_profile`). It goes once a day, three minutes after each close, and when you press the
    sync button;
  - when you press Send log: the app log of the last 48 hours, masked like error text below.
    Amounts (balance, $) are included only if you choose to include them (`_send_log`);
  - error reports: the app version, the OS name and the error text with long digit runs masked
    (`_report_error`; set `EQ_ERR_REPORT=0` to turn them off);
  - alerts meant for you, such as a missed entry or a lost broker connection, relayed through
    our server to your Telegram or Discord (`_member_alert`);
  - on a PIN reset you ask for: your token and the four-digit code (`/eqpin`).
  - backup 5-minute price bars, sent to the server only from the operator's own device; member
    installations do not send these bars (`_bar_backup_tick` returns unless the tier is `admin`,
    and the server stores bars only from the owner's token);
  - evaluation-account watch events (when a daily limit or a pass target is reached: the prop
    firm and account size, the limit, the day's profit and loss, the balance, the open positions
    and the account's last four characters): only when the server turns them on for a token,
    which today is the operator's own token only. Member installations do not send them
    (`_dce_note`, `_dce_on`).

  In error text, alerts and status events the app cuts the account IDs you configured to their
  last four characters and masks long digit runs; a message your broker returned can still contain
  other identifiers. No keys, passwords or PIN are ever included.

## Build it yourself

Python **3.9** is required (the GUI uses Tk 8.6; newer Python ships Tk 9 and the window
comes up empty).

```bash
python3.9 -m venv venv
./venv/bin/pip install -r requirements.txt pyinstaller
./venv/bin/python -m PyInstaller --clean --noconfirm "EQ Autopilot.spec"     # macOS
./venv/bin/python -m PyInstaller --clean --noconfirm "EQ Autopilot Windows.spec"   # Windows
```

Run the offline tests — they need no broker connection and no keys:

```bash
./venv/bin/python test_stop_guard_offline.py
./venv/bin/python test_build_guards_offline.py
./venv/bin/python test_portfolio_weights_offline.py
```

## Safety model

- **Stops are placed at the broker**, not held in the app. If the app closes, the stop stays.
- **Manual wins.** If you move a stop by hand, the app stops touching it — it will not tighten
  or reset what you decided.
- **Protective stop or no position.** If a stop order is rejected after entry, the position is
  closed immediately rather than left unprotected.
- **Closing flattens the whole account.** Use a dedicated account; do not mix in other trades.

## Reporting a security problem

Email the address on the site rather than opening a public issue, and give us time to ship a
fix before disclosure.

## License

See `LICENSE`.
