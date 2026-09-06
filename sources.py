"""Public data sources. No API keys, no OAuth, no accounts.

Sleeper's fantasy API is open and read-only. ESPN publishes NFL headlines over
RSS. Between them you get injury designations, league-wide add/drop momentum,
and breaking news, which covers most of what a waiver alert needs.

Sleeper asks that the full player map not be pulled more than once a day, so
everything here is cached on disk with a TTL and position-filtered to keep
responses small. Trending data is theirs; credit them if you publish anything
built on it.
"""

import json
import pathlib
import re
import time
import unicodedata
import xml.etree.ElementTree as ET

import requests

ROOT = pathlib.Path(__file__).parent
CACHE = ROOT / "players_cache.json"

SLEEPER = "https://api.sleeper.app/v1"
ESPN_FEEDS = [
    "https://www.espn.com/espn/rss/nfl/news",
    "https://www.espn.com/espn/rss/news",
]

POSITIONS = ("QB", "RB", "WR", "TE")
UA = {"User-Agent": "waiver-scout/1.0 (personal fantasy tool)"}

# Sleeper injury_status values, worst last.
SEVERITY = {
    None: 0, "": 0, "Probable": 1, "Questionable": 2, "Doubtful": 3,
    "Out": 4, "IR": 5, "PUP": 5, "Sus": 5, "NA": 4, "DNR": 5, "COV": 3,
}


class SourceError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# name handling
# ---------------------------------------------------------------------------

def normalize(name):
    """Fold accents, drop punctuation and suffixes, lowercase.

    'Ja'Marr Chase' and 'Travis Etienne Jr.' need to match text written a
    dozen different ways across feeds.
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def mentions(text, name):
    """Is this player named in this blob of text? Full name or surname."""
    t, n = normalize(text), normalize(name)
    if not t or not n:
        return False
    if n in t:
        return True
    parts = n.split()
    # Surnames only if distinctive enough to avoid false hits on 'Smith'.
    return len(parts) > 1 and len(parts[-1]) >= 6 and parts[-1] in t


# ---------------------------------------------------------------------------
# http
# ---------------------------------------------------------------------------

def _get(url, params=None, timeout=30, retries=3):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code == 200:
                return r
            last = f"[{r.status_code}]"
            if r.status_code in (429, 500, 502, 503):
                time.sleep(2 ** attempt)
                continue
            raise SourceError(f"GET {url} -> {last}")
        except requests.RequestException as exc:
            last = str(exc)
            time.sleep(2 ** attempt)
    raise SourceError(f"GET {url} failed: {last}")


# ---------------------------------------------------------------------------
# sleeper
# ---------------------------------------------------------------------------

def players(max_age_seconds=1800, force=False):
    """Slim map of active skill-position players, cached on disk.

    Returns {player_id: {name, pos, team, injury, note, depth}}.
    """
    if not force and CACHE.exists():
        try:
            blob = json.loads(CACHE.read_text())
            if time.time() - blob.get("fetched", 0) < max_age_seconds:
                return blob["players"]
        except (json.JSONDecodeError, KeyError):
            pass

    out = {}
    for pos in POSITIONS:
        r = _get(f"{SLEEPER}/players/nfl",
                 params={"position": pos, "active": "true"}, timeout=60)
        for pid, p in (r.json() or {}).items():
            if not isinstance(p, dict):
                continue
            name = p.get("full_name") or " ".join(
                filter(None, [p.get("first_name"), p.get("last_name")]))
            if not name:
                continue
            out[pid] = {
                "name": name,
                "pos": p.get("position") or pos,
                "team": p.get("team") or "FA",
                "injury": p.get("injury_status") or "",
                "note": (p.get("injury_notes") or p.get("injury_body_part") or ""),
                "depth": p.get("depth_chart_order"),
            }

    CACHE.write_text(json.dumps(
        {"fetched": time.time(), "players": out}, separators=(",", ":")))
    return out


def trending(kind="add", hours=24, limit=50):
    """League-wide add/drop momentum across all Sleeper leagues.

    Returns [{player_id, count}] ordered most-active first. This is the
    fastest public read on what the fantasy market is reacting to.
    """
    r = _get(f"{SLEEPER}/players/nfl/trending/{kind}",
             params={"lookback_hours": hours, "limit": limit})
    data = r.json()
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# espn
# ---------------------------------------------------------------------------

def headlines(limit=40):
    """Recent NFL headlines. Returns [{id, title, summary, link, published}]."""
    items, errors = [], []
    for feed in ESPN_FEEDS:
        try:
            r = _get(feed, timeout=20)
            root = ET.fromstring(r.content)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                if not title:
                    continue
                link = (item.findtext("link") or "").strip()
                items.append({
                    "id": (item.findtext("guid") or link or title).strip(),
                    "title": title,
                    "summary": (item.findtext("description") or "").strip(),
                    "link": link,
                    "published": (item.findtext("pubDate") or "").strip(),
                })
            if items:
                break  # first working feed is enough
        except (SourceError, ET.ParseError) as exc:
            errors.append(f"{feed}: {exc}")
    if not items and errors:
        raise SourceError("; ".join(errors))
    return items[:limit]


INJURY_WORDS = re.compile(
    r"\b(injur\w*|hurt|carted|ruled out|questionable|doubtful|out for|"
    r"placed on ir|injured reserve|mri|sprain\w*|strain\w*|tear|torn|acl|"
    r"concussion|hamstring|groin|ankle|knee|surgery|suspend\w*|activated|"
    r"return\w*|dnp|did not practice|limited|inactive|scratch\w*)\b",
    re.I,
)

OPPORTUNITY_WORDS = re.compile(
    r"\b(starter|starting|promoted|elevated|leads|lead back|"
    r"first team|snap share|target share|workload|role|touches|"
    r"depth chart|benched|released|waived|traded|signs|signed)\b",
    re.I,
)


def classify(text):
    """Rough read on why a headline matters. Cheap keyword pass, not magic."""
    tags = []
    if INJURY_WORDS.search(text or ""):
        tags.append("injury")
    if OPPORTUNITY_WORDS.search(text or ""):
        tags.append("opportunity")
    return tags
