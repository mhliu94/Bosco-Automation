# Linux deployment and credential storage

Keep the WJY directory credential-only. The local Windows layout is:

| Location | Contents | Git |
| --- | --- | --- |
| `Documents/KTrader/Bosco/WJY/` | `credentials.json`, original `passwords.txt` | Outside the repository |
| `Documents/Projects/Bosco-Automation/config/bosco.config.json` | Private WJY account/Kafka configuration | Ignored |
| `Documents/Projects/Bosco-Automation/work/bosco-wjy/state/` | Execution journal, reporting state, locks, service output | Ignored |
| `Documents/Projects/Bosco-Automation/work/bosco-wjy/validation.json` | Local verification report | Ignored |
| `Documents/Projects/Bosco-Automation/dist/wjy/` | Optional source archives and checksums | Ignored |
| `Documents/Projects/Bosco-Automation/docs/` | Deployment/protocol instructions | Committed |

The existing private configuration retains the supplied account IDs and Kafka endpoint, with no Kafka authentication. The credential files themselves are unchanged. Its credential path is relative to the config directory; state and logs resolve within the project, outside WJY.

## Install on Linux

Use Python 3.12. Clone the source into `~/apps/Bosco-Automation`, then transfer **only credentials** into `~/KTrader/Bosco/WJY`. `passwords.txt` is optional at runtime; the exported `credentials.json` already contains the trading password.

```bash
mkdir -p ~/apps
git clone https://github.com/mhliu94/Bosco-Automation.git ~/apps/Bosco-Automation
cd ~/apps/Bosco-Automation
python3.12 -m venv .venv
.venv/bin/python -m pip install -c constraints-tested.txt .
```

Transfer the local private `config/bosco.config.json` separately to the clone's `config/` directory. Preserve the complete `work/bosco-wjy/state/` directory when migrating an existing worker; do not run both copies. The relative paths work with this `~/apps/Bosco-Automation` / `~/KTrader/Bosco/WJY` layout. For another layout, set `api.sessionPath` to the full credential path and set `paths.workDir` outside the credential directory.

For a new account without a local config, copy `config/bosco.config.example.json` to `config/bosco.config.json` and fill the account IDs, Kafka server/group, and credential path as described in the [README](../README.md).

```bash
chmod 700 ~/KTrader/Bosco/WJY
chmod 600 ~/KTrader/Bosco/WJY/credentials.json config/bosco.config.json
export BOSCO_CONFIG="$PWD/config/bosco.config.json"
.venv/bin/bosco check-config
.venv/bin/bosco session-check
.venv/bin/bosco kafka-check
.venv/bin/bosco authenticate
.venv/bin/bosco holdings
.venv/bin/bosco limit-order --symbol AAPL --side Buy --quantity 1 --price 1.002 --no-submit
```

These checks do not place or cancel orders. `authenticate` renews trading unlock; `--no-submit` only prepares a ticket. Kafka metadata inspection does not join a group or publish messages.

To begin live control-server command processing:

```bash
.venv/bin/bosco runner
```

The default trading state is enabled, matching the reference script. Starting the runner can execute matching fresh Kafka orders. A new group starts at `latest`; a previously used group resumes saved offsets. Use `SET_TRADING_ENABLED` from the control server for subsequent state changes. A persisted `trading-state.json` under `paths.workDir` overrides the initial config default.

For systemd, use `bosco render-service` on Linux and follow the [README](../README.md#systemd-user-service). The WJY service name is `bosco-bosco-wjy`. No monitor server, emulator, phone connection, or Frida process is required at runtime.

## Verification and renewal

The initial WJY authentication, cash/empty portfolio/empty orders, and Kafka metadata checks succeeded. Financial submission/cancellation and nonempty portfolio/order mappings remain unverified live. The offline tests run without broker or Kafka credentials. The GitHub workflow tests Linux and Windows.

Read [KAFKA.md](KAFKA.md) and [PROTOCOL.md](PROTOCOL.md) for synthetic FOK, extended-hours conversion, and unresolved-submission recovery. Preserve the order journal instead of replaying an ambiguous command as a new order.

If the reconnect session expires, sign in on the physical phone and use `scripts/export_phone.py` as described in [ENROLLMENT.md](ENROLLMENT.md). Export writes only credentials unless a separate config output is explicitly requested. Credentials, passwords, private config, runtime state, and generated archives are excluded from Git.
