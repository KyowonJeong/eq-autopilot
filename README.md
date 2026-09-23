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
| `eqexec/broker/` | Topstep (ProjectX), Tradovate, Interactive Brokers, Bybit, Bitget, NinjaTrader |
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
  (locked keychain, headless VM, no keyring backend), the key stays in the config file and
  the app warns you — at save time and at every launch — until it can move it.
- Signal and heartbeat files are encrypted per member with AES-256-GCM, key derived from your
  token (`autopilot_crypto.py`). Builds before 2026.09.23b used an HMAC-authenticated stream
  cipher; the server keeps sending that format to those builds, and this build reads both.
- Keys are sent to your **broker** only. They are never transmitted to EdgeQuant.
  `grep` for the network calls and check for yourself.
- What the app does send us, and only if you turn it on: a summary of your filled trades for
  your public record page. No keys, no balances. See `push_profile` in `eqgui.py`.

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
