import json
import os
import time
import datetime
import zoneinfo

import requests
import icalendar
import recurring_ical_events

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError

from database import get_db
from google_auth import credentials_for_account, AccountAuthError, mark_auth_error

CACHE_TTL = int(os.environ.get("CACHE_TTL_MINUTES", "5")) * 60

# Event titles to hide, comma-separated, case-insensitive. "Blocked Time" is
# the shadow event some calendar apps create to mirror busy slots from one
# calendar onto another, so it always duplicates a real event.
HIDDEN_TITLES = {
    t.strip().lower()
    for t in os.environ.get("HIDDEN_EVENT_TITLES", "Blocked Time").split(",")
    if t.strip()
}


def _is_hidden(event):
    return (event.get("summary") or "").strip().lower() in HIDDEN_TITLES


def _tz():
    return zoneinfo.ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))


def _local_now():
    return datetime.datetime.now(_tz())


# ── Webcal (ICS) subscriptions ──────────────────────────────────────────────

def _webcal_http_url(url):
    if url.startswith("webcal://"):
        return "https://" + url[len("webcal://"):]
    return url


def probe_webcal(url):
    """Fetch and parse a webcal URL; returns the calendar's display name ('' if
    none). Raises on any fetch/parse failure."""
    resp = requests.get(_webcal_http_url(url), timeout=15)
    resp.raise_for_status()
    cal = icalendar.Calendar.from_ical(resp.content)
    name = cal.get("X-WR-CALNAME")
    return str(name) if name else ""


def _ics_to_raw(comp):
    """Convert a VEVENT occurrence into the Google-API event shape so the rest
    of the pipeline treats both sources identically. Returns None for all-day
    events (the agenda only shows timed events) or unparseable ones."""
    try:
        start = comp["DTSTART"].dt
        end_prop = comp.get("DTEND")
        end = end_prop.dt if end_prop is not None else start
        if not isinstance(start, datetime.datetime) or not isinstance(end, datetime.datetime):
            return None  # all-day
        # Floating (naive) times are interpreted in the configured timezone.
        if start.tzinfo is None:
            start = start.replace(tzinfo=_tz())
        if end.tzinfo is None:
            end = end.replace(tzinfo=_tz())
        return {
            "summary":  str(comp.get("SUMMARY", "")),
            "location": str(comp.get("LOCATION", "")) or None,
            "start":    {"dateTime": start.isoformat()},
            "end":      {"dateTime": end.isoformat()},
        }
    except Exception as e:
        print(f"Webcal event parse error: {e}")
        return None


def _webcal_events(db, window_start, window_end):
    """Raw (Google-shaped) events from all enabled webcal subscriptions,
    recurrences expanded. Failures are flagged on the subscription row."""
    raw = []
    subs = db.execute("SELECT * FROM webcal_subscriptions WHERE enabled=1").fetchall()
    for sub in subs:
        try:
            resp = requests.get(_webcal_http_url(sub["url"]), timeout=15)
            resp.raise_for_status()
            cal = icalendar.Calendar.from_ical(resp.content)
            occurrences = recurring_ical_events.of(cal).between(window_start, window_end)
        except Exception as e:
            print(f"Webcal error for {sub['name']}: {e}")
            db.execute(
                "UPDATE webcal_subscriptions SET fetch_error=? WHERE id=?",
                (str(e)[:300], sub["id"]),
            )
            db.commit()
            continue
        if sub["fetch_error"]:
            db.execute("UPDATE webcal_subscriptions SET fetch_error='' WHERE id=?", (sub["id"],))
            db.commit()
        raw.extend(filter(None, (_ics_to_raw(c) for c in occurrences)))
    return raw


# ── Public API ──────────────────────────────────────────────────────────────

def list_calendars(account_row):
    db = get_db()
    try:
        creds = credentials_for_account(account_row, db)
        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        result = svc.calendarList().list(maxResults=100).execute()
        return [
            {"id": c["id"], "name": c.get("summary", c["id"]), "primary": c.get("primary", False)}
            for c in result.get("items", [])
        ]
    except Exception as e:
        print(f"list_calendars error for {account_row['email']}: {e}")
        return []
    finally:
        db.close()


def events_payload():
    db = get_db()
    try:
        cache_key = _build_cache_key(db)
        row = db.execute(
            "SELECT payload, cached_at FROM event_cache WHERE cache_key=?", (cache_key,)
        ).fetchone()

        if row and (time.time() - row["cached_at"]) < CACHE_TTL:
            return json.loads(row["payload"])

        payload = _fetch_and_process(db)
        # Don't cache payloads built from failed fetches — a re-auth (or a
        # transient error clearing) should be reflected on the next poll.
        if not payload["auth_error"]:
            _upsert_cache(db, cache_key, payload)
        return payload
    finally:
        db.close()


# ── Internals ────────────────────────────────────────────────────────────────

def _build_cache_key(db):
    ids = sorted(
        r["calendar_id"]
        for r in db.execute("SELECT calendar_id FROM selected_calendars WHERE enabled=1").fetchall()
    )
    webcal_ids = sorted(
        str(r["id"])
        for r in db.execute("SELECT id FROM webcal_subscriptions WHERE enabled=1").fetchall()
    )
    date_str = _local_now().strftime("%Y-%m-%d")
    return f"{date_str}:{'|'.join(ids)}:w{'|'.join(webcal_ids)}"


def _fetch_and_process(db):
    now = _local_now()
    today_start = datetime.datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=_tz())
    today_end = datetime.datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=_tz())

    raw_events = []
    auth_failed = []
    accounts = db.execute("SELECT * FROM google_accounts").fetchall()

    for account in accounts:
        enabled_ids = [
            r["calendar_id"]
            for r in db.execute(
                "SELECT calendar_id FROM selected_calendars WHERE account_id=? AND enabled=1",
                (account["id"],),
            ).fetchall()
        ]
        if not enabled_ids:
            continue

        try:
            creds = credentials_for_account(account, db)
            svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        except AccountAuthError as e:
            print(f"Auth error for {account['email']}: {e}")
            auth_failed.append(account["email"])
            continue
        except Exception as e:
            print(f"Auth error for {account['email']}: {e}")
            continue

        for cal_id in enabled_ids:
            try:
                result = svc.events().list(
                    calendarId=cal_id,
                    singleEvents=True,
                    orderBy="startTime",
                    timeMin=today_start.isoformat(),
                    timeMax=today_end.isoformat(),
                    maxResults=50,
                    showHiddenInvitations=True,
                ).execute()
                raw_events.extend(result.get("items", []))
            except RefreshError as e:
                # The API client refreshes in-flight on 401; if that refresh is
                # rejected, the refresh token is dead — flag the account.
                print(f"Auth error for {account['email']}: {e}")
                mark_auth_error(db, account["id"], e)
                auth_failed.append(account["email"])
                break
            except HttpError as e:
                # 403 insufficient scopes: the account was connected without the
                # Calendar checkbox ticked on Google's consent screen.
                if e.resp.status == 403 and "insufficient" in str(e).lower():
                    msg = "Missing Calendar permission — reconnect and tick the Calendar checkbox on the Google consent screen"
                    print(f"Auth error for {account['email']}: {msg}")
                    mark_auth_error(db, account["id"], msg)
                    auth_failed.append(account["email"])
                    break
                print(f"Fetch error for {cal_id}: {e}")
            except Exception as e:
                print(f"Fetch error for {cal_id}: {e}")

    raw_events.extend(_webcal_events(db, today_start, today_end))

    return _process(raw_events, now, db, auth_failed)


def _is_declined(event):
    for attendee in event.get("attendees", []):
        if attendee.get("self"):
            return attendee.get("responseStatus") == "declined"
    return False


def _process(raw_events, now, db, auth_failed=None):
    # Drop all-day events; parse and deduplicate
    timed = []
    seen = set()
    for e in raw_events:
        start_str = e.get("start", {}).get("dateTime")
        end_str = e.get("end", {}).get("dateTime")
        if not start_str or not end_str:
            continue  # all-day

        start_dt = datetime.datetime.fromisoformat(start_str).astimezone(_tz())
        end_dt = datetime.datetime.fromisoformat(end_str).astimezone(_tz())
        key = (e.get("summary", ""), start_dt.isoformat())
        if key in seen:
            continue
        seen.add(key)

        # Skip events the user has explicitly declined.
        if _is_declined(e):
            continue

        if _is_hidden(e):
            continue

        timed.append({
            "title":    e.get("summary") or "(No title)",
            "location": e.get("location"),
            "start_dt": start_dt,
            "end_dt":   end_dt,
        })

    timed.sort(key=lambda e: e["start_dt"])

    completed  = [e for e in timed if e["end_dt"] <= now]
    remaining  = [e for e in timed if e["end_dt"] > now]
    next_event = remaining[0] if remaining else None
    later      = remaining[1:5]  # cap at 4

    tomorrow_first = None
    if next_event is None:
        tomorrow_first = _fetch_tomorrow_first(now, db)

    return {
        "auth_error":          bool(auth_failed),
        "auth_error_accounts": auth_failed or [],
        "day_of_week":     now.strftime("%A"),
        "month_day":       now.strftime("%B %-d"),
        "synced_at":       now.strftime("%-I:%M %p"),
        "total_today":     len(timed),
        "completed_today": len(completed),
        "is_all_done":     next_event is None,
        "next_event":      _format_next(next_event, now) if next_event else None,
        "later_events":    [_format_later(e) for e in later],
        "tomorrow_first":  tomorrow_first,
        # Sourced from Exchange so the template doesn't need device.* variables.
        # weather_temp: set this once you add a weather API (e.g. Open-Meteo).
        # battery_pct: Terminus BYOS doesn't expose this via Liquid; leave null.
        "weather_temp":    None,
        "battery_pct":     None,
    }


def _format_next(event, now):
    dur_mins = int((event["end_dt"] - event["start_dt"]).total_seconds() / 60)
    in_progress = event["start_dt"] <= now < event["end_dt"]

    if in_progress:
        countdown = "now"
    else:
        mins_until = int((event["start_dt"] - now).total_seconds() / 60)
        countdown = f"in {mins_until} min" if mins_until < 60 else f"in {round(mins_until / 60)} hr"

    return {
        "start_time":     event["start_dt"].strftime("%-I:%M"),
        "start_meridiem": event["start_dt"].strftime("%p"),
        "end_time":       event["end_dt"].strftime("%-I:%M"),
        "end_meridiem":   event["end_dt"].strftime("%p"),
        "time_range":     f"{event['start_dt'].strftime('%-I:%M')} – {event['end_dt'].strftime('%-I:%M')}",
        "duration":       _fmt_duration(dur_mins),
        "title":          event["title"],
        "location":       event["location"] or "No location",
        "countdown":      countdown,
        "in_progress":    in_progress,
    }


def _format_later(event):
    dur_mins = int((event["end_dt"] - event["start_dt"]).total_seconds() / 60)
    return {
        "start_time":     event["start_dt"].strftime("%-I:%M"),
        "start_meridiem": event["start_dt"].strftime("%p"),
        "duration":       _fmt_duration(dur_mins),
        "title":          event["title"],
        "location":       event["location"] or "No location",
    }


def _fmt_duration(mins):
    if mins >= 60 and mins % 60 == 0:
        return f"{mins // 60}h"
    return f"{mins}m"


def _fetch_tomorrow_first(now, db):
    tomorrow = now.date() + datetime.timedelta(days=1)
    t_start = datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, tzinfo=_tz())
    t_end   = datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 23, 59, 59, tzinfo=_tz())

    candidates = []

    accounts = db.execute("SELECT * FROM google_accounts").fetchall()
    for account in accounts:
        enabled_ids = [
            r["calendar_id"]
            for r in db.execute(
                "SELECT calendar_id FROM selected_calendars WHERE account_id=? AND enabled=1",
                (account["id"],),
            ).fetchall()
        ]
        if not enabled_ids:
            continue
        try:
            creds = credentials_for_account(account, db)
            svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
            for cal_id in enabled_ids:
                result = svc.events().list(
                    calendarId=cal_id,
                    singleEvents=True,
                    orderBy="startTime",
                    timeMin=t_start.isoformat(),
                    timeMax=t_end.isoformat(),
                    maxResults=10,
                    showHiddenInvitations=True,
                ).execute()
                for e in result.get("items", []):
                    if _is_declined(e) or _is_hidden(e):
                        continue
                    start_str = e.get("start", {}).get("dateTime")
                    if start_str:
                        candidates.append(
                            datetime.datetime.fromisoformat(start_str).astimezone(_tz())
                        )
                        break  # results are start-ordered; first hit per calendar
        except Exception:
            continue

    for e in _webcal_events(db, t_start, t_end):
        if _is_hidden(e):
            continue
        candidates.append(
            datetime.datetime.fromisoformat(e["start"]["dateTime"]).astimezone(_tz())
        )

    if candidates:
        return min(candidates).strftime("%-I:%M %p")
    return None


def _upsert_cache(db, key, payload):
    existing = db.execute("SELECT id FROM event_cache WHERE cache_key=?", (key,)).fetchone()
    now_ts = int(time.time())
    if existing:
        db.execute(
            "UPDATE event_cache SET payload=?, cached_at=? WHERE cache_key=?",
            (json.dumps(payload), now_ts, key),
        )
    else:
        db.execute(
            "INSERT INTO event_cache (cache_key, payload, cached_at) VALUES (?,?,?)",
            (key, json.dumps(payload), now_ts),
        )
    db.commit()
