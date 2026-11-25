# AGNDA: Your Daily Agenda for TRMNL

AGNDA is a local Flask companion app that turns your Google Calendars into a
daily agenda for a [TRMNL](https://trmnl.io/) e-ink display. It runs on your own
machine — nothing is pushed to a shared host — and serves a JSON endpoint that a
TRMNL device polls to render `terminus_template/agenda.liquid`.

<img width="480" height="360" alt="a photo of a Kindle showing AGNDA" src="https://github.com/user-attachments/assets/c5fe0352-9114-4c08-8a12-0c521af8ecb7" />


## Quick Start

The full, step-by-step install guide lives in **[SETUP.md](./SETUP.md)**. The
short version:

1. **Prereqs** — macOS or Linux, Python 3.10+ (Apple's system Python is 3.9 and
   too old), and Git.
2. **Clone + venv** — create an isolated `./venv` and install the 8 dependencies
   in `requirements.txt`.
3. **Google OAuth** — create your own OAuth client (do *not* reuse anyone
   else's), set the redirect URI to `http://localhost:4567/oauth/callback`, and
   copy your Client ID/Secret into `.env` (see `.env.example`).
4. **Run** — `./start.sh` sources `.env`, (re)builds `./venv` if needed, and
   launches `http://localhost:4567/setup`.
5. **Connect** — open the setup page, connect a Google account, tick
   "View your calendars", and pick which calendars to show.
6. **Wire to TRMNL** — point a Terminus Extension Exchange at
   `http://localhost:4567/api/events` and paste `terminus_template/agenda.liquid`.

(Optional) install the launchd autostart agent so it survives reboots — see
SETUP.md §6.

## Features

- **Daily agenda rendering** — an 800×480, 1-bit grayscale e-ink screen with a
  next-event hero and a rest-of-day list (Option A), plus an all-done / empty
  state.
- **Multiple Google accounts & calendars** — connect several accounts and pick
  which calendars to display; primary calendars get a badge.
- **Webcal / iCal subscriptions** — paste `webcal://` or `https://` iCal feed
  URLs; recurring events expand automatically.
- **Live `/api/events` endpoint** — JSON the Terminus Extension polls each
  refresh; supports a 5-minute event cache (configurable via
  `CACHE_TTL_MINUTES`).
- **Hidden-event filtering** — titles like "Blocked Time" (shadow events that
  duplicate real ones) are hidden via `HIDDEN_EVENT_TITLES`.
- **Setup dashboard** — a browser UI (`/setup`) to connect accounts, toggle
  calendars, add/remove webcal subscriptions, and remove accounts.
- **Auth-error state** — if Google rejects a stored refresh token, the screen
  shows "Calendar disconnected" with a re-connect prompt instead of a blank
  agenda.
- **Optional macOS autostart** — a launchd agent
  (`launchd/com.trmnl.agenda.plist.template`) keeps the app alive across
  reboots/logins.

## Privacy & data

Your credentials and calendar data stay on your machine. `.env` (your Google
OAuth Client ID/Secret) and `db/` (live OAuth refresh tokens, account emails,
selected calendar IDs, and cached events) are **git-ignored** — never commit
them. The repo ships only `.env.example` with placeholder values, plus the
source code and templates.

## Auto-start on boot

You can have AGNDA launch automatically when your computer boots, so you don't
have to run `./start.sh` by hand each session.

### macOS — launchd

The repo ships a launchd agent template at
`launchd/com.trmnl.agenda.plist.template`. Install it with:

```bash
mkdir -p ~/Library/LaunchAgents ~/Library/Logs
# Fill the two __PLACEHOLDERS__ with real paths and write the agent into place:
sed -e "s#__PROJECT_DIR__#$PWD#g" \
    -e "s#__HOME__#$HOME#g" \
    launchd/com.trmnl.agenda.plist.template \
    > ~/Library/LaunchAgents/com.trmnl.agenda.plist

plutil -lint ~/Library/LaunchAgents/com.trmnl.agenda.plist   # expect "OK"
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.trmnl.agenda.plist
```

Verify it's running:

```bash
launchctl print gui/$(id -u)/com.trmnl.agenda   # look for "state = running"
tail -f ~/Library/Logs/trmnl-agenda.log
```

Stop and remove:

```bash
launchctl bootout gui/$(id -u)/com.trmnl.agenda
rm ~/Library/LaunchAgents/com.trmnl.agenda.plist
```

> **Note — the extra `PATH` key in the plist.** launchd starts agents with the
> minimal `PATH=/usr/bin:/bin`, which on a Homebrew machine resolves `python3`
> to Apple's system 3.9.6 *before* the Homebrew 3.10+ you actually use — enough
> to break `start.sh`'s venv rebuild. The template sets
> `PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin` so `start.sh` finds the
> right interpreter. If the venv already exists and you never trigger a rebuild,
> you can delete the `EnvironmentVariables` block. The legacy `launchctl
> load`/`unload` commands still work but are deprecated in favor of
> `bootstrap`/`bootout`. The plist holds no credentials — those come from
> `.env`, which `start.sh` loads.

### Ubuntu / Linux — systemd

The repo ships a systemd user-unit template at
`systemd/trmnl-agenda.service`. Install it as a **per-user** service (it runs
as your login user, which is right for a local app that owns `.env` and `db/`):

```bash
mkdir -p ~/.config/systemd/user
# Fill __PROJECT_DIR__ with the absolute path to this repo and write the unit:
sed -e "s#__PROJECT_DIR__#$PWD#g" \
    systemd/trmnl-agenda.service \
    > ~/.config/systemd/user/trmnl-agenda.service

systemctl --user daemon-reload
systemctl --user enable --now trmnl-agenda.service
```

Verify it's running:

```bash
systemctl --user status trmnl-agenda.service
journalctl --user -u trmnl-agenda.service -f
```

Stop and disable:

```bash
systemctl --user stop trmnl-agenda.service
systemctl --user disable trmnl-agenda.service
```

> **Note — PATH in the unit.** `start.sh` needs a Python 3.10+ interpreter. On
> Ubuntu the system `python3` is fine, so the default `PATH` works. If you use a
> Homebrew python3 on macOS, point `PATH` at it first so the venv rebuild works.
> The unit holds no credentials — those come from `.env`, which `start.sh`
> loads. If you'd rather run it as a system-wide service, copy the unit to
> `/etc/systemd/system/`, change `WantedBy=default.target` to
> `WantedBy=multi-user.target`, and use `sudo systemctl enable --now
> trmnl-agenda`.

## Overview
A TRMNL plugin screen that renders a **daily agenda** on a Kindle‑sized e‑ink display at the
TRMNL native resolution of **800×480 landscape, 1‑bit / grayscale**. It prominently shows the
**next event** (large time + title + location + duration) and lists the **rest of the day** below
it in a smaller, scannable format. Two visual directions are included plus an **all‑done / empty
state** for each.

The final implementation will run as a TRMNL plugin, which renders an HTML/Liquid template
server‑side and ships a static image to the device. The goal is to reproduce these layouts in
that environment and bind them to live calendar + weather + battery data.

## About the Design Files
The file in this bundle (`Daily Agenda.dc.html`) is a **design reference created in HTML** — a
prototype showing the intended look and layout, **not production code to copy directly**. It uses
a small custom component runtime (`<x-dc>` / `support.js`) purely so it can be previewed; ignore
that wrapper. **Everything meaningful lives in the inline HTML markup and inline `style`
attributes** inside the template — read those for exact structure, sizing, and styling.

Your task is to **recreate these designs in the target environment** — for TRMNL that means a
plugin **HTML + Liquid template** (TRMNL uses the `liquid` templating language and ships an
optional `trmnl.css` framework). If you instead embed this in another stack (React, a static site,
etc.), follow that codebase's existing patterns. Do not ship the `.dc.html` file as‑is.

## Fidelity
**High‑fidelity (hifi).** Colors, typography, spacing, and layout are final and intended to be
reproduced precisely. Recreate the UI pixel‑accurately. The only thing to swap in is **real data**
(events, weather, battery, sync time) in place of the hardcoded sample content.

## e‑ink Constraints (important)
- **Palette is strictly grayscale, high contrast.** Only these values are used:
  - `#000000` — text, rules, hero borders, filled battery
  - `#6b6963` — secondary serif body / de‑emphasized meta (Option B)
  - `#8a8780` — muted labels, secondary text, AM/PM, durations
  - `#b3b0a8` — faint footer text (sync status)
  - `#d8d6cf` — hairline list dividers
  - `#ffffff` — background
- **No gradients, no color, no soft shadows, no anti‑aliased decorative imagery.** All separators
  are solid 1px/3px black or `#d8d6cf` hairlines.
- The `#c9c6bf` border and `box-shadow` on each frame in the prototype are **just the preview
  device bezel** — they are NOT part of the screen. The actual screen is edge‑to‑edge `#ffffff`.

---

## Screens / Views

There are **two design options** (A and B). Pick one to ship — they are alternatives, not both.
Each has a **Default** state and an **All‑done (empty)** state.

### Shared data model (both options)
Each event has: `time` (start), `meridiem` (AM/PM), `title`, `location` (string, or "No location"),
`duration` (e.g. "45 min", "1 hr", "90m"). The screen also needs: `dayOfWeek`, `monthDay`,
`weatherDesc`, `weatherTempF`, `batteryPct`, `syncTime`, and a computed `countdown` to the next
event ("in 25 min").

Sample content used in the mock:
- Header: **Sunday**, **June 21**, weather **72°** (partly cloudy), battery **84%**, synced **9:42 AM**
- Next: **Design Review — Q3 Roadmap** · 10:30–11:15 AM · 45 min · Conference Room B · Zoom · *in 25 min*
- 11:30 AM — **1:1 with Priya** · Zoom · 30m
- 12:30 PM — **Lunch with Marcus** · Café Loup · 1h
- 2:00 PM — **Focus: Spec writing** · No location · 90m
- 4:00 PM — **Team Standup** · Zoom · 15m

---

### Option A — "Minimal grotesque"
Sans‑serif, structural, hard rules. **Recommended for maximum legibility on e‑ink.**

**Frame:** 800×480, `#fff`, `display:flex; flex-direction:column; overflow:hidden`.

**Header** (`padding:18px 28px 16px; border-bottom:3px solid #000`, flex space‑between, align‑end):
- Left: `"Sunday"` IBM Plex Sans 700 / 30px / letter-spacing -.01em, then `"June 21"` IBM Plex Sans 500 / 30px / `#8a8780`, gap 14px, baseline‑aligned.
- Right (flex, gap 22px): sun icon (22px, stroke `#000` 1.8) + `"72°"` IBM Plex Mono 600/19px; then battery icon (26×14, stroke 1.6, filled bar) + `"84%"` IBM Plex Mono 600/14px, gap 7px.

**Body** (`flex:1; display:flex`):
- **Up‑Next hero** — `width:322px; flex:none; border-right:3px solid #000; padding:22px 26px; display:flex; flex-direction:column`:
  - Top row (space‑between): `"UP NEXT"` IBM Plex Mono 700/12px, letter-spacing .16em, uppercase — and a **countdown pill** `"in 25 min"` IBM Plex Mono 600/12px, **white text on solid `#000`, padding 5px 9px** (no border radius).
  - Time block (margin-top 18px, baseline): `"10:30"` IBM Plex Mono **700 / 62px** / line-height .85 / letter-spacing -.02em + `"AM"` IBM Plex Mono 600/20px, gap 8px.
  - Duration line: `"10:30 – 11:15 · 45 min"` IBM Plex Mono 500/14px `#8a8780`, margin-top 8px.
  - Title: `"Design Review — Q3 Roadmap"` IBM Plex Sans **700 / 25px** / line-height 1.15 / `text-wrap:pretty`, margin-top 18px.
  - Location (pushed to bottom via `margin-top:auto`): pin icon (16px) + `"Conference Room B · Zoom"` IBM Plex Sans 500/15px, gap 8px.
- **Rest‑of‑day list** — `flex:1; padding:16px 26px 18px; flex column`:
  - Section label `"LATER TODAY · 4"` IBM Plex Mono 700/12px, letter-spacing .16em, uppercase, `#8a8780`.
  - Each row: `display:flex; align-items:flex-start; gap:16px; padding:13px 0; border-bottom:1px solid #d8d6cf` (last row no border):
    - Time col `width:74px; flex:none`: time IBM Plex Mono 600/17px, meridiem IBM Plex Mono 500/11px `#8a8780` (margin-top 4px).
    - Middle `flex:1; min-width:0`: title IBM Plex Sans 600/18px / line-height 1.15; location IBM Plex Sans 400/13px `#8a8780` (margin-top 3px).
    - Duration `flex:none`: IBM Plex Mono 500/13px `#8a8780`, padding-top 3px.
  - Footer (`margin-top:auto`, space‑between): `"Synced 9:42 AM"` and `"5 events today"`, both IBM Plex Mono 500/11px `#b3b0a8`.

**All‑done state (A):** same header; centered column — sun icon 58px, `"You're all done"` IBM Plex Sans 700/34px, then `"Nothing left on the calendar today. / Next event is tomorrow, 9:00 AM."` IBM Plex Sans 400/17px `#8a8780`. Footer bar (`border-top:1px solid #d8d6cf; padding:14px 28px`): `"Synced 9:42 AM"` / `"5 events completed"`.

---

### Option B — "Editorial serif"
Newspaper masthead, serif headlines. More characterful; slightly denser.

**Frame:** 800×480, `#fff`, flex column, `font-family:'Newsreader',serif`.

**Masthead** (`padding:16px 30px 12px; border-bottom:1px solid #000`):
- Flex space‑between, align‑end: `"THE DAY AHEAD"` IBM Plex Mono 600/13px, letter-spacing .22em, uppercase — and right group (gap 18px) `"Partly cloudy · 72°"` + `"Battery 84%"`, both IBM Plex Mono 500/13px.
- Then a **double rule**: a 3px‑tall spacer div with `border-bottom:1px solid #000; margin:0 30px` directly under the masthead border (classic newspaper double line).

**Body** (`flex:1; display:flex`):
- **Featured (next event)** — `flex:1; padding:18px 30px 20px; border-right:1px solid #000; flex column`:
  - Eyebrow row (space‑between): `"UP NEXT"` IBM Plex Mono 600/12px letter-spacing .16em uppercase, and `"In 25 minutes"` IBM Plex Mono 600/12px.
  - Headline: `"Design Review — Q3 Roadmap"` Newsreader **600 / 40px** / line-height 1.05 / `text-wrap:pretty`, margin-top 14px.
  - Time block (`margin-top:auto`, baseline): `"10:30"` Newsreader **600 / 52px** + `"AM"` Newsreader 500/22px *italic*, gap 14px.
  - Meta (`margin-top:12px; padding-top:12px; border-top:1px solid #d8d6cf`): `"10:30 – 11:15 AM · 45 min"` IBM Plex Mono 500/15px `#000`, then `"Conference Room B · Zoom"` same font `#6b6963` on next line.
- **Agenda rail** — `width:316px; flex:none; padding:16px 28px; flex column`:
  - Label `"REST OF TODAY"` IBM Plex Mono 600/12px letter-spacing .16em uppercase `#6b6963`.
  - Each row: `display:flex; gap:14px; padding:11px 0; border-bottom:1px solid #d8d6cf` (last no border):
    - Time col `width:58px; flex:none`: IBM Plex Mono 600/14px / line-height 1.3, with meridiem on second line in `#8a8780`.
    - Middle `flex:1; min-width:0`: title Newsreader 600/21px / line-height 1.1; sub `"Zoom · 30 min"` IBM Plex Mono 400/13px `#8a8780` (margin-top 3px).
  - Footer (`margin-top:auto`): `"Synced 9:42 AM"` IBM Plex Mono 500/11px `#b3b0a8`.

**All‑done state (B):** same masthead + double rule; centered column — `"— FINAL EDITION —"` IBM Plex Mono 600/12px letter-spacing .22em uppercase `#6b6963`, `"Nothing left on the docket."` Newsreader 600/46px, `"Five events done. Your calendar resumes tomorrow at 9:00 AM."` Newsreader 400/18px *italic* `#6b6963`. Footer `"Synced 9:42 AM"` (border-top `#d8d6cf`, padding 14px 30px).

---

## Interactions & Behavior
E‑ink TRMNL screens are **static, non‑interactive images** refreshed on an interval (typically
every 5–15 min). There are no hover/click/focus states. "Behavior" means **render logic**:
- **Next event** = the soonest event whose start time is ≥ now. It populates the hero/featured block.
- **Rest of today** = remaining events after the next one, in chronological order. The mock shows 4;
  cap the list to what fits (≈4 rows). If more exist, show the first N and optionally a "+ N more" line.
- **Countdown** = humanized delta from now to the next event start ("in 25 min", "in 2 hr"). If the
  event is in progress, show "now". Recompute each refresh.
- **All‑done state** triggers when there are **no remaining events** for the day. Show tomorrow's
  first event time if available.
- **Duration** is derived from start/end (`45 min`, `1 hr`, `90m`). Option A uses the short form
  (`30m`, `1h`, `90m`); Option B uses the long form (`30 min`, `1 hr`).

## State / Data Requirements
Server‑side (TRMNL plugin) data the template needs each refresh:
- `events[]`: `{ start, end, title, location, allDay }` — from the user's calendar (ICS/Google/etc.).
- `now`: device/server time in the user's timezone (drives next‑event selection + countdown).
- `weather`: `{ description, tempF }`.
- `device`: `{ batteryPct, lastSyncTime }` — TRMNL exposes battery/sync via plugin variables.
Derived in template: `nextEvent`, `laterEvents[]`, `countdownLabel`, `isAllDone`, `tomorrowFirst`.

## Design Tokens
**Colors**
| Token | Hex | Use |
|---|---|---|
| ink | `#000000` | primary text, rules, hero border, filled battery |
| serif‑muted | `#6b6963` | secondary serif body (Option B) |
| muted | `#8a8780` | labels, secondary text, AM/PM, durations |
| faint | `#b3b0a8` | footer / sync text |
| hairline | `#d8d6cf` | list dividers, inner borders |
| paper | `#ffffff` | background |

**Typography**
- **IBM Plex Sans** — Option A titles & body. Weights 400/500/600/700.
- **IBM Plex Mono** — times, labels, meta, durations (both options). Weights 400/500/600/700.
- **Newsreader** (serif) — Option B masthead headlines & titles. Weights 400/500/600/700; italic used for AM/PM and empty‑state line.
- Key sizes: hero time A 62px / B 52px; title A 25px / B 40px (headline); list titles 18px (A) / 21px (B); labels 11–13px; meta 13–15px.

**Spacing & rules**
- Frame padding varies per region (header `18px 28px`, hero `22px 26px`, list `16px 26px`, masthead `16px 30px`).
- Header underline **3px solid `#000`** (A); masthead **1px + double rule** (B). Hero/featured divider **3px** (A) / **1px** (B). List dividers **1px `#d8d6cf`**.
- Countdown pill (A): solid black, **no radius**, padding `5px 9px`. No rounded corners anywhere — keep it crisp for e‑ink.
- Line spacing: tight (.85–1.15) on display type; 1.2–1.4 on body.

## Assets
- **Icons are inline SVG** (no external files): sun (header weather + empty state), battery, map pin.
  All single‑color black strokes — recreate as inline SVG or your icon system's equivalents. Keep
  stroke‑based, ~1.6–1.9px stroke, no fills except the battery level bar.
- **Fonts**: Google Fonts — IBM Plex Sans, IBM Plex Mono, Newsreader. For a TRMNL plugin, prefer
  self‑hosting/`@font-face` since the device renders server‑side; verify they rasterize cleanly at
  1‑bit. If you want to match TRMNL's stock look, their framework ships a default bitmap font you
  can substitute.

## Files
- `Daily Agenda.dc.html` — the HTML design reference containing **both options** (A and B) and
  **both states** (default + all‑done), laid out as labeled 800×480 frames. Open it in a browser to
  inspect exact markup and inline styles. The outer gray wrapper and per‑frame borders/shadows are
  preview scaffolding only — the real screen is the inner white 800×480 area.

## Suggested first prompt for Claude Code
> "Read `README.md` and `Daily Agenda.dc.html`. I'm building a TRMNL
> plugin (HTML + Liquid). Scaffold the plugin and implement **Option A** at 800×480, 1‑bit grayscale,
> binding the next‑event hero and the rest‑of‑day list to my calendar data, plus the weather/battery
> header and the all‑done empty state. Match the typography, rules, and spacing in the README exactly."
