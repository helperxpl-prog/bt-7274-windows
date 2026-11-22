"""
core/user_mixes.py — User-Personalized YouTube Mix Engine for BT-7274 MARK LV.

Directly surfaces the personalized "My Mix" and auto-generated Mixes that YouTube
creates for the user (identified by playlist ID prefix 'RD...', 'RDMM...', 'RDGMEM...'
and the signature 4-split thumbnail / infinite radio queue), rather than random
user-created public playlists ('PL...').

Sources:
1. Primary: Scans the user's active Firefox browser profile history (places.sqlite)
   for the user's actual personalized YouTube mixes and Supermixes ('RDMM...').
2. Web Fallback: Scrapes YouTube search strictly for YouTube's auto-generated RD Mixes
   ('watchEndpoint' with 'RD...' playlist IDs and '&start_radio=1').
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import requests

log = logging.getLogger("bt7274.user_mixes")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
        "Gecko/20100101 Firefox/128.0"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_CACHED_USER_MIXES: List[Dict[str, Any]] = []
_LAST_CACHE_TIME: float = 0.0
_CACHE_TTL_SEC: float = 45.0

MOOD_TAGS: Dict[str, List[str]] = {
    "ENERGETIC": [
        "monster", "skillet", "beggin", "hardtekk", "montagem", "royal",
        "funk", "hardstyle", "rage", "dance", "energy", "workout", "rock",
        "metal", "adrenaline", "speed", "power", "gym", "beast",
    ],
    "CHILL": [
        "slowed", "reverb", "chill", "lofi", "love me not", "dandelions",
        "blue", "quiet", "acoustic", "soft", "shameless", "breeze",
        "doce brisa", "calm", "relax", "unwind", "peaceful", "dream",
    ],
    "FOCUSED": [
        "chess", "beat", "slowed", "ambient", "instrumental", "code",
        "focus", "synth", "study", "concentration", "reverb",
    ],
    "HAPPY": [
        "honeypie", "cupid", "sunroof", "joyful", "dandelions", "happy",
        "cheerful", "smile", "upbeat", "good vibes", "celebrate",
    ],
    "MELANCHOLIC": [
        "slowed", "reverb", "love me not", "imposter", "isolated", "missing piece",
        "blue", "sad", "lonely", "drained", "heartbreak", "crying", "arctic monkeys",
    ],
    "INTENSE": [
        "monster", "skillet", "hardtekk", "rage", "hardstyle", "montagem",
        "phonk", "battle", "intense", "heavy", "screaming", "drift phonk",
    ],
    "BALANCED_FALLBACK": [
        "slowed", "reverb", "honeypie", "beggin", "shameless", "dandelions",
    ],
}


def find_firefox_places_db() -> Optional[Path]:
    """Locate places.sqlite in the active Firefox profile across macOS, Linux, and Windows."""
    candidates = [
        Path.home() / "Library" / "Application Support" / "Firefox",
        Path.home() / ".config" / "mozilla" / "firefox",
        Path.home() / ".mozilla" / "firefox",
    ]
    if os.environ.get("APPDATA"):
        candidates.append(Path(os.environ["APPDATA"]) / "Mozilla" / "Firefox")
    for base in candidates:
        if not base.exists():
            continue
        # Check profiles.ini
        ini = base / "profiles.ini"
        if ini.exists():
            try:
                text = ini.read_text(encoding="utf-8", errors="ignore")
                paths = re.findall(r"Path=([^\r\n]+)", text)
                for p in paths:
                    full_p = base / p if not os.path.isabs(p) else Path(p)
                    db = full_p / "places.sqlite"
                    if db.exists():
                        return db
            except Exception:
                pass

        # Glob fallback
        for db in base.glob("*default*/places.sqlite"):
            if db.exists():
                return db

    return None


def get_user_mixes(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Retrieve the user's authentic YouTube mixes from their Firefox history."""
    global _CACHED_USER_MIXES, _LAST_CACHE_TIME

    now = time.monotonic()
    if not force_refresh and _CACHED_USER_MIXES and (now - _LAST_CACHE_TIME) < _CACHE_TTL_SEC:
        return _CACHED_USER_MIXES

    db_path = find_firefox_places_db()
    if not db_path or not db_path.exists():
        return _CACHED_USER_MIXES

    tmp_db = Path("/tmp") / "places_mix_reader.sqlite"
    try:
        shutil.copyfile(db_path, tmp_db)
    except Exception as e:
        log.warning(f"Could not copy places.sqlite: {e}")
        return _CACHED_USER_MIXES

    mixes: List[Dict[str, Any]] = []
    seen_pids = set()

    try:
        conn = sqlite3.connect(tmp_db)
        cur = conn.cursor()
        cur.execute("""
            SELECT url, title, visit_count, last_visit_date 
            FROM moz_places 
            WHERE url LIKE '%list=RD%' 
            ORDER BY last_visit_date DESC
        """)
        rows = cur.fetchall()
        conn.close()

        for url, title, count, date in rows:
            pm = re.search(r"list=(RD[A-Za-z0-9_-]+)", url)
            if not pm:
                continue
            pid = pm.group(1)
            if pid in seen_pids:
                continue
            seen_pids.add(pid)

            vm = re.search(r"v=([A-Za-z0-9_-]{11})", url)
            vid = vm.group(1) if vm else ""
            if not vid:
                continue

            clean_title = re.sub(r"^\(\d+\)\s*", "", title or "").replace(" - YouTube", "").strip()
            # Canonical YouTube Mix URL with &start_radio=1
            mix_url = f"https://www.youtube.com/watch?v={vid}&list={pid}&start_radio=1"

            # Determine if this is a personal Supermix (RDMM) or Genre Mix (RDGMEM)
            is_supermix = "RDMM" in pid
            is_genre_mix = "RDGMEM" in pid

            mixes.append({
                "pid": pid,
                "vid": vid,
                "title": clean_title,
                "url": mix_url,
                "count": count or 1,
                "is_supermix": is_supermix,
                "is_genre_mix": is_genre_mix,
            })

    except Exception as e:
        log.error(f"Error reading user mixes from places.sqlite: {e}")
    finally:
        try:
            if tmp_db.exists():
                tmp_db.unlink()
        except Exception:
            pass

    _CACHED_USER_MIXES = mixes
    _LAST_CACHE_TIME = now
    log.info(f"Loaded {len(mixes)} unique personalized YouTube mixes from Firefox profile.")
    return mixes


def scrape_youtube_rd_mix(query: str) -> Optional[Dict[str, str]]:
    """
    Scrape YouTube search specifically for official YouTube-generated RD Mixes.
    Extracts 'watchEndpoint' pairing a videoId and playlistId starting with 'RD'.
    Appends '&start_radio=1' to trigger YouTube's radio mix layout and collage thumbnail.
    """
    clean_q = f"{query} mix" if "mix" not in query.lower() else query
    search_url = f"https://www.youtube.com/results?search_query={quote_plus(clean_q)}"

    try:
        r = requests.get(search_url, headers=HEADERS, timeout=8)
        html = r.text

        # 1. Match watchEndpoint with both videoId and playlistId starting with RD
        endpoints = re.findall(
            r'\"watchEndpoint\":\{\"videoId\":\"([A-Za-z0-9_-]{11})\",\"playlistId\":\"(RD[A-Za-z0-9_-]+)\"',
            html,
        )
        for vid, pid in endpoints:
            # Title extraction attempt near the endpoint
            mix_url = f"https://www.youtube.com/watch?v={vid}&list={pid}&start_radio=1"
            return {
                "pid": pid,
                "vid": vid,
                "title": f"YouTube Mix for {query}",
                "url": mix_url,
            }

        # 2. Match playlistId starting with RD alone
        pids = re.findall(r'\"playlistId\":\"(RD[A-Za-z0-9_-]+)\"', html)
        if pids:
            pid = pids[0]
            vids = re.findall(r'\"videoId\":\"([A-Za-z0-9_-]{11})\"', html)
            vid = vids[0] if vids else ""
            mix_url = (
                f"https://www.youtube.com/watch?v={vid}&list={pid}&start_radio=1"
                if vid else f"https://www.youtube.com/playlist?list={pid}"
            )
            return {
                "pid": pid,
                "vid": vid,
                "title": f"YouTube Mix for {query}",
                "url": mix_url,
            }

    except Exception as e:
        log.warning(f"Failed to scrape YouTube RD mix for '{query}': {e}")

    return None


def get_best_youtube_mix(mood_key: str, query: str = "") -> Tuple[str, str, bool]:
    """
    Select the optimal YouTube mix for the user:
    1. First tries to match the user's authentic personalized mixes from Firefox history.
    2. If no history match or query is novel, scrapes YouTube strictly for official RD Mixes.
    3. Always returns an authentic YouTube Mix (list=RD... with 4-split thumbnail), never a random PL... playlist.

    Returns:
        (mix_url, mix_title, is_personal_history)
    """
    user_mixes = get_user_mixes()
    q_low = query.lower().strip()

    # Check if query is a generic request (e.g. "play a mix", "my mix", "music based on my mood")
    _filler = {
        "play", "a", "the", "me", "something", "based", "on", "my", "mood",
        "youtube", "mix", "playlist", "music", "vibe", "songs", "to", "for", "listen"
    }
    _words = set(re.findall(r"[a-z]+", q_low))
    is_generic_query = not (_words - _filler)

    # Priority 1: Exact search query match within user's own mixes (if specific)
    if not is_generic_query and q_low and user_mixes:
        for m in user_mixes:
            if q_low in m["title"].lower():
                log.info(f"Matched query '{query}' in user mix history: {m['title']}")
                return m["url"], m["title"], True

    # If the user specified a distinct custom artist/genre query (not generic mix request),
    # and it's not in their history, scrape the official YouTube RD Mix for that exact query.
    if not is_generic_query:
        scraped = scrape_youtube_rd_mix(query)
        if scraped:
            log.info(f"Scraped official YouTube RD Mix for novel query '{query}': {scraped['url']}")
            return scraped["url"], scraped["title"], False

    # Priority 2: Mood-aligned match from user's own mixes
    tags = MOOD_TAGS.get(mood_key, MOOD_TAGS["BALANCED_FALLBACK"])
    if user_mixes:
        # Check Supermixes and Genre mixes first
        for m in user_mixes:
            t_low = m["title"].lower()
            if any(tag in t_low for tag in tags):
                log.info(f"Matched mood '{mood_key}' to user mix: {m['title']}")
                return m["url"], m["title"], True

    # Priority 3: If user asked for "my mix" or no specific query, return their top Supermix
    if user_mixes:
        # Look for RDMM (My Supermix)
        for m in user_mixes:
            if m["is_supermix"]:
                log.info(f"Selected user's top Supermix: {m['title']}")
                return m["url"], f"My Mix — {m['title']}", True
        return user_mixes[0]["url"], user_mixes[0]["title"], True

    # Priority 4: Scrape YouTube strictly for an official RD Mix
    target_q = query if query else (tags[0] if tags else "chill")
    scraped = scrape_youtube_rd_mix(target_q)
    if scraped:
        log.info(f"Scraped official YouTube RD Mix for '{target_q}': {scraped['url']}")
        return scraped["url"], scraped["title"], False

    # Priority 5: Fallback to user's first available mix
    if user_mixes:
        return user_mixes[0]["url"], user_mixes[0]["title"], True

    # Final absolute fallback: generic YouTube My Mix URL
    return "https://www.youtube.com/watch?list=RDMM&start_radio=1", "YouTube My Mix", False
