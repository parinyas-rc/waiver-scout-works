# Waiver scout

Pushes a Telegram alert when something on your watchlist moves. No Yahoo app,
no OAuth, no API keys — the only account you need is Telegram.

It watches three public sources and tells you when:

- **A watchlist player's injury designation changes** — healthy to Questionable
  to Out, or the reverse
- **A watchlist player appears in breaking NFL news** — tagged by whether the
  headline is about injury or opportunity
- **The fantasy market piles into someone** — Sleeper publishes what its entire
  user base is adding, which moves well before your league reacts

That third one is the interesting bit. It catches breakouts you never thought
to write down: any player entering the top 10 most-added, or whose add count
doubles between checks, gets flagged even if you've never heard of him.

**What it can't see:** your actual roster, or when someone in your league drops
a player. Both need Yahoo authentication. If you decide you want them later,
that version is written and ready — this one is the three-minute path.

---

## Setup

### 1. Telegram bot

1. Message [@BotFather](https://t.me/BotFather) and send `/newbot`
2. Follow the prompts and copy the token
3. **Send your new bot any message** — it can't message you first
4. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` and copy
   `result[0].message.chat.id`

### 2. Push to GitHub, add two secrets

Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `TELEGRAM_TOKEN` | from step 1 |
| `TELEGRAM_CHAT_ID` | from step 1 |

### 3. Confirm it works

Actions → **pulse** → Run workflow. Or locally:

```bash
pip install -r requirements.txt
TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... python scout.py --mode check
```

`--mode check` hits both data sources, reports how many players and headlines
came back, and sends a test message. Run it any time something seems off.

Make the repo **public** — scheduled Actions minutes are unlimited on public
repos and capped at 2,000/month on private ones. The gameday polling alone
would exhaust that cap in about three weeks. Nothing sensitive is stored:
credentials live in Secrets and `state.json` is just IDs and injury strings.

---

## The watchlist

`watchlist.json` is the file that matters. Three tiers:

```json
{
  "tiers": {
    "must_add":  ["MarShawn Lloyd"],
    "priority":  ["Rico Dowdle", "Jaylen Warren"],
    "stash":     ["Carnell Tate"]
  }
}
```

Tier affects urgency: `must_add` players trigger a loud notification for any
news at all, the others only for injuries. Names are matched loosely —
apostrophes, accents and `Jr.`/`III` suffixes are all normalised, so
`Ja'Marr Chase` matches a headline reading `JaMarr Chase`.

Surname-only matching is deliberately conservative: it needs six or more
characters, so "Dowdle" matches but "Smith" never will.

Update this weekly. The bot handles speed; you handle judgement.

---

## Schedules

| Workflow | When | Latency |
|---|---|---|
| `pulse` | every 15 min, always | 15-25 min |
| `gameday` | hourly during game windows, polls every 90s inside the job | ~90 sec |
| `digest` | 12:00 UTC daily | n/a |

`gameday` exists because GitHub's cron floor is five minutes and scheduled runs
get delayed under load. Starting on the hour and looping internally gets you
90-second latency in the window where it matters.

---

## Rate limits and manners

Sleeper asks that the full player map not be pulled more than once a day. This
respects that: player data is position-filtered (QB/RB/WR/TE, active only),
cached to disk, and refreshed at most every 6 hours in normal operation, or
every 15 minutes during gameday loops. The small endpoints — trending and RSS —
are what get polled frequently.

Trending data comes from Sleeper and the digest credits them. ESPN's RSS terms
require linking back to the source article, which the news alerts do.

---

## Known limits

- **RSS feeds move.** If `--mode check` reports the ESPN feed failing, edit
  `ESPN_FEEDS` at the top of `sources.py`; it tries each in order.
- **Scheduled workflows are disabled after 60 days of repo inactivity.**
  Committing `state.json` each run keeps it alive, but glance at it in November.
- The first run of each detector stays silent by design — it records a baseline
  rather than alerting on everything at once. Silence on run one is correct.
- Headline matching is keyword-based, not semantic. It will occasionally fire
  on a player mentioned in passing. Tighten `INJURY_WORDS` in `sources.py` if
  the noise annoys you.
- Sleeper's trending counts reflect Sleeper's user base, not your Yahoo league.
  It's a leading indicator of the wider market, which is what you want, but a
  player trending there may already be rostered in your 12-teamer.

Run `python test_local.py` after any edit — 45 checks across parsing, matching,
diffing and alerting, no network needed.
