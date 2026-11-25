import os
import json
import sqlite3
from urllib.parse import quote

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, redirect, request, session, url_for, jsonify
from markupsafe import escape

from database import init_db, get_db
from google_auth import authorization_url, exchange_code, credentials_for_account, CALENDAR_SCOPE
from calendar_fetcher import events_payload, list_calendars, probe_webcal

import google.auth.transport.requests
from googleapiclient.discovery import build

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(32))

init_db()


# ── Setup dashboard ──────────────────────────────────────────────────────────

@app.get("/")
def index():
    return redirect("/setup")


@app.get("/setup")
def setup():
    db = get_db()
    accounts = db.execute("SELECT * FROM google_accounts ORDER BY id").fetchall()
    accounts_data = []
    for a in accounts:
        cals = db.execute(
            "SELECT * FROM selected_calendars WHERE account_id=? ORDER BY calendar_name",
            (a["id"],),
        ).fetchall()
        accounts_data.append({"account": dict(a), "calendars": [dict(c) for c in cals]})
    webcals = [
        dict(w)
        for w in db.execute("SELECT * FROM webcal_subscriptions ORDER BY id").fetchall()
    ]
    db.close()

    connected = request.args.get("connected", "")
    webcal_error = request.args.get("webcal_error", "")
    port = os.environ.get("PORT", "4567")
    return render_setup(accounts_data, webcals, connected, webcal_error, port)


# ── OAuth flow ────────────────────────────────────────────────────────────────

@app.get("/oauth/start")
def oauth_start():
    url, state, verifier = authorization_url()
    session["oauth_state"] = state
    session["oauth_code_verifier"] = verifier
    return redirect(url)


@app.get("/oauth/callback")
def oauth_callback():
    error = request.args.get("error")
    if error:
        return f"OAuth error: {escape(error)}", 400

    code  = request.args.get("code")
    state = request.args.get("state")
    if not code:
        return "Missing authorization code", 400

    verifier = session.pop("oauth_code_verifier", None)
    if not verifier:
        return "OAuth session expired — please try connecting again.", 400

    try:
        creds = exchange_code(code, state, verifier)
    except Exception as e:
        return f"Token exchange failed: {escape(str(e))}", 500

    # Google's granular consent screen leaves the Calendar checkbox unticked by
    # default; without that scope the token is useless to us, so refuse it
    # rather than storing an account that can't fetch anything.
    if CALENDAR_SCOPE not in (creds.scopes or []):
        return (
            """<div style="font-family:sans-serif;max-width:560px;margin:80px auto;">
              <h2>Calendar access wasn't granted</h2>
              <p>The Google consent screen has a checkbox for
              <strong>&ldquo;View your calendars&rdquo;</strong> that must be ticked —
              Google leaves it unchecked by default.</p>
              <p><a href="/oauth/start">Try again</a> and tick the Calendar checkbox
              before clicking Continue.</p>
            </div>""",
            400,
        )

    email = _fetch_email(creds)
    if not email:
        return "Could not retrieve Google account email", 500

    db = get_db()
    expires_at = int(creds.expiry.timestamp()) if creds.expiry else 0
    existing = db.execute("SELECT * FROM google_accounts WHERE email=?", (email,)).fetchone()
    if existing:
        db.execute(
            "UPDATE google_accounts SET access_token=?, refresh_token=?, expires_at=?, auth_error='' WHERE id=?",
            (
                creds.token,
                creds.refresh_token or existing["refresh_token"],
                expires_at,
                existing["id"],
            ),
        )
        account_id = existing["id"]
    else:
        cur = db.execute(
            "INSERT INTO google_accounts (email, access_token, refresh_token, expires_at) VALUES (?,?,?,?)",
            (email, creds.token, creds.refresh_token or "", expires_at),
        )
        account_id = cur.lastrowid
    db.commit()

    account = db.execute("SELECT * FROM google_accounts WHERE id=?", (account_id,)).fetchone()
    _sync_calendars(account, db)
    db.close()

    return redirect(f"/setup?connected={quote(email)}")


# ── Calendar selection ────────────────────────────────────────────────────────

@app.post("/calendars/update")
def calendars_update():
    db = get_db()
    # Unchecked checkboxes are absent from the form, so start from all-disabled
    # and enable what was submitted.
    db.execute("UPDATE selected_calendars SET enabled=0")
    db.execute("UPDATE webcal_subscriptions SET enabled=0")
    for key in request.form:
        if key.startswith("calendars["):
            # key format: calendars[ACCOUNT_ID:CALENDAR_ID]
            inner = key[len("calendars["):-1]
            account_id_str, _, cal_id = inner.partition(":")
            db.execute(
                "UPDATE selected_calendars SET enabled=1 WHERE account_id=? AND calendar_id=?",
                (int(account_id_str), cal_id),
            )
        elif key.startswith("webcals["):
            db.execute(
                "UPDATE webcal_subscriptions SET enabled=1 WHERE id=?",
                (int(key[len("webcals["):-1]),),
            )
    db.execute("DELETE FROM event_cache")
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.post("/webcals/add")
def webcal_add():
    url = (request.form.get("url") or "").strip()
    name = (request.form.get("name") or "").strip()

    if not url.startswith(("webcal://", "http://", "https://")):
        return redirect("/setup?webcal_error=" + quote("URL must start with webcal://, http://, or https://"))

    try:
        detected_name = probe_webcal(url)
    except Exception as e:
        return redirect("/setup?webcal_error=" + quote(f"Could not fetch calendar: {e}"[:200]))

    db = get_db()
    try:
        db.execute(
            "INSERT INTO webcal_subscriptions (url, name) VALUES (?,?)",
            (url, name or detected_name or url),
        )
    except sqlite3.IntegrityError:
        db.close()
        return redirect("/setup?webcal_error=" + quote("Already subscribed to that URL."))
    db.execute("DELETE FROM event_cache")
    db.commit()
    db.close()
    return redirect("/setup")


@app.post("/webcals/<int:webcal_id>/remove")
def webcal_remove(webcal_id):
    db = get_db()
    db.execute("DELETE FROM webcal_subscriptions WHERE id=?", (webcal_id,))
    db.execute("DELETE FROM event_cache")
    db.commit()
    db.close()
    return redirect("/setup")


@app.post("/accounts/<int:account_id>/remove")
def account_remove(account_id):
    db = get_db()
    db.execute("DELETE FROM google_accounts WHERE id=?", (account_id,))
    db.execute("DELETE FROM event_cache")
    db.commit()
    db.close()
    return redirect("/setup")


# ── Terminus polling endpoint ─────────────────────────────────────────────────

@app.get("/api/events")
def api_events():
    db = get_db()
    account_count = db.execute("SELECT COUNT(*) FROM google_accounts").fetchone()[0]
    cal_count = db.execute(
        "SELECT COUNT(*) FROM selected_calendars WHERE enabled=1"
    ).fetchone()[0]
    db.close()

    if account_count == 0:
        return jsonify({"error": "no_accounts", "message": "No Google accounts connected yet."})
    if cal_count == 0:
        return jsonify({"error": "no_calendars", "message": "No calendars selected."})

    try:
        payload = events_payload()
        return jsonify(payload)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": "fetch_failed", "message": str(e)}), 500


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch_email(creds):
    try:
        svc = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        info = svc.userinfo().get().execute()
        return info.get("email")
    except Exception:
        return None


def _sync_calendars(account, db):
    available = list_calendars(account)
    for cal in available:
        existing = db.execute(
            "SELECT id FROM selected_calendars WHERE account_id=? AND calendar_id=?",
            (account["id"], cal["id"]),
        ).fetchone()
        if not existing:
            db.execute(
                "INSERT INTO selected_calendars (account_id, calendar_id, calendar_name, enabled) VALUES (?,?,?,?)",
                (account["id"], cal["id"], cal["name"], 1 if cal["primary"] else 0),
            )
    db.commit()


# ── Inline HTML rendering (no template engine dependency) ─────────────────────

def render_setup(accounts_data, webcals, connected, webcal_error, port):
    notice = ""
    if connected:
        notice = f"""
        <div class="notice">
          ✓ Connected <strong>{escape(connected)}</strong> — toggle the calendars you want below.
        </div>"""
    if webcal_error:
        notice += f"""
        <div class="notice notice-error">
          ⚠ {escape(webcal_error)}
        </div>"""

    if not accounts_data:
        account_blocks = """
        <div class="card">
          <div class="empty-accounts">
            <p>No Google accounts connected yet.</p>
            <br>
            <a href="/oauth/start" class="btn btn-primary">Connect Google Account</a>
          </div>
        </div>"""
        connect_link = ""
    else:
        blocks = []
        for entry in accounts_data:
            a = entry["account"]
            cals = entry["calendars"]
            cal_rows = ""
            for cal in cals:
                checked = "checked" if cal["enabled"] else ""
                primary_badge = '<span class="primary-badge">primary</span>' if cal["calendar_id"] == a["email"] else ""
                cal_rows += f"""
                <li>
                  <label class="toggle">
                    <input type="checkbox" class="cal-toggle"
                      name="calendars[{a['id']}:{escape(cal['calendar_id'])}]"
                      value="1" {checked}>
                    <span class="slider"></span>
                  </label>
                  <span class="name">{escape(cal['calendar_name'])}</span>
                  {primary_badge}
                </li>"""

            cal_list = f'<ul class="calendar-list">{cal_rows}</ul>' if cals else '<p style="color:#888;font-size:14px;">No calendars found.</p>'

            auth_warning = ""
            if a.get("auth_error"):
                auth_warning = f"""
              <div class="auth-warning">
                <div>
                  <strong>⚠ Needs re-connection</strong> — Google rejected this account's saved
                  credentials, so its calendars are not being fetched.
                  <div class="auth-warning-detail">{escape(a['auth_error'])}</div>
                </div>
                <a href="/oauth/start" class="btn btn-primary btn-sm">Reconnect</a>
              </div>"""

            blocks.append(f"""
            <div class="card">
              <div class="section-actions">
                <div>
                  <h3>{escape(a['email'])}</h3>
                  <p class="meta">Google Calendar account</p>
                </div>
                <button class="btn btn-danger btn-sm" formaction="/accounts/{a['id']}/remove"
                        onclick="return confirm('Remove this account?')">Remove</button>
              </div>
              {auth_warning}
              {cal_list}
            </div>""")

        account_blocks = "".join(blocks)
        connect_link = '<a href="/oauth/start" class="btn btn-primary" style="margin-bottom:32px;">+ Connect Another Account</a>'

    webcal_rows = ""
    for w in webcals:
        checked = "checked" if w["enabled"] else ""
        error_badge = ""
        if w["fetch_error"]:
            error_badge = f'<span class="error-badge" title="{escape(w["fetch_error"])}">fetch failing</span>'
        webcal_rows += f"""
                <li>
                  <label class="toggle">
                    <input type="checkbox" class="cal-toggle" name="webcals[{w['id']}]" value="1" {checked}>
                    <span class="slider"></span>
                  </label>
                  <span class="name">{escape(w['name'])}
                    <span class="webcal-url" title="{escape(w['url'])}">{escape(w['url'])}</span>
                  </span>
                  {error_badge}
                  <button class="btn btn-danger btn-sm" formaction="/webcals/{w['id']}/remove"
                          onclick="return confirm('Remove this subscription?')">Remove</button>
                </li>"""

    webcal_list = (
        f'<ul class="calendar-list">{webcal_rows}</ul>'
        if webcals
        else '<p style="color:#888;font-size:14px;">No subscriptions yet.</p>'
    )
    webcal_card = f"""
            <div class="card">
              <h3>Calendar Subscriptions</h3>
              <p class="meta">webcal / iCal feeds, independent of your Google accounts</p>
              {webcal_list}
            </div>"""

    accounts_html = f"""
        <form id="calendars-form" action="/calendars/update" method="post">
          {account_blocks}
          {webcal_card}
        </form>
        {connect_link}
        <div class="card">
          <h3>Add Calendar Subscription</h3>
          <p class="meta">Paste a webcal:// or https:// iCal feed URL.</p>
          <form action="/webcals/add" method="post" class="webcal-add">
            <input type="text" name="url" placeholder="webcal://example.com/calendar.ics" required>
            <input type="text" name="name" placeholder="Name (optional)">
            <button class="btn btn-primary">Subscribe</button>
          </form>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TRMNL Agenda</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            font-size: 15px; background: #f5f4f0; color: #1a1a1a; }}
    header {{ background: #000; color: #fff; padding: 16px 32px;
              display: flex; align-items: center; gap: 16px; }}
    header h1 {{ margin: 0; font-size: 18px; font-weight: 600; }}
    header a {{ color: #aaa; text-decoration: none; font-size: 13px; }}
    main {{ max-width: 780px; margin: 40px auto; padding: 0 24px; }}
    h2 {{ font-size: 22px; font-weight: 700; margin: 0 0 6px; }}
    .subtitle {{ color: #666; margin: 0 0 32px; }}
    .card {{ background: #fff; border: 1px solid #e0ddd6; border-radius: 8px;
             padding: 24px; margin-bottom: 20px; }}
    .card h3 {{ margin: 0 0 4px; font-size: 16px; }}
    .card .meta {{ font-size: 13px; color: #888; margin: 0 0 16px; }}
    .btn {{ display: inline-block; padding: 9px 18px; border-radius: 6px;
            font-size: 14px; font-weight: 500; text-decoration: none;
            cursor: pointer; border: none; }}
    .btn-primary {{ background: #000; color: #fff; }}
    .btn-primary:hover {{ background: #222; }}
    .btn-danger {{ background: #fff; color: #c00; border: 1px solid #fcc; }}
    .btn-danger:hover {{ background: #fff5f5; }}
    .btn-sm {{ padding: 5px 12px; font-size: 13px; }}
    .calendar-list {{ list-style: none; margin: 0; padding: 0; }}
    .calendar-list li {{ display: flex; align-items: center; gap: 12px;
                         padding: 9px 0; border-bottom: 1px solid #f0ede6; }}
    .calendar-list li:last-child {{ border-bottom: none; }}
    .calendar-list .name {{ flex: 1; font-size: 14px; }}
    .primary-badge {{ font-size: 11px; font-weight: 600; color: #555; background: #eee;
                      padding: 2px 7px; border-radius: 99px; letter-spacing: .04em; }}
    label.toggle {{ position: relative; display: inline-block;
                    width: 40px; height: 22px; flex-shrink: 0; }}
    label.toggle input {{ opacity: 0; width: 0; height: 0; }}
    label.toggle .slider {{ position: absolute; inset: 0; background: #ccc;
                             border-radius: 22px; transition: .2s; cursor: pointer; }}
    label.toggle input:checked + .slider {{ background: #000; }}
    label.toggle .slider::before {{ content: ""; position: absolute; width: 16px; height: 16px;
                                     left: 3px; bottom: 3px; background: #fff;
                                     border-radius: 50%; transition: .2s; }}
    label.toggle input:checked + .slider::before {{ transform: translateX(18px); }}
    .notice {{ background: #f0fdf4; border: 1px solid #bbf7d0; color: #166534;
               padding: 12px 16px; border-radius: 6px; font-size: 14px; margin-bottom: 20px; }}
    .auth-warning {{ background: #fffbeb; border: 1px solid #fde68a; color: #92400e;
                     padding: 12px 16px; border-radius: 6px; font-size: 14px;
                     margin-bottom: 16px; display: flex; align-items: center;
                     justify-content: space-between; gap: 16px; }}
    .auth-warning-detail {{ font-family: monospace; font-size: 12px; color: #b45309;
                            margin-top: 6px; word-break: break-word; }}
    .auth-warning .btn {{ flex-shrink: 0; }}
    .notice-error {{ background: #fef2f2; border-color: #fecaca; color: #991b1b; }}
    .error-badge {{ font-size: 11px; font-weight: 600; color: #92400e; background: #fef3c7;
                    padding: 2px 7px; border-radius: 99px; letter-spacing: .04em;
                    flex-shrink: 0; cursor: help; }}
    .webcal-url {{ display: block; font-size: 11px; color: #aaa; font-family: monospace;
                   overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
                   max-width: 420px; }}
    .webcal-add {{ display: flex; gap: 10px; }}
    .webcal-add input {{ flex: 1; padding: 8px 12px; border: 1px solid #d5d2ca;
                         border-radius: 6px; font-size: 14px; font-family: inherit; }}
    .webcal-add input[name="name"] {{ flex: 0 0 180px; }}
    .api-box {{ font-family: monospace; font-size: 13px; background: #f5f4f0;
                border: 1px solid #e0ddd6; padding: 10px 14px; border-radius: 5px;
                word-break: break-all; color: #333; margin-top: 12px; }}
    .empty-accounts {{ text-align: center; padding: 48px 0; color: #888; }}
    .empty-accounts p {{ margin: 8px 0; font-size: 15px; }}
    .section-actions {{ display: flex; align-items: center;
                        justify-content: space-between; margin-bottom: 12px; }}
    .section-actions h3 {{ margin: 0; font-size: 16px; }}
  </style>
</head>
<body>
  <header>
    <h1>TRMNL Agenda</h1>
    <a href="/setup">Setup</a>
    <a href="/api/events" target="_blank">Preview JSON</a>
  </header>
  <main>
    {notice}
    <h2>Google Calendar Setup</h2>
    <p class="subtitle">Connect your Google accounts and choose which calendars to show on your TRMNL.</p>
    {accounts_html}
    <div class="card" style="margin-top:32px;">
      <h3>Terminus Extension Setup</h3>
      <p class="meta">Point your Terminus Extension Exchange at this URL:</p>
      <div class="api-box">http://localhost:{port}/api/events</div>
      <p style="font-size:13px;color:#888;margin-top:12px;">
        In Terminus: Extensions → New Extension → add an Exchange with the URL above,
        then paste the Liquid template from <code>terminus_template/agenda.liquid</code>.
      </p>
    </div>
  </main>
  <script>
    document.addEventListener("change", function(e) {{
      if (!e.target.matches(".cal-toggle")) return;
      const form = document.getElementById("calendars-form");
      const data = new FormData(form);
      fetch("/calendars/update", {{ method: "POST", body: data }});
    }});
  </script>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "4567"))
    app.run(host="127.0.0.1", port=port, debug=False)
