"""Background jobs: daily check-in, wake-up, prayer reminders and user reminders."""

from __future__ import annotations

from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import get_config
from app.logger import get_logger
from app.memory_db import MemoryDB, get_db
from app.mentor import checkin, prayer, wakeup
from app.mentor.notifications import notify

log = get_logger(__name__)


def _hm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


class CompanionScheduler:
    def __init__(self, db: MemoryDB | None = None):
        self.cfg = get_config()
        self.db = db or get_db()
        self.scheduler = BackgroundScheduler(timezone=self.cfg.tz())

    def start(self) -> None:
        s, sc = self.scheduler, self.cfg.scheduler
        h, m = _hm(sc.checkin_time)
        s.add_job(lambda: notify("Daily check-in", checkin.prompt_message(self.db)), "cron",
                  hour=h, minute=m, id="checkin", replace_existing=True)
        if sc.wakeup_enabled:
            h, m = _hm(sc.wakeup_time)
            s.add_job(lambda: notify("Wake up", wakeup.morning_message(self.db)), "cron",
                      hour=h, minute=m, id="wakeup", replace_existing=True)
        if sc.prayer_reminders_enabled and prayer.is_configured():
            self.schedule_prayers()
            s.add_job(self.schedule_prayers, "cron", hour=0, minute=5, id="prayer-refresh", replace_existing=True)
        s.add_job(self.check_reminders, "interval", seconds=30, id="reminders", replace_existing=True)
        s.start()
        log.info("Scheduler started with jobs: %s", [j.id for j in s.get_jobs()])

    def schedule_prayers(self) -> None:
        now = datetime.now(self.cfg.tz())
        before = timedelta(minutes=self.cfg.scheduler.prayer_reminder_minutes_before)
        for name, when in prayer.times_for(now.date()).items():
            if name not in prayer.PRAYERS:
                continue
            label = prayer.DISPLAY[name]
            if when - before > now and before:
                self.scheduler.add_job(
                    notify, "date", run_date=when - before, id=f"prayer-pre-{name}", replace_existing=True,
                    args=("Prayer soon", f"{label} is in {before.seconds // 60} minutes."),
                )
            if when > now:
                self.scheduler.add_job(
                    notify, "date", run_date=when, id=f"prayer-{name}", replace_existing=True,
                    args=("Prayer time", f"It's time for {label}."),
                )

    def check_reminders(self) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        for r in self.db.due_reminders(now):
            notify("Reminder", r["text"])
            self.db.complete_reminder(r["id"])

    def add_reminder(self, text: str, minutes: float) -> str:
        due = datetime.now() + timedelta(minutes=minutes)
        self.db.add_reminder(text, due.isoformat(timespec="seconds"))
        return f"I'll remind you at {due:%I:%M %p}: {text}"

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
