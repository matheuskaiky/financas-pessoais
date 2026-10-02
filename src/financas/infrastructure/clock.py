import datetime as dt


class SystemClock:
    def today(self) -> dt.date:
        return dt.date.today()

    def now(self) -> dt.datetime:
        return dt.datetime.now()
