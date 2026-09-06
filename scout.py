"""Waiver scout — no accounts, no API keys.

Modes:
  pulse   one pass, diff against saved state, alert on what changed
  loop    poll repeatedly (gameday); state written once at the end
  digest  daily summary, no diffing
  check   verify both data sources and Telegram, then exit
"""

import argparse
import json
import pathlib
import sys
import time
from datetime import datetime, timezone

import notify
import sources

ROOT = pathlib.Path(__file__).parent
STATE_PATH = ROOT / "state.json"
WATCHLIST_PATH = ROOT / "watchlist.json"

TIER_WEIGHT = {"must_add": 3, "priority": 2, "stash": 1}


def load_json(path, fallback):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def save_state(state):
    state["updated"] = datetime.now(timezone.utc).isoformat()
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def watched(watchlist):
    """[(name, tier, weight)] flattened from the watchlist file."""
    out = []
    for tier, names in (watchlist.get("tiers") or {}).items():
        for n in names:
            out.append((n, tier, TIER_WEIGHT.get(tier, 1)))
    return out


# ---------------------------------------------------------------------------
# detectors
# ---------------------------------------------------------------------------

def check_injuries(players, watchlist, previous):
    """Injury designation changes on anyone you're tracking."""
    alerts, current = [], {}
    names = watched(watchlist)

    for pid, p in players.items():
        hit = next((t for n, t, _ in names if sources.mentions(p["name"], n)
                    and sources.normalize(n) in sources.normalize(p["name"])), None)
        if not hit:
            continue
        current[pid] = p["injury"]
        was = previous.get(pid)
        if was is None or was == p["injury"]:
            continue

        old, new = sources.SEVERITY.get(was, 0), sources.SEVERITY.get(p["injury"], 0)
        if new == old:
            continue
        worse = new > old
        icon = "\u26a0\ufe0f" if worse else "\u2705"
        change = f"{was or 'healthy'} \u2192 {p['injury'] or 'healthy'}"
        alerts.append({
            "kind": "injury",
            "urgent": worse and new >= 4,
            "text": (
                f"{icon} <b>{notify.esc(p['name'])}</b> "
                f"({notify.esc(p['pos'])} {notify.esc(p['team'])}) {notify.esc(change)}"
                + (f"\n<i>{notify.esc(p['note'])}</i>" if p["note"] else "")
                + f"\n<i>on your {hit.replace('_', ' ')} list</i>"
            ),
        })
    return alerts, current


def check_news(items, players, watchlist, seen_ids):
    """Fresh headlines naming a watchlist player."""
    alerts = []
    names = watched(watchlist)

    for item in items:
        if item["id"] in seen_ids:
            continue
        blob = f"{item['title']} {item['summary']}"
        tags = sources.classify(blob)

        for name, tier, _ in names:
            if not sources.mentions(blob, name):
                continue
            label = "".join(f" [{t}]" for t in tags)
            alerts.append({
                "kind": "news",
                "urgent": "injury" in tags or tier == "must_add",
                "text": (
                    f"\U0001f4f0 <b>{notify.esc(name)}</b>{notify.esc(label)}\n"
                    f"{notify.esc(item['title'])}"
                    + (f"\n{notify.esc(item['link'])}" if item["link"] else "")
                ),
            })
            break  # one alert per headline
    return alerts


def check_momentum(trend, players, watchlist, previous):
    """Players the wider fantasy market is piling into.

    Two signals: someone on your list starts trending at all, or a player
    climbs sharply relative to the last check. The second is how you catch a
    breakout you hadn't thought to write down.
    """
    alerts, current = [], {}
    names = watched(watchlist)
    ranks = {row["player_id"]: i for i, row in enumerate(trend)}

    for i, row in enumerate(trend):
        pid, count = row.get("player_id"), int(row.get("count") or 0)
        if not pid:
            continue
        current[pid] = count
        p = players.get(pid)
        if not p:
            continue

        was = previous.get(pid)
        listed = next((t for n, t, _ in names
                       if sources.normalize(n) in sources.normalize(p["name"])), None)

        reason = None
        if listed and was is None:
            reason = f"on your {listed.replace('_', ' ')} list, now trending"
        elif was is None and i < 8 and count >= 300:
            reason = f"new to the top 10 ({count:,} adds)"
        elif was and count >= was * 2 and count >= 200:
            reason = f"adds doubled to {count:,}"

        if not reason:
            continue

        hurt = f" \u2014 currently {p['injury']}" if p["injury"] else ""
        alerts.append({
            "kind": "momentum",
            "urgent": bool(listed),
            "rank": i,
            "text": (
                f"\U0001f4c8 <b>{notify.esc(p['name'])}</b> "
                f"({notify.esc(p['pos'])} {notify.esc(p['team'])}){notify.esc(hurt)}\n"
                f"<i>{notify.esc(reason)}</i>"
            ),
        })
    return alerts, current


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------

def run_pulse(state, watchlist, player_ttl=21600, quiet=False):
    players = sources.players(max_age_seconds=player_ttl)
    trend = sources.trending("add", hours=24, limit=50)
    try:
        news = sources.headlines(limit=40)
    except sources.SourceError as exc:
        print(f"[news] unavailable: {exc}")
        news = []

    alerts = []
    inj, inj_state = check_injuries(players, watchlist, state.get("injuries", {}))
    alerts += inj
    alerts += check_news(news, players, watchlist, set(state.get("news_ids", [])))
    mom, mom_state = check_momentum(trend, players, watchlist,
                                    state.get("trending", {}))
    alerts += mom

    state["injuries"] = inj_state
    state["trending"] = mom_state
    state["news_ids"] = ([i["id"] for i in news]
                         + state.get("news_ids", []))[:400]

    if alerts and not quiet:
        urgent = [a for a in alerts if a.get("urgent")]
        head = ("\U0001f6a8 <b>Waiver alert</b>" if urgent
                else "\U0001f4cb <b>Wire update</b>")
        notify.send(f"{head}\n\n" + "\n\n".join(a["text"] for a in alerts),
                    silent=not urgent)

    return alerts


def run_digest(watchlist):
    players = sources.players(max_age_seconds=1800)
    trend = sources.trending("add", hours=24, limit=25)
    drops = sources.trending("drop", hours=24, limit=10)

    lines = [f"\U0001f4c5 <b>Wire brief</b> \u2014 {datetime.now(timezone.utc):%a %d %b}"]

    hurt = []
    for name, tier, _ in watched(watchlist):
        for p in players.values():
            if sources.normalize(name) in sources.normalize(p["name"]) and p["injury"]:
                hurt.append((p, tier))
                break
    if hurt:
        lines.append("\n<b>Watchlist injury flags</b>")
        for p, tier in sorted(hurt, key=lambda x: -sources.SEVERITY.get(x[0]["injury"], 0)):
            lines.append(f"\u2022 {notify.esc(p['name'])} ({notify.esc(p['pos'])}) "
                         f"\u2014 <b>{notify.esc(p['injury'])}</b>")

    if trend:
        lines.append("\n<b>Most added, last 24h</b>")
        for row in trend[:10]:
            p = players.get(row["player_id"])
            if not p:
                continue
            flag = f" ({notify.esc(p['injury'])})" if p["injury"] else ""
            lines.append(
                f"\u2022 <b>{notify.esc(p['name'])}</b> "
                f"{notify.esc(p['pos'])} {notify.esc(p['team'])}{flag} "
                f"\u00b7 {int(row.get('count') or 0):,}"
            )

    if drops:
        names = [players[r["player_id"]]["name"]
                 for r in drops[:5] if r["player_id"] in players]
        if names:
            lines.append("\n<b>Most dropped</b>\n"
                         + notify.esc(", ".join(names)))

    lines.append("\n<i>Trending data from Sleeper.</i>")
    notify.send("\n".join(lines), silent=True)
    return lines


def run_loop(state, watchlist, interval, duration):
    deadline = time.time() + duration
    passes = 0
    while time.time() < deadline:
        started = time.time()
        try:
            # Player map refreshes less often than the loop spins; news and
            # trending are small and safe to poll every pass.
            alerts = run_pulse(state, watchlist, player_ttl=900)
            passes += 1
            if alerts:
                print(f"[loop] pass {passes}: {len(alerts)} alert(s)")
        except Exception as exc:
            print(f"[loop] {type(exc).__name__}: {exc}")
        time.sleep(max(10, interval - (time.time() - started)))
    print(f"[loop] done after {passes} passes")
    return state


def run_check():
    ok = True
    print("\nChecking sources\n")

    try:
        p = sources.players(force=True)
        print(f"  OK        Sleeper players ({len(p):,} active skill players)")
        sample = next(iter(p.values()))
        print(f"            e.g. {sample['name']} ({sample['pos']} {sample['team']})")
    except Exception as exc:
        print(f"  FAILED    Sleeper players: {exc}")
        ok = False

    try:
        t = sources.trending("add", hours=24, limit=10)
        print(f"  OK        Sleeper trending ({len(t)} rows)")
    except Exception as exc:
        print(f"  FAILED    Sleeper trending: {exc}")
        ok = False

    try:
        h = sources.headlines(limit=5)
        print(f"  OK        ESPN headlines ({len(h)} items)")
        if h:
            print(f"            latest: {h[0]['title'][:60]}")
    except Exception as exc:
        print(f"  FAILED    ESPN headlines: {exc}")
        print("            Feeds move occasionally; edit ESPN_FEEDS in sources.py")
        ok = False

    wl = load_json(WATCHLIST_PATH, {})
    n = len(watched(wl))
    print(f"  {'OK' if n else 'EMPTY':9} watchlist ({n} players)")

    try:
        sent = notify.send("\u2705 <b>Waiver scout connected.</b>\n"
                           "If you can read this, setup worked.")
        print(f"  {'OK' if sent else 'FAILED':9} Telegram")
        ok = ok and sent
    except Exception as exc:
        print(f"  FAILED    Telegram: {exc}")
        ok = False

    print("\n" + ("All good.\n" if ok else "Fix the above, then re-run.\n"))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="pulse",
                    choices=["pulse", "loop", "digest", "check"])
    ap.add_argument("--interval", type=int, default=90)
    ap.add_argument("--duration", type=int, default=3300)
    args = ap.parse_args()

    if args.mode == "check":
        sys.exit(0 if run_check() else 1)

    watchlist = load_json(WATCHLIST_PATH, {"tiers": {}})
    if not watched(watchlist):
        print("watchlist.json is empty — nothing to watch.")
        return

    state = load_json(STATE_PATH, {})

    if args.mode == "digest":
        run_digest(watchlist)
        return

    if args.mode == "loop":
        state = run_loop(state, watchlist, args.interval, args.duration)
    else:
        run_pulse(state, watchlist)

    save_state(state)


if __name__ == "__main__":
    main()
