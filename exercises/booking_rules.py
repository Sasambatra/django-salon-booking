from datetime import datetime, timedelta
def fits_in_gap(duration_minutes: int, gap_minutes: int) -> bool:
    if gap_minutes >= duration_minutes:
        return True
    return False
def can_cancel(starts_at: datetime, now: datetime) -> bool:
    return starts_at - now >= timedelta(hours=2)

def fits_opening_hours(starts_at: datetime, duration_minutes: int) -> bool:
    if duration_minutes <= 0:
        return False

    if starts_at.weekday() == 6:
        return False

    opens_at = starts_at.replace(hour=9, minute=0, second=0, microsecond=0)
    closes_at = starts_at.replace(hour=18, minute=0, second=0, microsecond=0)
    ends_at = starts_at + timedelta(minutes=duration_minutes)

    return opens_at <= starts_at and ends_at <= closes_at