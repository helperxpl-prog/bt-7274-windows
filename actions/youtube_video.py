#youtube_video.py
import json
import re
import sys
import time
import subprocess
import shutil
from pathlib import Path
from datetime import datetime
from urllib.parse import quote_plus

try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import numpy as np
    _NUMPY = True
except ImportError:
    _NUMPY = False

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    _TRANSCRIPT_OK = True
except ImportError:
    _TRANSCRIPT_OK = False

from config import get_os, is_windows, is_mac, is_linux


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_YT_VIDEO_FILTER = "EgIQAQ%3D%3D"
_YT_PLAYLIST_FILTER = "EgIQAw%3D%3D"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _open_url(url: str) -> None:
    try:
        import webbrowser
        webbrowser.open(url)
        return
    except Exception:
        pass
    try:
        if is_mac():
            subprocess.Popen(["open", url])
        elif is_linux():
            subprocess.Popen(["xdg-open", url])
        else:
            subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
    except Exception as e:
        print(f"[YouTube] ⚠️ open_url failed: {e}")

def _scrape_first_playlist_url(query: str) -> tuple[str | None, str | None]:
    if not _REQUESTS_OK:
        return None, None

    search_url = (
        f"https://www.youtube.com/results"
        f"?search_query={quote_plus(query)}"
        f"&sp={_YT_PLAYLIST_FILTER}"
    )

    try:
        r = requests.get(search_url, headers=HEADERS, timeout=10)
        html = r.text

        # 1. Look for watchEndpoint pairing videoId and playlistId
        endpoints = re.findall(
            r'\"watchEndpoint\":\{\"videoId\":\"([A-Za-z0-9_-]{11})\",\"playlistId\":\"([A-Za-z0-9_-]+)\"',
            html,
        )
        for vid, pid in endpoints:
            if pid and not pid.startswith(("WL", "LL")):
                return f"https://www.youtube.com/watch?v={vid}&list={pid}", None

        # 2. Look for playlistId alone
        playlist_ids = re.findall(r'\"playlistId\":\"([A-Za-z0-9_-]{10,})\"', html)
        for pid in playlist_ids:
            if pid and not pid.startswith(("WL", "LL")):
                return f"https://www.youtube.com/playlist?list={pid}", None

    except Exception as e:
        print(f"[YouTube] ⚠️ scrape_first_playlist_url failed: {e}")

    return None, None

def _scrape_first_video_url(query: str) -> str | None:

    if not _REQUESTS_OK:
        return None

    search_url = (
        f"https://www.youtube.com/results"
        f"?search_query={quote_plus(query)}"
        f"&sp={_YT_VIDEO_FILTER}"
    )

    try:
        r    = requests.get(search_url, headers=HEADERS, timeout=10)
        html = r.text

        video_ids = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', html)

        seen = set()
        for vid in video_ids:
            if vid in seen:
                continue
            seen.add(vid)

            if f'/shorts/{vid}' in html:
                continue
            return f"https://www.youtube.com/watch?v={vid}"

    except Exception as e:
        print(f"[YouTube] ⚠️ scrape_first_video_url failed: {e}")

    return None

def _extract_video_id(url: str) -> str | None:
    match = re.search(
        r"(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/)([A-Za-z0-9_-]{11})", url
    )
    return match.group(1) if match else None


def _is_valid_youtube_url(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be)", url or ""))


def _ask_for_url(prompt_text: str = "YouTube video URL:") -> str | None:
    try:
        import tkinter as tk
        from tkinter import simpledialog

        root = tk._default_root
        if root is None:
            root = tk.Tk()
            root.withdraw()

        url = simpledialog.askstring("J.A.R.V.I.S", prompt_text, parent=root)
        return url.strip() if url else None
    except Exception as e:
        print(f"[YouTube] ⚠️ URL dialog failed: {e}")
        return None


def _get_transcript(video_id: str) -> str | None:
    if not _TRANSCRIPT_OK:
        return None
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript      = None

        lang_priority = ["en", "tr", "de", "fr", "es", "it", "pt", "ru", "ja", "ko", "ar", "zh"]

        try:
            transcript = transcript_list.find_manually_created_transcript(lang_priority)
        except Exception:
            pass

        if transcript is None:
            try:
                transcript = transcript_list.find_generated_transcript(lang_priority)
            except Exception:
                for t in transcript_list:
                    transcript = t
                    break

        if transcript is None:
            return None

        fetched = transcript.fetch()
        return " ".join(entry["text"] for entry in fetched)

    except Exception as e:
        print(f"[YouTube] ⚠️ Transcript fetch failed: {e}")
        return None


def _summarize_with_gemini(transcript: str, video_url: str) -> str:
    from google.genai import types
    from core import gemini

    max_chars = 80000
    truncated = transcript[:max_chars] + ("..." if len(transcript) > max_chars else "")
    # A whole transcript can be 80k characters, hence the long deadline — but a
    # deadline there is, and the ladder in core/gemini.py picks the model.
    response = gemini.call(
        f"Please summarize this YouTube video transcript:\n\n{truncated}",
        tier=gemini.SMART,
        timeout_ms=60_000,
        config=types.GenerateContentConfig(
            system_instruction=(
                "You are BT-7274, a Vanguard-class Titan AI assistant. "
                "Summarize YouTube video transcripts clearly and concisely. "
                "Structure: 1-sentence overview, then 3-5 key points. "
                "Be direct. Address the user as 'sir'. "
                "Match the language of the transcript."
            )
        )
    )
    if response is None:
        return "I couldn't reach Gemini to summarise that transcript, sir."
    return (response.text or "").strip()


def _save_summary(content: str, video_url: str) -> str:
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"youtube_summary_{ts}.txt"
    desktop  = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    filepath = desktop / filename

    header = (
        f"BT-7274 — YouTube Summary\n"
        f"{'─' * 50}\n"
        f"URL    : {video_url}\n"
        f"Date   : {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{'─' * 50}\n\n"
    )
    filepath.write_text(header + content, encoding="utf-8")

    try:
        if is_windows():
            subprocess.Popen(["notepad.exe", str(filepath)])
        elif is_mac():
            subprocess.Popen(["open", "-t", str(filepath)])
        else:
            subprocess.Popen(["xdg-open", str(filepath)])
    except Exception as e:
        print(f"[YouTube] ⚠️ Could not open text editor: {e}")

    return str(filepath)


def _scrape_video_info(video_id: str) -> dict:
    if not _REQUESTS_OK:
        return {}
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        r    = requests.get(url, headers=HEADERS, timeout=12)
        html = r.text
        info = {}

        for key, pattern in [
            ("title",    r'"title":\{"runs":\[\{"text":"([^"]+)"'),
            ("channel",  r'"ownerChannelName":"([^"]+)"'),
            ("views",    r'"viewCount":"(\d+)"'),
            ("duration", r'"lengthSeconds":"(\d+)"'),
            ("likes",    r'"label":"([0-9,]+ likes)"'),
        ]:
            match = re.search(pattern, html)
            if match:
                raw = match.group(1)
                if key == "views":
                    info[key] = f"{int(raw):,}"
                elif key == "duration":
                    secs = int(raw)
                    info[key] = f"{secs // 60}:{secs % 60:02d}"
                else:
                    info[key] = raw

        return info
    except Exception as e:
        print(f"[YouTube] ⚠️ Info scrape failed: {e}")
        return {}


def _scrape_trending(region: str = "TR", max_results: int = 8) -> list[dict]:
    if not _REQUESTS_OK:
        return []
    url = f"https://www.youtube.com/feed/trending?gl={region.upper()}"
    try:
        r    = requests.get(url, headers=HEADERS, timeout=12)
        html = r.text

        titles   = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"\}\]', html)
        channels = re.findall(r'"ownerText":\{"runs":\[\{"text":"([^"]+)"', html)

        results, seen = [], set()
        for i, title in enumerate(titles):
            if title in seen or len(title) < 5:
                continue
            seen.add(title)
            channel = channels[i] if i < len(channels) else "Unknown"
            results.append({"rank": len(results) + 1, "title": title, "channel": channel})
            if len(results) >= max_results:
                break

        return results
    except Exception as e:
        print(f"[YouTube] ⚠️ Trending scrape failed: {e}")
        return []

def _handle_mood_mix(parameters: dict, player=None, speak=None) -> str:
    query = (parameters.get("query") or "").strip()
    mood_hint = (parameters.get("mood") or "").strip()
    full_context = f"{mood_hint} {query}".strip()

    try:
        from core.mood_detector import detect_mood, MoodResult, MOOD_PROFILES
        mood_res = detect_mood(text=full_context)
    except Exception as e:
        print(f"[YouTube] ⚠️ Mood detection error: {e}")
        from core.mood_detector import MOOD_PROFILES, MoodResult
        fb = MOOD_PROFILES["BALANCED_FALLBACK"]
        mood_res = MoodResult(
            mood_key="BALANCED_FALLBACK",
            display_name=fb["display_name"],
            confidence=0.35,
            reason="Telemetry uncertain; defaulted to balanced ambient",
            spoken_announcement=fb["spoken"],
            search_queries=fb["queries"],
            is_fallback=True,
        )

    # State recognized mood clearly
    announcement = mood_res.spoken_announcement
    conf_pct = int(mood_res.confidence * 100)

    if player:
        player.write_log(f"[MOOD] Recognized {mood_res.display_name} ({conf_pct}% confidence | {mood_res.reason})")

    if speak:
        try:
            speak(announcement)
        except Exception as e:
            print(f"[YouTube] ⚠️ Speech output failed: {e}")

    # Retrieve the user's authentic personalized mix from history or official YouTube RD Mix
    from core.user_mixes import get_best_youtube_mix
    target_url, target_title, from_history = get_best_youtube_mix(
        mood_key=mood_res.mood_key,
        query=query or full_context,
    )

    source_tag = "Personal YouTube Mix" if from_history else "Official YouTube Mix"
    print(f"[YouTube] ▶️ Opening {source_tag}: {target_url} ({target_title})")
    _open_url(target_url)

    if player:
        player.write_log(f"[YouTube] Playing {source_tag} ({target_title}): {target_url}")

    return (
        f"Recognized mood: {mood_res.display_name} ({conf_pct}% confidence | {mood_res.reason}). "
        f"Playing {source_tag}: {target_title} ({target_url})"
    )


def _handle_play(parameters: dict, player=None, speak=None) -> str:
    query = parameters.get("query", "").strip()
    low = query.lower()

    # Route mood, vibe, mix, or playlist requests to the mood-aware handler
    if not query or any(w in low for w in ("mood", "vibe", "how i feel", "how i sound")) or low in ("mix", "playlist", "a mix", "music", "some music"):
        return _handle_mood_mix(parameters, player, speak)

    if player:
        player.write_log(f"[YouTube] Searching: {query}")

    # If query specifically mentions 'mix' or 'playlist', use official YouTube RD Mixes
    if any(w in low for w in ("mix", "playlist")):
        from core.user_mixes import get_best_youtube_mix
        mix_url, mix_title, from_hist = get_best_youtube_mix(mood_key="BALANCED_FALLBACK", query=query)
        if mix_url:
            source = "Personal YouTube Mix" if from_hist else "Official YouTube Mix"
            print(f"[YouTube] ▶️ Opening {source}: {mix_url} ({mix_title})")
            _open_url(mix_url)
            return f"Playing {source}: {mix_title}"

    print(f"[YouTube] 🔍 Scraping first non-Shorts video for: {query}")

    video_url = _scrape_first_video_url(query)

    if video_url:
        print(f"[YouTube] ▶️ Opening: {video_url}")
        _open_url(video_url)
        return f"Playing: {query}"

    print(f"[YouTube] ⚠️ Scrape failed, opening filtered search page")
    fallback_url = (
        f"https://www.youtube.com/results"
        f"?search_query={quote_plus(query)}"
        f"&sp={_YT_VIDEO_FILTER}"
    )
    _open_url(fallback_url)
    return f"Opened YouTube search for: {query} (manual selection required)"


def _handle_summarize(parameters: dict, player, speak) -> str:
    if not _TRANSCRIPT_OK:
        return "youtube-transcript-api is not installed. Run: pip install youtube-transcript-api"

    url = _ask_for_url("Please paste the YouTube video URL:")
    if not url:
        return "No URL provided, sir. Summary cancelled."
    if not _is_valid_youtube_url(url):
        return "That doesn't appear to be a valid YouTube URL, sir."

    video_id = _extract_video_id(url)
    if not video_id:
        return "Could not extract video ID from that URL, sir."

    if player:
        player.write_log(f"[YouTube] Summarizing: {url}")
    if speak:
        speak("Fetching the transcript now, sir. One moment.")

    transcript = _get_transcript(video_id)
    if not transcript:
        return "I couldn't retrieve a transcript for that video, sir."

    if speak:
        speak("Transcript retrieved. Generating summary now.")

    try:
        summary = _summarize_with_gemini(transcript, url)
    except Exception as e:
        return f"Summary generation failed, sir: {e}"

    if speak:
        speak(summary)

    if parameters.get("save", False):
        saved_path = _save_summary(summary, url)
        return f"Summary complete and saved to Desktop: {saved_path}"

    return summary


def _handle_get_info(parameters: dict, player, speak) -> str:
    url = parameters.get("url", "").strip()
    if not url:
        url = _ask_for_url("Please paste the YouTube video URL:")
    if not url or not _is_valid_youtube_url(url):
        return "Please provide a valid YouTube URL, sir."

    video_id = _extract_video_id(url)
    if not video_id:
        return "Could not extract video ID, sir."

    if player:
        player.write_log(f"[YouTube] Getting info: {url}")

    info = _scrape_video_info(video_id)
    if not info:
        return "Could not retrieve video information, sir."

    lines = [
        f"{key.capitalize()}: {info[key]}"
        for key in ("title", "channel", "views", "duration", "likes")
        if key in info
    ]
    result = "\n".join(lines)

    if speak:
        speak(f"Here's the video info, sir. {result.replace(chr(10), '. ')}")

    return result


def _handle_trending(parameters: dict, player, speak) -> str:
    region = parameters.get("region", "TR").upper()

    if player:
        player.write_log(f"[YouTube] Trending: {region}")

    trending = _scrape_trending(region=region, max_results=8)
    if not trending:
        return f"Could not fetch trending videos for region {region}, sir."

    lines  = [f"Top trending videos in {region}:"]
    lines += [f"{v['rank']}. {v['title']} — {v['channel']}" for v in trending]
    result = "\n".join(lines)

    if speak:
        top3   = trending[:3]
        spoken = "Here are the top trending videos, sir. " + ". ".join(
            f"Number {v['rank']}: {v['title']} by {v['channel']}" for v in top3
        )
        speak(spoken)

    return result

_ACTION_MAP = {
    "play":          _handle_play,
    "play_mood_mix": _handle_mood_mix,
    "mood_mix":      _handle_mood_mix,
    "playlist":      _handle_mood_mix,
    "summarize":     _handle_summarize,
    "get_info":      _handle_get_info,
    "trending":      _handle_trending,
}


def youtube_video(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "play").lower().strip()

    if player:
        player.write_log(f"[YouTube] Action: {action}")
    print(f"[YouTube] ▶️  Action: {action}  Params: {params}")

    handler = _ACTION_MAP.get(action)
    if handler is None:
        if any(w in action for w in ("mood", "mix", "playlist")):
            handler = _handle_mood_mix
        else:
            return (
                f"Unknown YouTube action: '{action}'. "
                "Available: play, play_mood_mix, summarize, get_info, trending."
            )

    try:
        if action in ("play", "play_mood_mix", "mood_mix", "playlist"):
            return handler(params, player, speak) or "Done."
        return handler(params, player, speak) or "Done."
    except Exception as e:
        print(f"[YouTube] ❌ Error in {action}: {e}")
        return f"YouTube {action} failed, sir: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "youtube_video",
    "description": (
        "Controls YouTube playback, mood-based playlists, and video transcripts. "
        "Actions: 'play_mood_mix' recognizes the user's mood from recent voice/audio telemetry "
        "and wording, announces the recognized mood, and launches a matching YouTube mix/playlist; "
        "'play' plays a specific query or video; 'summarize' summarizes transcripts; "
        "'get_info' extracts video metadata; 'trending' returns top trending videos."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "play_mood_mix | play | summarize | get_info | trending (default: play). Use play_mood_mix for music mixes, playlists, or mood-based requests."
            },
            "query": {
                "type": "STRING",
                "description": "Search query or genre/artist context for play or play_mood_mix"
            },
            "mood": {
                "type": "STRING",
                "description": "Optional explicit mood hint (e.g. energetic, chill, focused, happy, melancholic, intense)"
            },
            "save": {
                "type": "BOOLEAN",
                "description": "Save summary to Notepad (summarize only)"
            },
            "region": {
                "type": "STRING",
                "description": "Country code for trending e.g. TR, US"
            },
            "url": {
                "type": "STRING",
                "description": "Video URL for get_info action"
            }
        },
        "required": []
    },
    "handler": youtube_video,
}
