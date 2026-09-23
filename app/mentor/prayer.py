"""Daily prayer times (calculated offline with adhanpy)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.config import get_config

PRAYERS = ("fajr", "dhuhr", "asr", "maghrib", "isha")
DISPLAY = {"fajr": "Fajr", "sunrise": "Sunrise", "dhuhr": "Dhuhr", "asr": "Asr", "maghrib": "Maghrib", "isha": "Isha"}


class PrayerNotConfigured(RuntimeError):
    pass


def is_configured() -> bool:
    user = get_config().user
    return user.latitude is not None and user.longitude is not None


def times_for(day: date | None = None) -> dict[str, datetime]:
    """Prayer times (timezone-aware) for `day`, including sunrise."""
    from adhanpy.PrayerTimes import PrayerTimes
    from adhanpy.calculation.CalculationMethod import CalculationMethod
    from adhanpy.calculation.CalculationParameters import CalculationParameters
    from adhanpy.calculation.Madhab import Madhab

    cfg = get_config()
    if not is_configured():
        raise PrayerNotConfigured(
            "Set your latitude and longitude (Settings page, or config.yaml) to enable prayer times."
        )
    params = CalculationParameters(method=CalculationMethod[cfg.scheduler.prayer_method.upper()])
    params.madhab = Madhab[cfg.scheduler.madhab.upper()]
    day = day or datetime.now(cfg.tz()).date()
    pt = PrayerTimes(
        (float(cfg.user.latitude), float(cfg.user.longitude)),
        datetime(day.year, day.month, day.day),
        calculation_parameters=params,
        time_zone=cfg.tz(),
    )
    return {name: getattr(pt, name) for name in ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha")}


def next_prayer(now: datetime | None = None) -> tuple[str, datetime]:
    now = now or datetime.now(get_config().tz())
    for name, when in times_for(now.date()).items():
        if name in PRAYERS and when > now:
            return name, when
    return "fajr", times_for(now.date() + timedelta(days=1))["fajr"]


def describe_today() -> str:
    try:
        times = times_for()
    except PrayerNotConfigured as e:
        return str(e)
    line = ", ".join(f"{DISPLAY[n]} {t.strftime('%I:%M %p').lstrip('0')}" for n, t in times.items())
    name, when = next_prayer()
    now = datetime.now(get_config().tz())
    mins = int((when - now).total_seconds() // 60)
    return f"Today: {line}. Next is {DISPLAY[name]} in {mins // 60}h {mins % 60}m."
