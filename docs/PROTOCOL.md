# Native BSL adapter and verification boundaries

Inspected package: `com.tsci.hms`, BSL version `1.0.6`, from the user's connected physical phone. Reference workflow revision: `07a0416e98f86b9896b805e3f1b75fd3947c0ed6`.

## Transport

BSL uses its native CMSP TCP protocol rather than the reference broker's HTTPS API. The configured broker host, port, and public RSA modulus are exported from the selected phone account. Framing uses a little-endian 32-bit total length and 16-bit message code, followed by the payload.

RSA-2048 PKCS#1 v1.5 bootstraps an AES-256 session key (31304/31305/31306). XML requests and replies travel in AES-CBC PKCS#7 envelopes (31307), with inner request/response codes 51000/51001. The fixed IV is inherited from the app protocol; this is not TLS or a redesigned cryptographic scheme. XML is built through ElementTree, external entity/DOCTYPE declarations are rejected, frames are bounded, and the read deadline covers the whole response.

Each response must match both operation and request message number. Heartbeats are answered; unrelated asynchronous messages are ignored and order changes are discovered by polling. Revoked-session notifications close the connection. Credentials and decrypted traffic are not logged by the runtime.

## Operations

| Purpose | Native request | Verification |
| --- | --- | --- |
| Reconnect | `reconnect` | Live success |
| Trading unlock | `login_trade` | Live success using the separate trading password |
| Cash | `cash` | Live success, three currency rows |
| Positions | `holding_stock` | Live success, empty portfolio |
| Today's orders | `today_order` | Live success, empty list |
| Exact order/history fallback | `today_order`, `history_order` | Offline tests; history route inspected in app, no live history match |
| Submit | `place_order` | App request fields inspected; encrypted protocol and adapter tested offline; no live submission |
| Cancel | `cancel_order` | App request fields inspected; exact-ID/context tests offline; no live cancellation |

Paging uses `begin`, `count`, `str_begin` and requires a consistent native `all_count`. Missing counts, repeated rows, changing totals, or incomplete pages fail instead of publishing a partial snapshot.

US submission uses `market_code=USA`, `order_side=1/2`, `trade_type=2` for limit or `6` for market, `ccy_code=USD`, quantity, and limit price when applicable. Limit orders set `extended_trading=Y`; market orders set `N`. The app omits `order_validity` for DAY. Authentication is completed before the journal's send marker is persisted. Cancellation re-reads the exact original order and sends its native context.

## Assumptions requiring deployment observation

The account had no positions or orders during inspection. Nonempty rows, partial fills, execution prices, timestamps, and terminal status mappings therefore have synthetic test coverage rather than live account evidence. Unknown status codes remain `UNKNOWN`; they do not imply a fill or cancellation. Unsupported/malformed position data fails the snapshot. Broker app upgrades can change the protocol and enrollment adapter.

There is no verified BSL equivalent of the reference broker's per-symbol permission/lot-size endpoint. The adapter confirms a USD ledger, permits whole-share US stock tickets, and relies on the broker to enforce instrument permissions, purchasing power, and supported order types. Sells additionally require enough available long holdings after accounting for open sells.

There is no verified BSL trading-session endpoint. Regular/extended hours use the maintained `exchange_calendars` XNYS schedule, including holidays and DST. Premarket starts at 04:00 New York time; normal after-hours end at 20:00. After-hours conversion on early-close dates is deliberately unavailable until broker hours are established. A calendar does not detect real-time exchange halts or broker-specific outages. Explicit limit orders follow the app's DAY/extended behavior and are subject to the broker's acceptance rules.

Position fields map native `qty`, `qty_sellable`, `latest_price`, `market_value`, and `cost_price`/`avg_price`. Daily P/L is computed only when `qty_bod`, `prev_close`, `amount_today_buy`, and `amount_today_sell` are all present. It is never replaced with unrealized P/L. These columns were identified from app models but could not be exercised with this empty portfolio.

## Recovery

BSL has no verified echoed client request ID or server idempotency key. Local command IDs prevent replay inside the preserved account journal, but are not sent as a presumed broker deduplication token. An acknowledged order is reconciled by its exact ID and ticket fields. A sent request without an exact acknowledgment remains ambiguous and blocks new submissions. Unclassified broker errors are also ambiguous; they are not treated as definite rejection.

For an unresolved send, stop the runner and preserve `state/api/orders/` and the broker's order evidence. Establish whether an order exists, its exact ID, and its ticket from the broker before reconciling the journal. Do not delete the journal, retry with a new command ID, or infer identity from symbol/time alone. No automatic guess or automatic resubmission is implemented.

Cancel acknowledgment alone is not success. A cancellation is sent at most once per durable marker, followed by exact-ID status checks. If still unconfirmed, it remains unresolved; it is never blindly repeated. There is no claim of distributed exactly-once execution across broker/Kafka/disk.

## Verified environment

Development verification used Windows, Python 3.12, the real WJY account for nonfinancial operations, and Kafka metadata at the supplied endpoint. No command consumer or live producer was started. No financial orders or cancellations were executed. `constraints-tested.txt` records dependency versions; pandas is constrained below 3 because the Windows application-control policy rejected the installed pandas 3.1.1 binary. The compatible 2.3.3 package loaded and the calendar tests passed without changing system policy. Linux CI is provided but has not run in this workspace.
