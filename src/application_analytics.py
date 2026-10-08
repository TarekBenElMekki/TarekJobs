"""Session accounting and read-only reports. Callers own transactions; no migrations here."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
METRICS = {
    'review.applied': 'applied', 'review.already_applied': 'already_applied',
    'review.shortlisted': 'shortlisted', 'review.not_matching': 'not_matching',
    'review.not_eligible': 'not_eligible', 'review.expired': 'expired',
    'review.not_interested': 'not_interested',
    'application.link_saved': 'links_saved', 'application.link_replaced': 'links_replaced',
    'followup.transition': 'followup_transitions',
}
REVIEW_ACTIONS = {'dismissed': 'review.not_matching', 'shortlisted': 'review.shortlisted',
                  'not_eligible': 'review.not_eligible'}


def log_event(message: str) -> None:
    # Never log session tokens, client IDs, URLs, payloads, or exception text.
    try:
        logger = logging.getLogger('ttea.application_analytics')
        if not logger.handlers:
            directory = Path(__file__).resolve().parents[1] / 'logs'
            directory.mkdir(exist_ok=True)
            from logging.handlers import RotatingFileHandler
            handler = RotatingFileHandler(directory / 'application_analytics.log',
                                          maxBytes=1_000_000, backupCount=2, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        logger.info(message)
    except OSError:
        pass  # Logging failure cannot block workflow persistence.


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def zone(name: str):
    if name == 'UTC':
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError('Unknown timezone or missing tzdata dependency') from exc


def moment(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def rows(connection, sql, parameters=()):
    cursor = connection.execute(sql, parameters)
    keys = [column[0] for column in cursor.description]
    return [dict(zip(keys, row)) for row in cursor.fetchall()]


def get_session(connection, token):
    result = rows(connection, 'SELECT * FROM application_sessions WHERE session_token = ?', (token,))
    return result[0] if result else None


def start_session(connection, client_id, token, timezone_name='UTC', extension_version=None):
    client_id, token = str(UUID(client_id)), str(UUID(token))
    zone(timezone_name)
    existing = get_session(connection, token)
    if existing:
        if existing['client_id'] != client_id:
            raise ValueError('Session token belongs to a different client')
        # A completed token is never reopened. The worker must create a new token.
        return existing
    connection.execute(
        """UPDATE application_sessions SET ended_at=last_seen_at,
           end_reason='recovered_after_disconnect'
           WHERE client_id=? AND ended_at IS NULL""", (client_id,),
    )
    now = utc_now()
    connection.execute(
        """INSERT INTO application_sessions
           (session_token, client_id, channel, started_at, last_seen_at,
            timezone, extension_version, created_at)
           VALUES (?, ?, 'chrome_sidepanel', ?, ?, ?, ?, ?)""",
        (token, client_id, now, now, timezone_name, extension_version, now),
    )
    return get_session(connection, token)


def touch_session(connection, token, end=False):
    token = str(UUID(token))
    session = get_session(connection, token)
    if session is None:
        raise LookupError('Unknown application session')
    if session['ended_at'] is None:
        now = utc_now()
        if end:
            connection.execute(
                """UPDATE application_sessions SET last_seen_at=?, ended_at=?,
                   end_reason='last_window_closed' WHERE id=? AND ended_at IS NULL""",
                (now, now, session['id']),
            )
        else:
            connection.execute('UPDATE application_sessions SET last_seen_at=? WHERE id=? AND ended_at IS NULL',
                               (now, session['id']))
    return get_session(connection, token)


def record_activity(connection, token, action, job_id, application_id=None, metadata=None):
    """Best-effort savepoint inside the core transaction; never commit it here."""
    if not token:
        return False
    try:
        connection.execute('SAVEPOINT application_analytics_event')
    except sqlite3.Error:
        log_event('Activity savepoint unavailable; core mutation preserved')
        return False
    try:
        session = get_session(connection, token)
        if not session or session['ended_at'] is not None:
            raise ValueError('No active session')
        if action not in METRICS:
            raise ValueError('Unsupported activity')
        now = utc_now()
        connection.execute(
            """INSERT INTO application_activity_events
               (session_id, job_id, application_id, action_type, occurred_at, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session['id'], job_id, application_id, action, now,
             json.dumps(metadata or {}, sort_keys=True)),
        )
        connection.execute('UPDATE application_sessions SET last_seen_at=? WHERE id=?', (now, session['id']))
        connection.execute('RELEASE SAVEPOINT application_analytics_event')
        return True
    except (sqlite3.Error, ValueError, TypeError):
        connection.execute('ROLLBACK TO SAVEPOINT application_analytics_event')
        connection.execute('RELEASE SAVEPOINT application_analytics_event')
        log_event('Activity recording unavailable; core mutation preserved')
        return False


def counts(events):
    result = {key: 0 for key in METRICS.values()}
    result.update(unique_jobs_touched=0, review_decisions=0)
    jobs = set()
    for event in events:
        action = event['action_type']
        if action not in METRICS:
            continue
        result[METRICS[action]] += 1
        result['review_decisions'] += int(action.startswith('review.'))
        if event['job_id'] is not None:
            jobs.add(event['job_id'])
    result['unique_jobs_touched'] = len(jobs)
    return result


def session_analytics(connection, limit, timezone_name):
    local_zone = zone(timezone_name)
    sessions = rows(connection, 'SELECT * FROM application_sessions ORDER BY started_at DESC, id DESC LIMIT ?', (limit,))
    items = []
    for session in sessions:
        events = rows(connection, 'SELECT action_type, job_id FROM application_activity_events WHERE session_id=?', (session['id'],))
        end = session['ended_at'] or session['last_seen_at']
        items.append({
            'id': session['id'], 'started_at': session['started_at'],
            'ended_at': session['ended_at'], 'last_seen_at': session['last_seen_at'],
            'effective_end_at': end, 'is_open': session['ended_at'] is None,
            'end_reason': session['end_reason'], 'timezone': timezone_name,
            'session_timezone': session['timezone'],
            'local_started_at': moment(session['started_at']).astimezone(local_zone).isoformat(),
            'duration_seconds': max(0, (moment(end) - moment(session['started_at'])).total_seconds()),
            **counts(events),
        })
    return {'items': items, 'timezone': timezone_name}


def daily_analytics(connection, days, timezone_name):
    local_zone = zone(timezone_name)
    today = datetime.now(UTC).astimezone(local_zone).date()
    first = today - timedelta(days=days - 1)
    first_record = connection.execute('SELECT MIN(started_at) FROM application_sessions').fetchone()[0]
    if not first_record:
        return {'items': [], 'timezone': timezone_name, 'today': today.isoformat()}
    first = max(first, moment(first_record).astimezone(local_zone).date())
    start = datetime.combine(first, time.min, local_zone).astimezone(UTC)
    stop = datetime.combine(today + timedelta(days=1), time.min, local_zone).astimezone(UTC)
    events = rows(connection,
        'SELECT action_type, job_id, occurred_at FROM application_activity_events WHERE occurred_at>=? AND occurred_at<?',
        (start.isoformat(), stop.isoformat()))
    sessions = rows(connection,
        'SELECT * FROM application_sessions WHERE started_at<? AND COALESCE(ended_at,last_seen_at)>=?',
        (stop.isoformat(), start.isoformat()))
    by_day = {}
    current = first
    while current <= today:
        by_day[current] = {'date': current.isoformat(), 'sessions_started': 0, 'active_seconds': 0, '_events': []}
        current += timedelta(days=1)
    for event in events:
        day = moment(event['occurred_at']).astimezone(local_zone).date()
        if day in by_day:
            by_day[day]['_events'].append(event)
    for session in sessions:
        beginning = moment(session['started_at'])
        ending = moment(session['ended_at'] or session['last_seen_at'])
        day = beginning.astimezone(local_zone).date()
        if day in by_day:
            by_day[day]['sessions_started'] += 1
        cursor, ending = max(beginning, start), min(ending, stop)
        while cursor < ending:
            day = cursor.astimezone(local_zone).date()
            boundary = datetime.combine(day + timedelta(days=1), time.min, local_zone).astimezone(UTC)
            segment_end = min(ending, boundary)
            if day in by_day:
                by_day[day]['active_seconds'] += (segment_end - cursor).total_seconds()
            cursor = segment_end
    items = []
    for day in sorted(by_day, reverse=True):
        item = by_day[day]
        item.update(counts(item.pop('_events')))
        items.append(item)
    return {'items': items, 'timezone': timezone_name, 'today': today.isoformat()}


def tracker(connection, state, search, limit, offset):
    filters, parameters = [], []
    if state != 'all':
        filters.append('a.current_state=?')
        parameters.append(state)
    if search.strip():
        filters.append('(j.title LIKE ? OR j.company LIKE ? OR j.location LIKE ?)')
        parameters.extend(['%' + search.strip() + '%'] * 3)
    where = ' WHERE ' + ' AND '.join(filters) if filters else ''
    base = ' FROM applications a JOIN jobs j ON j.id=a.job_id'
    total = connection.execute('SELECT COUNT(*)' + base + where, parameters).fetchone()[0]
    items = rows(connection, '''SELECT a.id AS application_id, a.job_id, a.current_state,
        a.application_url, a.submitted_at, a.started_at, a.updated_at,
        a.last_activity_at, a.last_activity_at AS latest_activity_at,
        j.title, j.company, j.location, j.canonical_url, j.review_status, j.verdict,
        EXISTS(SELECT 1 FROM application_events e WHERE e.application_id=a.id
               AND e.to_state='applied' AND json_extract(e.metadata_json,'$.historical_application')=1)
               AS historical_application''' + base + where +
        ' ORDER BY a.last_activity_at DESC, a.id DESC LIMIT ? OFFSET ?', (*parameters, limit, offset))
    for item in items:
        item['historical_application'] = bool(item['historical_application'])
    state_counts = dict(connection.execute('SELECT current_state, COUNT(*) FROM applications GROUP BY current_state').fetchall())
    return {'items': items, 'total': total, 'state_counts': state_counts,
            'total_applications': sum(state_counts.values()), 'limit': limit, 'offset': offset}
