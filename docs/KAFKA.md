# KTrader Kafka interface

The worker preserves the reference project's topic names, JSON contracts, command routing, manual commits, and post-order reporting. WJY routes by string `account_id: "BOSCO-WJY"`; reports also include numeric `account_num_id: 29`.

| Topic | Direction | Role |
| --- | --- | --- |
| `trading-commands` | Control server → worker | Account-filtered order/state commands |
| `account-details` | Worker → control server | Cash, positions, and trading state |
| `order-status` | Worker → control server | Changed KTrader v1 order snapshots |
| `price-books` | Market-data source → worker | Extended-hours market-to-limit pricing |

## Commands

Supply a unique stable `command_id` and current ISO-8601 UTC `ts` with every command. The following is an illustrative payload, not a command published by setup:

```json
{
  "command_id": "your-unique-command-id",
  "account_id": "BOSCO-WJY",
  "ts": "2026-11-10T15:00:00Z",
  "type": "LIMIT_ORDER",
  "symbol": "AAPL",
  "side": "BUY",
  "qty_shares": 1,
  "limit_price": 1.01
}
```

| `type` | Required type-specific fields |
| --- | --- |
| `MARKET_ORDER` | `symbol`, `side`, `qty_shares` |
| `LIMIT_ORDER` | `symbol`, `side`, `qty_shares`, `limit_price` |
| `LIMIT_ORDER_FOK` | Same as limit; submit, wait, cancel remainder |
| `CANCEL_OPEN_ORDERS` | None; cancels the account's open orders |
| `SET_TRADING_ENABLED` | `trading_enabled` boolean |

`LIMIT_ORDER` also uses the synthetic FOK path when `time_in_force` is `"FOK"` or `cancel_unfilled` is `true`. This is not an exchange-atomic FOK guarantee; an executed partial quantity cannot be undone. Buy/sell strings are normalized. Quantity must be positive and a whole number of US shares. Market commands with `notional_usd` are rejected, matching the reference.

Wrong-account, unsupported-type, and stale commands are skipped and committed. Missing/unparseable timestamps are stale by default; timestamps more than 30 seconds in the future are also rejected. `maxCommandAgeSeconds` defaults to 300. A missing command ID falls back to `kafka:<topic>:<partition>:<offset>`, but the control server should supply IDs. Reusing an ID with a different ticket is an error. The same completed ticket returns its durable result without another submission.

The worker uses synchronous manual offset commits and defaults to committing failed commands, as Gigamoney does. Broker effects and Kafka commits cannot form one atomic transaction: the durable order journal prevents replay after a crash/commit failure. Authentication failure stops intake. Disable state suppresses new orders; it still permits cancellations and reporting.

## Account details

```json
{
  "account_id": "BOSCO-WJY",
  "account_num_id": 29,
  "cash": 0.0,
  "cash_by_currency": {"USD": 0.0},
  "positions": [],
  "ts": "2026-11-10T15:00:00+00:00",
  "trading_enabled": true
}
```

`cash` is the actual USD ledger balance. `cash_by_currency` uses the native
`currency` response's `balance` values, so other currencies represent additional
cash holdings. The native `cash` response and `static_cash_by_rate` fields contain
converted summaries and are not used as holdings. Broker currency codes are
preserved. Each position has `symbol`, `qty`, `avg_price`, and available
`marketPrice`, `marketValue`, `dailyPL`, `dailyPLPercent` fields. Daily P/L is omitted
unless the BSL response includes enough data to calculate it. An order-read
failure does not suppress an otherwise complete holdings report.

Account-detail messages use the reference's empty string Kafka key. Producer delivery is acknowledged (`acks=all`, idempotent producer); failures do not cause financial-command replay.

## Order status

The schema is [tests/fixtures/order-status.v1.schema.json](../tests/fixtures/order-status.v1.schema.json). IDs remain strings, including alphanumeric IDs and integers exceeding JavaScript's exact range. Key: JSON `[account_id, order_id]`. A snapshot includes schema version, account IDs, exact broker ID, symbol, side/type, quantities, fill price, status, timestamps, and a durable per-order `sequence` starting at zero. The originating `command_id` is attached when known.

Statuses: `PENDING_NEW`, `NEW`, `PARTIALLY_FILLED`, `PENDING_CANCEL`, `FILLED`, `CANCELED`, `REJECTED`, `EXPIRED`, `UNKNOWN`. A partial withdrawal is `CANCELED` with its real nonzero fill quantity. A locally refused ticket uses `local-rejection:<hash>` instead of inventing a broker order ID.

Only the current New York trading date is published, as in the reference. Terminal records stop detail polling after durable observation. Pending reports are persisted before publish and acknowledged afterward. A crash between Kafka acknowledgment and the local save may resend the exact same sequence/payload; the control server should deduplicate `(account_id, order_id, sequence)`.

## Price books

```json
{
  "symbol": "AAPL",
  "update_time": 1794322800000000000,
  "offer_side": {
    "update_time": 1794322800000000000,
    "levels": [{"prc": "1.01", "total_qty": "100"}]
  },
  "bid_side": {
    "update_time": 1794322800000000000,
    "levels": [{"prc": "1.00", "total_qty": "100"}]
  }
}
```

Timestamps are integer Unix nanoseconds. Buys walk ascending offers; sells walk descending bids. The selected limit is the level covering the requested size, or the outermost available level if visible depth is insufficient. No added slippage band or size cap is imposed. Partial fills remain possible. Missing, stale, future-dated, invalid, or empty books block conversion. The background reader uses direct partition assignment and does not join the trading command group.
