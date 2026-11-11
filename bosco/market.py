"""US regular/extended sessions with holiday and early-close awareness."""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from functools import lru_cache

@lru_cache(maxsize=4)
def calendar(year):
    import exchange_calendars
    return exchange_calendars.get_calendar('XNYS', start=f'{year-1}-01-01', end=f'{year+1}-12-31')

def market_session(now=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo('America/New_York'))
    cal = calendar(local.year)
    day = local.date().isoformat()
    if not cal.is_session(day): return None
    opening, closing = cal.session_open(day).to_pydatetime(), cal.session_close(day).to_pydatetime()
    start = local.replace(hour=4, minute=0, second=0, microsecond=0)
    end = local.replace(hour=20, minute=0, second=0, microsecond=0)
    if start <= now < opening: return 'premarket'
    if opening <= now < closing: return 'regular'
    # Broker availability after an early close is not established. Refuse it.
    if closing.astimezone(local.tzinfo).hour != 16: return None
    if closing <= now < end: return 'afterhours'
    return None
