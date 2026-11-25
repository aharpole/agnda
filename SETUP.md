# AGNDA - install & run

AGNDA is a local Flask companion app. It polls your Google Calendars and serves a
`/api/events` JSON endpoint for a [TRMNL](https://trmnl.io/) device running the
[Terminus Extension](https://github.com/terminus-trmnl/terminus-extension), which
renders `terminus_template/agenda.liquid` on an 800x480 e-ink display. It runs on
your own computer - nothing is pushed to a shared host.

## 1. Pre-flight

This was developed on a macOS machine. It should work just fine on Linux machines too, just make sure you have a good Python environment set up.

- **macOS with Python 3.10 or newer.** This is a hard minimum - `icalendar` and
  the Google API libraries carry `Requires-Python: >=3.10`, so `pip` will refuse
  to install them on an older interpreter. Important gotcha: **Apple's built-in
  system Python is 3.9.6 and is too old.** It lives at `/usr/bin/python3`
  (shipped with the Xcode Command Line Tools). Install a modern Python with
  Homebrew instead: `brew install python` (gets the current 3.x on Apple silicon
  at `/opt/homebrew/bin/python3`), or use `pyenv`. Verify with
  `python3 --version` before continuing - you want 3.10 or newer, **not 3.9**.
- **Git** to clone the repo.
- **A TRMNL device (or a DIY TRMNL alternative)** with the Terminus Extension app (this is what displays the
  agenda).
- **A Google account** and a **Google Cloud project** with your own OAuth 2.0
  client (created in step 4).

AGNDA has eight third-party dependencies, listed in `requirements.txt` (Flask,
google-auth, google-auth-oauthlib, google-api-python-client, python-dotenv,
requests, icalendar, recurring-ical-events). **Install them into an isolated
virtual environment - never into your system Python.** A global
`pip install -r requirements.txt` pollutes system packages and can collide with
other tools, and on a stock Mac it can't succeed anyway (`/usr/bin/python3` is
3.9.6). `./start.sh` handles this automatically: it builds `./venv` if it is
missing and reuses it otherwise. Step 2 shows the same setup explicitly.

## 2. Create a Python virtualenv

Keep AGNDA's dependencies out of your system Python by running everything from a
virtualenv:

```bash
git clone <your-repo-url> trmnl_agenda && cd trmnl_agenda

# Make sure `python3` is your 3.10+ interpreter, not Apple's 3.9.6.
python3 --version              # expect 3.10 or newer

# Create the venv at ./venv and install the dependencies INTO it.
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Run the app from the venv:

```bash
./venv/bin/python3 app.py
```

...or activate it and use bare `python`/`pip` for the session:

```bash
source venv/activate
python app.py                 # equivalent to the call above
```

- **`venv/` is git-ignored** - never commit it. It is just the installed
  packages; anyone can regenerate it from `requirements.txt`.
- **`./start.sh` automates both of the commands above** whenever `venv/` is
  missing or corrupted, so you rarely need to type them. Use them manually if
  you'd rather control the interpreter, or if you want to inspect what lands in
  `venv/` before running anything.

## 3. Configure `.env` and launch

```bash
cp .env.example .env
# Edit .env:
#   GOOGLE_CLIENT_ID        -> from your own OAuth client (step 4)
#   GOOGLE_CLIENT_SECRET    -> from your own OAuth client (step 4)
#   PORT=4567               -> must match the OAuth redirect URI
#   TIMEZONE=America/Los_Angeles   -> your IANA timezone
./start.sh   # sources .env, (re)builds ./venv if needed, launches http://localhost:4567/setup
```

`start.sh` exits early (and refuses to run) if `.env` is missing, so create it
first. After it starts, open <http://localhost:4567/setup> in your browser and
continue below.

## 4. Connect a Google account

1. Create your own OAuth client -- **do not reuse someone else's**:
   - Go to <https://console.cloud.google.com/> and create or pick a project.
   - **APIs & Services -> Credentials -> Create Credentials -> OAuth 2.0 Client ID**
   - Application type: **Web application**.
   - Authorized redirect URI: `http://localhost:4567/oauth/callback`
     (use whatever `PORT` you set in `.env`).
   - Also enable the **Google Calendar API**.
   - Paste the **Client ID** and **Client Secret** into `.env`.
2. Open <http://localhost:4567/setup> and click **Connect Google Account**.
3. On Google's consent screen **tick "View your calendars"** - it is unchecked by
   default, and a token granted without the calendar scope is useless to AGNDA.
4. Pick the calendars you want shown; the setup page saves them automatically.

## 5. Point Terminus at it

In the Terminus Extension app, add a new **Extension -> Exchange** whose URL is

```
http://localhost:4567/api/events
```

then paste the contents of `terminus_template/agenda.liquid` as that extension's
Liquid template. The device now renders your daily agenda.

## 6. (Optional) Autostart across reboots - macOS LaunchAgent

Running `./start.sh` in a Terminal keeps AGNDA alive only for that session. To
make it survive reboots and logins, install the launchd agent that ships in
`launchd/com.trmnl.agenda.plist.template`:

```bash
mkdir -p ~/Library/LaunchAgents ~/Library/Logs

# Fill the two __PLACEHOLDERS__ with real paths and write the agent into place:
sed -e "s#__PROJECT_DIR__#$PWD#g" \
    -e "s#__HOME__#$HOME#g" \
    launchd/com.trmnl.agenda.plist.template \
    > ~/Library/LaunchAgents/com.trmnl.agenda.plist

plutil -lint ~/Library/LaunchAgents/com.trmnl.agenda.plist    # expect "OK"
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.trmnl.agenda.plist
```

Verify it is up:

```bash
launchctl print gui/$(id -u)/com.trmnl.agenda     # look for "state = running"
tail -f ~/Library/Logs/trmnl-agenda.log
```

Stop and remove:

```bash
launchctl bootout gui/$(id -u)/com.trmnl.agenda
rm ~/Library/LaunchAgents/com.trmnl.agenda.plist
```

> **Note - the extra `PATH` key in the plist.** launchd starts agents with the
> minimal `PATH=/usr/bin:/bin`, which on a Homebrew machine resolves `python3` to
> Apple's system 3.9.6 *before* the Homebrew 3.10+ you actually use - enough to
> break `start.sh`'s venv rebuild. The template sets
> `PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin` so `start.sh` finds the
> right interpreter. If the venv already exists and you never trigger a rebuild,
> you can delete the `EnvironmentVariables` block. The legacy
> `launchctl load`/`unload` commands still work but are deprecated in favor of
> `bootstrap`/`bootout`. The plist holds no credentials - those come from
> `.env`, which `start.sh` loads.
