"""Offline tests. No network, no accounts.

    python test_local.py
"""

import json
import pathlib
import sys
import time

import notify
import scout
import sources

ROOT = pathlib.Path(__file__).parent
FAILS = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not cond:
        FAILS.append(label)


WATCHLIST = {"tiers": {
    "must_add": ["MarShawn Lloyd"],
    "priority": ["Rico Dowdle", "Travis Etienne Jr."],
    "stash": ["Ja'Marr Chase"],
}}

PLAYERS = {
    "100": {"name": "MarShawn Lloyd", "pos": "RB", "team": "GB",
            "injury": "", "note": "", "depth": 2},
    "200": {"name": "Rico Dowdle", "pos": "RB", "team": "PIT",
            "injury": "Questionable", "note": "ankle", "depth": 1},
    "300": {"name": "Ja'Marr Chase", "pos": "WR", "team": "CIN",
            "injury": "", "note": "", "depth": 1},
    "400": {"name": "Some Rookie", "pos": "WR", "team": "NYJ",
            "injury": "", "note": "", "depth": 3},
    "500": {"name": "Travis Etienne Jr.", "pos": "RB", "team": "NO",
            "injury": "", "note": "", "depth": 1},
}

print("\nName normalization")
check("apostrophes folded", sources.normalize("Ja'Marr Chase") == "jamarr chase",
      sources.normalize("Ja'Marr Chase"))
check("suffix stripped", sources.normalize("Travis Etienne Jr.") == "travis etienne",
      sources.normalize("Travis Etienne Jr."))
check("accents folded", sources.normalize("Amon-Ra St. Brown") == "amonra st brown",
      sources.normalize("Amon-Ra St. Brown"))
check("empty is safe", sources.normalize(None) == "")

print("\nHeadline matching")
check("full name matches",
      sources.mentions("Report: MarShawn Lloyd to start Sunday", "MarShawn Lloyd"))
check("apostrophe variant matches",
      sources.mentions("JaMarr Chase limited in practice", "Ja'Marr Chase"))
check("suffix variant matches",
      sources.mentions("Travis Etienne ruled out", "Travis Etienne Jr."))
check("distinctive surname matches",
      sources.mentions("Dowdle takes over backfield", "Rico Dowdle"))
check("unrelated headline does not match",
      not sources.mentions("Chiefs win in overtime", "MarShawn Lloyd"))
check("short common surname does not fire",
      not sources.mentions("Smith had a big day", "Devin Smith"))

print("\nHeadline classification")
check("injury words tagged", "injury" in sources.classify("Ruled out with a hamstring strain"))
check("opportunity words tagged",
      "opportunity" in sources.classify("Named the starter, will lead the backfield"))
check("both can fire", set(sources.classify("Starter placed on IR")) == {"injury", "opportunity"})
check("neutral headline untagged", sources.classify("Team unveils new uniforms") == [])

print("\nWatchlist flattening")
flat = scout.watched(WATCHLIST)
check("all tiers flattened", len(flat) == 4, str(len(flat)))
check("weights applied", dict((t, w) for _, t, w in flat)["must_add"] == 3)

print("\nInjury diffing")
prev = {"200": "", "100": ""}
alerts, state = scout.check_injuries(PLAYERS, WATCHLIST, prev)
check("new designation alerts", len(alerts) == 1, f"got {len(alerts)}")
check("names the right player", alerts and "Rico Dowdle" in alerts[0]["text"])
check("Questionable is not urgent", alerts and not alerts[0]["urgent"])
check("unwatched player ignored", "400" not in state)
check("state records watchlist players", set(state) == {"100", "200", "300", "500"},
      str(sorted(state)))

worse = dict(PLAYERS)
worse["200"] = {**PLAYERS["200"], "injury": "Out"}
al2, _ = scout.check_injuries(worse, WATCHLIST, {"200": "Questionable"})
check("escalation to Out is urgent", al2 and al2[0]["urgent"])

better = dict(PLAYERS)
better["200"] = {**PLAYERS["200"], "injury": ""}
al3, _ = scout.check_injuries(better, WATCHLIST, {"200": "Out"})
check("recovery alerts but not urgently", al3 and not al3[0]["urgent"])
check("recovery uses the tick icon", al3 and "\u2705" in al3[0]["text"])

noprev, _ = scout.check_injuries(PLAYERS, WATCHLIST, {})
check("first run stays silent", noprev == [])

print("\nNews detection")
news = [
    {"id": "n1", "title": "MarShawn Lloyd ruled out with a groin injury",
     "summary": "", "link": "https://x", "published": ""},
    {"id": "n2", "title": "Cowboys sign a punter", "summary": "", "link": "", "published": ""},
]
na = scout.check_news(news, PLAYERS, WATCHLIST, set())
check("watchlist headline alerts", len(na) == 1, f"got {len(na)}")
check("injury headline is urgent", na and na[0]["urgent"])
check("irrelevant headline ignored", na and "punter" not in na[0]["text"])
check("already-seen headline skipped", scout.check_news(news, PLAYERS, WATCHLIST, {"n1"}) == [])

print("\nMomentum detection")
trend = [
    {"player_id": "100", "count": 4200},
    {"player_id": "400", "count": 900},
    {"player_id": "300", "count": 120},
]
ma, mstate = scout.check_momentum(trend, PLAYERS, WATCHLIST, {})
kinds = [a["text"] for a in ma]
check("watchlist player trending alerts", any("MarShawn Lloyd" in t for t in kinds))
check("unlisted breakout in top 10 alerts", any("Some Rookie" in t for t in kinds))
check("watchlist momentum is urgent",
      any(a["urgent"] for a in ma if "MarShawn Lloyd" in a["text"]))
check("state captures counts", mstate.get("100") == 4200)

doubled, _ = scout.check_momentum(
    [{"player_id": "400", "count": 900}], PLAYERS, WATCHLIST, {"400": 400})
check("doubling adds fires", doubled and "doubled" in doubled[0]["text"])

flat_trend, _ = scout.check_momentum(
    [{"player_id": "400", "count": 420}], PLAYERS, WATCHLIST, {"400": 400})
check("mild growth stays quiet", flat_trend == [])

unknown, _ = scout.check_momentum(
    [{"player_id": "999", "count": 5000}], PLAYERS, WATCHLIST, {})
check("unknown player id ignored", unknown == [])

print("\nEnd to end with stubbed sources")
_p, _t, _h, _s = (sources.players, sources.trending,
                  sources.headlines, notify.send)
sources.players = lambda **k: PLAYERS
sources.trending = lambda kind="add", hours=24, limit=50: (
    trend if kind == "add" else [{"player_id": "300", "count": 80}])
sources.headlines = lambda limit=40: news
sent = []
notify.send = lambda text, **k: sent.append(text) or True
try:
    scout.WATCHLIST_PATH = ROOT / "watchlist.json"
    st = {}
    a1 = scout.run_pulse(st, WATCHLIST, quiet=True)
    check("first pulse produces momentum + news only",
          all(x["kind"] != "injury" for x in a1))
    check("state persisted after pulse", "trending" in st and "news_ids" in st)

    st2 = {"injuries": {"200": ""}, "trending": {"100": 4200}, "news_ids": ["n1"]}
    a2 = scout.run_pulse(st2, WATCHLIST, quiet=True)
    check("second pulse catches the injury change",
          any(x["kind"] == "injury" for x in a2))
    check("second pulse suppresses the seen headline",
          not any(x["kind"] == "news" for x in a2))

    sent.clear()
    scout.run_digest(WATCHLIST)
    check("digest sends one message", len(sent) == 1)
    check("digest lists most added", sent and "Most added" in sent[0])
    check("digest credits Sleeper", sent and "Sleeper" in sent[0])
finally:
    sources.players, sources.trending, sources.headlines, notify.send = _p, _t, _h, _s

print("\nRSS parsing")
xml = b"""<?xml version="1.0"?><rss><channel>
<item><title>Player X carted off</title><link>https://e/1</link>
<guid>g1</guid><description>More here</description></item>
<item><title></title><link>https://e/2</link></item>
</channel></rss>"""


class FakeResp:
    content = xml


_get = sources._get
sources._get = lambda *a, **k: FakeResp()
try:
    items = sources.headlines()
    check("parses items", len(items) == 1, f"got {len(items)}")
    check("uses guid as id", items and items[0]["id"] == "g1")
    check("skips empty titles", all(i["title"] for i in items))
finally:
    sources._get = _get

print("\nCache TTL")
cache_backup = sources.CACHE
sources.CACHE = pathlib.Path("/tmp/_players_cache_test.json")
try:
    sources.CACHE.write_text(json.dumps({"fetched": time.time(), "players": PLAYERS}))
    check("fresh cache is reused", sources.players(max_age_seconds=3600) == PLAYERS)
    sources.CACHE.write_text(json.dumps({"fetched": 0, "players": PLAYERS}))
    stale = False
    try:
        sources.players(max_age_seconds=60)
    except Exception:
        stale = True  # would have hit the network
    check("stale cache triggers a refetch", stale)
    sources.CACHE.write_text("not json")
    corrupt = False
    try:
        sources.players(max_age_seconds=3600)
    except Exception:
        corrupt = True
    check("corrupt cache triggers a refetch", corrupt)
finally:
    sources.CACHE.unlink(missing_ok=True)
    sources.CACHE = cache_backup

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All tests passed.")
