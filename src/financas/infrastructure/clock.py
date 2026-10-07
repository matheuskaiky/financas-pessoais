import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# The user's calendar, whatever the time zone of the machine or container the server runs on.
# Brazil has had no daylight saving time since 2019, so the fallback (a host without tz data)
# is the same wall clock.
try:
    SAO_PAULO: dt.tzinfo = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:  # pragma: no cover - depends on the host's tz database
    SAO_PAULO = dt.timezone(dt.timedelta(hours=-3), "America/Sao_Paulo")


class SystemClock:
    def today(self) -> dt.date:
        return dt.datetime.now(SAO_PAULO).date()

    def now(self) -> dt.datetime:
        return dt.datetime.now(SAO_PAULO).replace(tzinfo=None)  # naive wall time, as before
