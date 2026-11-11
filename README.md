# Bosco Automation for Linux

An emulator-free BSL Securities worker that speaks the same KTrader Kafka command and reporting contracts as [GigaMoney-Automation-Linux](https://github.com/mhliu94/GigaMoney-Automation-Linux). The runtime connects directly to the BSL broker and Kafka. It needs no phone, ADB, Frida, emulator, browser, or monitor server after enrollment.

This implementation targets the native protocol inspected in BSL Android `com.tsci.hms` version **1.0.6**. Authentication, account reads, and Kafka connectivity have been checked against the WJY account. **Live order submission, fills, cancellation, and nonempty position/order mapping have not been verified against this account.** The corresponding code is covered by offline tests. Linux execution still needs verification on the deployment host; development tests ran on Windows with Python 3.12.

## Implemented workflows

| Gigamoney workflow | Bosco behavior |
| --- | --- |
| Market buy/sell | Native US whole-share market order during the regular session |
| Extended-hours market command | Converts to a DAY limit order using the opposite side of Kafka `price-books`, as in the reference |
| Limit buy/sell | Native DAY limit order; buy prices round up to cents, sell prices round down |
| `LIMIT_ORDER_FOK` | Synthetic limit, wait, then cancel the remainder; partial fills are possible, exactly as in the reference workflow |
| Cancel all | Reads open orders, cancels each by its exact native order ID, then confirms terminal status |
| Trading enable/disable | Durable Kafka-controlled state; cancellations remain available when disabled |
| Account reporting | Cash by currency and positions at startup, periodically, and after order commands |
| Order reporting | KTrader v1 snapshots, current New York trading date, durable sequence/outbox and duplicate suppression |
| Recovery | Account lock, durable pre-send journal, command-ID deduplication, exact-order reconciliation; financial writes are never automatically replayed |
| CLI/service | Read commands, order previews, standalone order commands, runner control, and systemd user-unit generation |

Supported command scope is the reference's US equity execution flow. Dollar-notional orders, fractional lots, short sales, and extra order types have not been added. Broker permissions still apply. No monitor/web server is included.

BSL-specific differences and unverified assumptions are documented in [docs/PROTOCOL.md](docs/PROTOCOL.md). In particular, BSL has no verified echoed client idempotency key: a lost submission acknowledgment without an order ID blocks new submissions pending operator reconciliation. Unknown broker error codes also retain that guard.

## Linux installation

Use Python **3.12**, writable credentials and runtime-state directories, and outbound access to the configured Kafka and broker TCP endpoints. Keep the machine clock synchronized. Paths in the config resolve relative to its directory; `~` is also supported.

Keep **only credentials** in `~/KTrader/Bosco/WJY`. Clone the code separately, put local settings in the ignored `config/bosco.config.json`, and keep runtime state under the project. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the layout and migration instructions.

```bash
mkdir -p ~/apps
git clone https://github.com/mhliu94/Bosco-Automation.git ~/apps/Bosco-Automation
cd ~/apps/Bosco-Automation
python3.12 -m venv .venv
.venv/bin/python -m pip install -c constraints-tested.txt .
cp config/bosco.config.example.json config/bosco.config.json
# Edit the local config with your account IDs, Kafka server/group, and credential path.
# See the Account configuration section below.
chmod 700 ~/KTrader/Bosco/WJY
chmod 600 ~/KTrader/Bosco/WJY/credentials.json config/bosco.config.json
export BOSCO_CONFIG="$PWD/config/bosco.config.json"

.venv/bin/bosco check-config
.venv/bin/bosco session-check
.venv/bin/bosco kafka-check
.venv/bin/bosco holdings
.venv/bin/bosco limit-order --symbol AAPL --side Buy --quantity 1 --price 1.002 --no-submit
```

The final command only prepares a ticket; it does not send an order. `kafka-check` only fetches cluster metadata: it does not join a consumer group or publish messages. `authenticate` refreshes trading authentication without placing an order.

Start command processing when ready:

```bash
.venv/bin/bosco runner
```

**Starting the runner enables processing of matching live commands.** The default is trading enabled, matching Gigamoney. Kafka starts at the saved group offset, or `latest` for a new group. Existing unexpired queued commands may therefore execute. Set `bosco.tradingEnabledDefault` to `false` before the first run if a control-server enable command should be required. Once `trading-state.json` exists under `paths.workDir`, that persisted state takes precedence.

Use a unique command consumer group for each logical account. Run only one worker for the same broker account and state directory. Preserve the entire `paths.workDir` directory across deployments: deleting the journal removes replay protection. None of these state files belong in the credentials directory.

## systemd user service

For the WJY local configuration (`paths.workDir: ../work/bosco-wjy/state`, `service.name: bosco-bosco-wjy`), run these on Linux from the installed source directory:

```bash
.venv/bin/bosco render-service
mkdir -p ~/.config/systemd/user
cp work/bosco-wjy/state/systemd/bosco-bosco-wjy.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now bosco-bosco-wjy.service
journalctl --user -u bosco-bosco-wjy.service -f
```

The generator uses the current Python executable and config's absolute Linux paths. The service restarts on ordinary failures, uses `UMask=0077`, and stops automatic restarts on authentication-required exit code `3`. For a service that persists after logout, enable user lingering according to the host's administration policy. Stop a service with `systemctl --user stop bosco-bosco-wjy.service`; a foreground runner can also be stopped with `bosco runner-stop`.

## Account configuration

`config/bosco.config.example.json` lists defaults. Edit a private copy at `config/bosco.config.json`; the real deployment settings are not committed. For example:

| Setting | Value |
| --- | --- |
| `bosco.accountId` | Logical string ID matching the credential export and control server |
| `bosco.accountNumId` | Numeric ID matching the credential export and control server |
| `kafka.bootstrapServers` | Your Kafka server, e.g. `kafka.example.com:9092` |
| `kafka.consumerGroupId` | Unique group for this account, e.g. `bosco-bosco-wjy` |
| `kafka.clientConfig` | `{}` — no SASL/TLS credentials configured |
| `api.sessionPath` | `~/KTrader/Bosco/WJY/credentials.json` |
| `paths.workDir` | `../work/bosco-wjy/state` |
| `paths.logDir` | `../logs/bosco-wjy` |

Default timings match the reference: 5 seconds before an order/cancel operation, 2 seconds before post-order reporting, 2 seconds before a synthetic FOK cancellation, 30-second holdings interval, and 300-second maximum command age. Read failures receive at most two attempts. Price-book snapshots expire after 300 seconds. `automation.maxCancelCount` defaults to 50 per cancel-all call.

`api.positionCost` chooses `diluted` (`cost_price`) or `average` (`avg_price`). Native daily P/L fields are optional; see the protocol notes. `api.brokerTimezone` defaults to `Asia/Hong_Kong` for native timestamps; `api.orderTimezone` is `America/New_York` for the daily reporting boundary.

For future Kafka authentication, put the standard librdkafka settings in `kafka.clientConfig`. Relative certificate/key paths resolve beside the account config. Kafka usernames/passwords are unnecessary for the currently supplied endpoint.

## Credentials and renewal

The private `credentials.json` contains the selected broker account, device identity, reconnect tokens, broker endpoint/public RSA modulus, and the **separate trading password**. Trading unlock renews automatically when its token is missing or near expiry. The account login password is not required at runtime. `passwords.txt` is an enrollment input, not a runtime dependency; its contents are never put in the source archive.

If the broker revokes/expires the reconnect session, the worker stops intake and records `api/authentication.json` under `paths.workDir`. Sign in on the physical phone and re-export the selected account. Enrollment writes only `credentials.json` by default; an optional config output must be outside that directory. The extraction adapter is version-specific and currently requires the rooted phone and a matching Frida server. Enrollment instructions are in [docs/ENROLLMENT.md](docs/ENROLLMENT.md). No background phone helper is needed after export.

## Tests and implementation notes

```bash
.venv/bin/python -m pip install -c constraints-tested.txt '.[dev]'
.venv/bin/python -m pytest -q
```

Tests use synthetic credentials and in-memory broker/Kafka doubles. They include encrypted handshake/framing, request matching, authentication before a durable send marker, lost replies, exact-ID cancellation, pagination, calendar boundaries, no-replay recovery, holdings, and KTrader status schemas. The CI workflow defines Linux and Windows jobs; it has not been run remotely during this task.

See [docs/KAFKA.md](docs/KAFKA.md) for messages and [NOTICE.md](NOTICE.md) for source attribution. The source archive excludes account credentials, captured broker traffic, APK/decompiled app files, reference checkout, and the development virtual environment.
