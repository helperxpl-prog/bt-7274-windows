"""
core/mood_detector.py — Multi-Modal Mood Detection Engine for BT-7274 MARK LV.

Extracts real-time acoustic features (energy/volume, speaking pace, pitch/F0 contour,
tonal inflection, and spectral centroid) from recent microphone audio buffers,
fuses them with text/query sentiment cues, announces the recognized mood with
BT-7274 Vanguard Titan persona, and maps to tailored YouTube mix/playlist queries
with robust fallback when telemetry is uncertain.
"""
from __future__ import annotations

import collections
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import signal

log = logging.getLogger("bt7274.mood_detector")

# ── Voice Buffer Singleton ───────────────────────────────────────────────────

class VoiceBuffer:
    """Thread-safe ring buffer capturing the most recent microphone PCM audio."""

    def __init__(self, max_seconds: float = 8.0, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.max_samples = int(max_seconds * sample_rate)
        self._lock = threading.Lock()
        self._buffer = collections.deque(maxlen=int(max_seconds * sample_rate / 512) + 20)
        self._total_samples = 0
        self._last_push_time = 0.0

    def push(self, chunk: np.ndarray | bytes, sample_rate: Optional[int] = None) -> None:
        """Push incoming PCM int16 audio chunk from microphone callback."""
        if chunk is None or len(chunk) == 0:
            return
        if sample_rate and sample_rate != self.sample_rate:
            self.sample_rate = sample_rate

        if isinstance(chunk, bytes):
            arr = np.frombuffer(chunk, dtype=np.int16)
        else:
            arr = np.asarray(chunk, dtype=np.int16).flatten()

        if len(arr) == 0:
            return

        with self._lock:
            self._buffer.append(arr.copy())
            self._total_samples += len(arr)
            self._last_push_time = time.monotonic()

    def get_recent_audio(self, seconds: float = 5.0) -> np.ndarray:
        """Retrieve recent audio as float32 array in range [-1.0, 1.0]."""
        target_samples = int(seconds * self.sample_rate)
        with self._lock:
            if not self._buffer:
                return np.zeros(0, dtype=np.float32)

            chunks = list(self._buffer)

        if not chunks:
            return np.zeros(0, dtype=np.float32)

        merged = np.concatenate(chunks)
        if len(merged) > target_samples:
            merged = merged[-target_samples:]

        return (merged.astype(np.float32) / 32768.0).clip(-1.0, 1.0)

    def has_audio(self, min_seconds: float = 0.4) -> bool:
        """Check if buffer has accumulated sufficient audio."""
        with self._lock:
            return (self._total_samples >= int(min_seconds * self.sample_rate) and
                    (time.monotonic() - self._last_push_time) < 4.0)


VOICE_BUFFER = VoiceBuffer()


def record_audio_chunk(chunk: np.ndarray | bytes, sample_rate: int = 16000) -> None:
    """Global helper called by microphone listener."""
    VOICE_BUFFER.push(chunk, sample_rate)


# ── Acoustic Feature Representation ──────────────────────────────────────────

@dataclass
class AcousticFeatures:
    total_duration_sec: float = 0.0
    speech_duration_sec: float = 0.0
    speech_ratio: float = 0.0
    rms_energy: float = 0.0
    peak_energy: float = 0.0
    pitch_mean_hz: float = 0.0
    pitch_std_hz: float = 0.0
    speaking_pace_syl_sec: float = 0.0
    spectral_centroid_hz: float = 0.0
    zero_crossing_rate: float = 0.0
    is_valid_speech: bool = False


def extract_acoustic_features(
    audio: np.ndarray,
    sample_rate: int = 16000,
    frame_ms: int = 30,
    hop_ms: int = 15,
) -> AcousticFeatures:
    """Analyze raw audio and extract pitch, pace, energy, and timbre features."""
    if len(audio) < int(0.25 * sample_rate):
        return AcousticFeatures()

    total_duration = len(audio) / sample_rate
    frame_len = int(sample_rate * (frame_ms / 1000.0))
    hop_len = int(sample_rate * (hop_ms / 1000.0))

    if len(audio) < frame_len:
        return AcousticFeatures(total_duration_sec=total_duration)

    # Frame slicing
    num_frames = 1 + (len(audio) - frame_len) // hop_len
    frames = np.lib.stride_tricks.as_strided(
        audio,
        shape=(num_frames, frame_len),
        strides=(audio.strides[0] * hop_len, audio.strides[0])
    )

    # Frame RMS energies
    frame_rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    overall_rms = float(np.mean(frame_rms))
    peak_energy = float(np.max(frame_rms))

    # Noise floor and Voice Activity Detection (VAD)
    sorted_rms = np.sort(frame_rms)
    noise_floor = float(np.mean(sorted_rms[:max(1, int(len(sorted_rms) * 0.2))]))
    vad_thresh = max(noise_floor * 2.2, 0.015)
    speech_mask = frame_rms > vad_thresh
    speech_frames_count = int(np.sum(speech_mask))

    speech_duration = speech_frames_count * (hop_ms / 1000.0)
    speech_ratio = speech_duration / max(total_duration, 1e-6)

    if speech_duration < 0.28:
        # Insufficient speech activity
        return AcousticFeatures(
            total_duration_sec=total_duration,
            speech_duration_sec=speech_duration,
            speech_ratio=speech_ratio,
            rms_energy=overall_rms,
            peak_energy=peak_energy,
            is_valid_speech=False,
        )

    speech_frames = frames[speech_mask]
    speech_rms_mean = float(np.mean(frame_rms[speech_mask]))

    # 1. Pitch (F0) Estimation via Normalized Autocorrelation
    # Human fundamental voice range: 75 Hz to 360 Hz
    min_lag = int(sample_rate / 360.0)
    max_lag = int(sample_rate / 75.0)

    f0_estimates = []
    step = max(1, len(speech_frames) // 30)
    for frame in speech_frames[::step]:
        f_norm = frame - np.mean(frame)
        corr = signal.correlate(f_norm, f_norm, mode="full")
        corr = corr[len(corr) // 2:]
        if len(corr) > max_lag:
            peak_idx = min_lag + np.argmax(corr[min_lag:max_lag])
            peak_val = corr[peak_idx]
            zero_val = corr[0]
            if zero_val > 1e-6 and (peak_val / zero_val) > 0.35:
                est_f0 = sample_rate / peak_idx
                if 70.0 <= est_f0 <= 380.0:
                    f0_estimates.append(est_f0)

    if f0_estimates:
        pitch_mean = float(np.mean(f0_estimates))
        pitch_std = float(np.std(f0_estimates))
    else:
        pitch_mean = 140.0
        pitch_std = 0.0

    # 2. Speaking Pace (Syllable / Energy envelope peaks across utterance span)
    speech_indices = np.where(speech_mask)[0]
    utterance_frames = frame_rms[speech_indices[0]:speech_indices[-1] + 1]
    utterance_span_sec = float(len(utterance_frames) * (hop_ms / 1000.0))

    smooth_win = max(3, int(90 / hop_ms))
    smooth_kernel = np.ones(smooth_win) / smooth_win
    smoothed_env = np.convolve(utterance_frames, smooth_kernel, mode="same")

    dyn = float(np.max(smoothed_env) - np.min(smoothed_env))
    prominence = max(0.003, 0.20 * dyn)
    height = float(np.mean(smoothed_env) * 0.65)
    min_dist = max(2, int(130 / hop_ms))

    peaks, _ = signal.find_peaks(smoothed_env, distance=min_dist, prominence=prominence, height=height)
    syllable_count = len(peaks)
    speaking_pace = syllable_count / max(utterance_span_sec, 0.4)

    # 3. Spectral Centroid (Timbre & Tension)
    speech_audio = speech_frames.flatten()
    if len(speech_audio) >= 256:
        nperseg = min(512, len(speech_audio))
        freqs, psd = signal.welch(speech_audio, sample_rate, nperseg=nperseg)
        centroid = float(np.sum(freqs * psd) / (np.sum(psd) + 1e-12))
    else:
        centroid = 1400.0

    # 4. Zero Crossing Rate (ZCR)
    zcr = float(np.mean(np.abs(np.diff(np.sign(speech_audio)))) / 2.0)

    return AcousticFeatures(
        total_duration_sec=total_duration,
        speech_duration_sec=speech_duration,
        speech_ratio=speech_ratio,
        rms_energy=speech_rms_mean,
        peak_energy=peak_energy,
        pitch_mean_hz=pitch_mean,
        pitch_std_hz=pitch_std,
        speaking_pace_syl_sec=speaking_pace,
        spectral_centroid_hz=centroid,
        zero_crossing_rate=zcr,
        is_valid_speech=True,
    )


# ── Text & Context Sentiment Analysis ────────────────────────────────────────

KEYWORD_LEXICON: Dict[str, Dict[str, float]] = {
    "ENERGETIC": {
        "workout": 3.5, "gym": 3.5, "hype": 3.5, "pump": 3.0, "pumped": 3.5,
        "energy": 3.0, "energetic": 3.5, "fast": 2.0, "run": 2.5, "running": 2.5,
        "party": 3.0, "dance": 2.5, "power": 2.0, "heavy": 1.5, "upbeat": 2.0,
        "lift": 2.5, "beast": 3.0, "cardio": 3.0, "train": 2.0,
    },
    "CHILL": {
        "chill": 3.5, "relax": 3.5, "relaxing": 3.5, "lofi": 4.0, "lo-fi": 4.0,
        "calm": 3.5, "unwind": 3.0, "peaceful": 3.0, "ambient": 3.0, "soft": 2.5,
        "slow": 2.0, "laid back": 3.5, "cozy": 3.0, "sleep": 2.5, "rest": 2.5,
        "bedtime": 3.0, "night": 2.0, "easy": 2.0, "vibe": 1.5,
    },
    "FOCUSED": {
        "focus": 4.0, "study": 4.0, "studying": 4.0, "code": 4.0, "coding": 4.0,
        "work": 3.0, "working": 3.0, "concentrate": 3.5, "concentration": 3.5,
        "deep work": 4.0, "grind": 3.0, "synthwave": 3.5, "instrumental": 3.0,
        "programming": 3.5, "read": 2.5, "reading": 2.5, "homework": 3.0,
    },
    "HAPPY": {
        "happy": 4.0, "good vibes": 3.5, "cheerful": 3.5, "feel good": 3.5,
        "positive": 3.0, "joy": 3.5, "smile": 2.5, "sunny": 2.5, "celebrate": 3.0,
        "excited": 2.5, "bright": 2.0, "uplifting": 3.0,
    },
    "MELANCHOLIC": {
        "sad": 4.0, "down": 3.0, "depressed": 4.0, "lonely": 3.5, "tired": 3.0,
        "exhausted": 3.5, "rough day": 4.0, "heartbreak": 4.0, "crying": 4.0,
        "melancholy": 4.0, "gloom": 3.0, "gloomy": 3.0, "hurts": 3.0,
        "drained": 3.5, "bad day": 3.5,
    },
    "INTENSE": {
        "angry": 4.0, "rage": 4.0, "mad": 3.0, "furious": 4.0, "aggressive": 4.0,
        "phonk": 4.0, "drift phonk": 4.0, "metal": 4.0, "heavy metal": 4.0,
        "rock": 3.0, "intense": 3.5, "screaming": 3.5, "battle": 3.5,
        "adrenaline": 3.5, "destroy": 3.0,
    },
}


def analyze_text_cues(text: str) -> Tuple[Dict[str, float], Optional[str]]:
    """Scan input text for explicit or subtle sentiment keywords."""
    low = (text or "").lower()
    scores: Dict[str, float] = {k: 0.0 for k in KEYWORD_LEXICON}
    matched_word = None

    for mood, keywords in KEYWORD_LEXICON.items():
        for kw, weight in keywords.items():
            if re.search(r"\b" + re.escape(kw) + r"\b", low):
                scores[mood] += weight
                if matched_word is None:
                    matched_word = kw

    return scores, matched_word


# ── Mood Classification Profiles ─────────────────────────────────────────────

@dataclass
class MoodResult:
    mood_key: str
    display_name: str
    confidence: float
    reason: str
    spoken_announcement: str
    search_queries: List[str]
    is_fallback: bool = False
    acoustic_features: Optional[AcousticFeatures] = None


MOOD_PROFILES: Dict[str, Dict[str, Any]] = {
    "ENERGETIC": {
        "display_name": "High Energy & Workout",
        "spoken": "Acoustic telemetry indicates high energy and an elevated cadence, Pilot. Queuing an energetic workout mix.",
        "queries": [
            "high energy workout music mix playlist",
            "energetic hype music mix playlist",
            "best gym motivation playlist mix",
        ],
    },
    "CHILL": {
        "display_name": "Chill & Relaxed",
        "spoken": "Voice analysis detects a relaxed cadence and calm tonal frequencies, Pilot. Engaging a chill lofi mix.",
        "queries": [
            "chill lofi hip hop mix playlist",
            "relaxing acoustic chill vibes playlist",
            "chillhop beats to relax playlist mix",
        ],
    },
    "FOCUSED": {
        "display_name": "Deep Focus & Study",
        "spoken": "Detecting steady cadence and focused vocal telemetry, Pilot. Launching a deep focus study mix.",
        "queries": [
            "synthwave coding focus mix playlist",
            "deep focus instrumental study mix playlist",
            "ambient electronic work focus playlist mix",
        ],
    },
    "HAPPY": {
        "display_name": "Upbeat & Joyful",
        "spoken": "Voice resonance displays upbeat frequencies and positive inflection, Pilot. Queuing a feel-good playlist.",
        "queries": [
            "feel good upbeat pop music mix playlist",
            "happy energetic upbeat playlist mix",
            "positive vibes summer music mix playlist",
        ],
    },
    "MELANCHOLIC": {
        "display_name": "Melancholic & Reflective",
        "spoken": "Telemetry detects subdued vocal energy and low cadence, Pilot. Queuing a soft, reflective playlist.",
        "queries": [
            "sad emotional acoustic songs mix playlist",
            "melancholy indie slow songs mix playlist",
            "comforting sad songs playlist mix",
        ],
    },
    "INTENSE": {
        "display_name": "Intense & Adrenaline",
        "spoken": "Vocal acoustic analysis indicates high acoustic tension and aggressive drive, Pilot. Launching an intense adrenaline mix.",
        "queries": [
            "aggressive drift phonk workout mix playlist",
            "heavy metal rock adrenaline mix playlist",
            "dark cyber synthwave intense mix playlist",
        ],
    },
    "BALANCED_FALLBACK": {
        "display_name": "Balanced Ambient Chill",
        "spoken": "Voice telemetry is inconclusive, Pilot. Selecting a balanced ambient chill mix.",
        "queries": [
            "popular hits music mix playlist",
            "chill lofi beats playlist mix",
            "top acoustic pop mix playlist",
        ],
    },
}


def detect_mood(
    audio: Optional[np.ndarray] = None,
    text: str = "",
    sample_rate: int = 16000,
) -> MoodResult:
    """
    Multi-modal mood detector combining vocal telemetry and text context.
    If audio is not supplied, it attempts to pull from the global VOICE_BUFFER.
    """
    if audio is None:
        audio = VOICE_BUFFER.get_recent_audio(seconds=5.5)

    acoustics = extract_acoustic_features(audio, sample_rate=sample_rate)
    text_scores, matched_keyword = analyze_text_cues(text)

    # Initialize raw mood scores
    mood_scores = {k: 0.0 for k in MOOD_PROFILES if k != "BALANCED_FALLBACK"}
    reasons: List[str] = []

    # ── 1. Acoustic scoring ──────────────────────────────────────────────────
    if acoustics.is_valid_speech:
        rms = acoustics.rms_energy
        pace = acoustics.speaking_pace_syl_sec
        f0_mean = acoustics.pitch_mean_hz
        f0_std = acoustics.pitch_std_hz
        centroid = acoustics.spectral_centroid_hz

        # Energetic: high RMS, fast pace, pitch variation, bright centroid
        if rms > 0.055:
            mood_scores["ENERGETIC"] += 2.5
            mood_scores["INTENSE"] += 1.8
            mood_scores["CHILL"] -= 1.5
            reasons.append(f"elevated vocal energy (RMS {rms:.3f})")
        if pace > 3.8:
            mood_scores["ENERGETIC"] += 2.4
            mood_scores["CHILL"] -= 1.0
            reasons.append(f"fast pace ({pace:.1f} syl/s)")
        if f0_std > 26.0:
            mood_scores["ENERGETIC"] += 1.5
            mood_scores["HAPPY"] += 2.0
            reasons.append("expressive pitch dynamics")
        if centroid > 1650.0:
            mood_scores["ENERGETIC"] += 1.2
            mood_scores["INTENSE"] += 1.8

        # Chill: mellow energy, relaxed pace, warm timbre
        if 0.026 <= rms <= 0.048:
            mood_scores["CHILL"] += 2.5
            mood_scores["FOCUSED"] += 1.5
        if pace < 3.0:
            if rms >= 0.026:
                mood_scores["CHILL"] += 2.2
            else:
                mood_scores["MELANCHOLIC"] += 2.5
            mood_scores["ENERGETIC"] -= 1.5
            reasons.append(f"relaxed pace ({pace:.1f} syl/s)")
        if centroid < 1350.0 and rms < 0.050:
            if rms >= 0.026:
                mood_scores["CHILL"] += 1.8
            else:
                mood_scores["MELANCHOLIC"] += 1.5
            reasons.append("warm acoustic spectrum")

        # Focused: steady pace, low pitch variance, controlled volume
        if 2.8 <= pace <= 3.8 and 0.022 <= rms <= 0.055:
            mood_scores["FOCUSED"] += 2.2
        if f0_std < 18.0 and acoustics.speech_duration_sec > 0.8 and 0.022 <= rms <= 0.055:
            mood_scores["FOCUSED"] += 1.8
            reasons.append("steady focused tone")

        # Happy: elevated F0, high F0 variance, bright tone, upbeat pace
        if f0_mean > 175.0 and f0_std > 22.0:
            mood_scores["HAPPY"] += 2.5
            reasons.append("bright, elevated pitch contour")
        if pace >= 3.4 and centroid > 1500.0:
            mood_scores["HAPPY"] += 1.5

        # Melancholic: quiet, slow pace, prolonged pauses, flat pitch
        if rms < 0.026:
            mood_scores["MELANCHOLIC"] += 3.5
            mood_scores["CHILL"] -= 1.0
            mood_scores["ENERGETIC"] -= 2.0
            reasons.append(f"subdued vocal energy (RMS {rms:.3f})")
        if f0_std < 14.0 and acoustics.speech_ratio < 0.45:
            mood_scores["MELANCHOLIC"] += 1.8

        # Intense: high energy, high centroid, aggressive dynamics
        if rms > 0.075 and centroid > 1850.0:
            mood_scores["INTENSE"] += 3.2
            reasons.append(f"high vocal intensity (RMS {rms:.3f}, centroid {centroid:.0f}Hz)")

    # ── 2. Text scoring fusion ───────────────────────────────────────────────
    has_text_signal = any(v > 0 for v in text_scores.values())
    if has_text_signal:
        for k, v in text_scores.items():
            mood_scores[k] += v * 1.6  # direct keyword cues carry high intent weight
        if matched_keyword:
            reasons.append(f"keyword '{matched_keyword}' in request")

    # ── 3. Decision & Confidence Estimation ──────────────────────────────────
    sorted_moods = sorted(mood_scores.items(), key=lambda x: x[1], reverse=True)
    top_mood, top_score = sorted_moods[0]
    runner_up_score = sorted_moods[1][1] if len(sorted_moods) > 1 else 0.0

    # Minimum score threshold to avoid false positives
    min_confidence_score = 2.4

    if top_score < min_confidence_score:
        # Voice telemetry is inconclusive and no strong text keywords -> Fallback cleanly
        fallback_prof = MOOD_PROFILES["BALANCED_FALLBACK"]
        reason_str = "Vocal telemetry inconclusive; defaulted to balanced ambient."
        log.info(f"[MoodDetector] Telemetry uncertain (top score {top_score:.1f}). Using fallback.")
        return MoodResult(
            mood_key="BALANCED_FALLBACK",
            display_name=fallback_prof["display_name"],
            confidence=0.35,
            reason=reason_str,
            spoken_announcement=fallback_prof["spoken"],
            search_queries=fallback_prof["queries"],
            is_fallback=True,
            acoustic_features=acoustics,
        )

    # Compute normalized confidence
    margin = top_score - runner_up_score
    confidence = min(0.96, max(0.50, 0.40 + (top_score / 12.0) + (margin / 8.0)))

    prof = MOOD_PROFILES[top_mood]
    reason_str = ", ".join(reasons) if reasons else "Acoustic and semantic alignment"

    log.info(f"[MoodDetector] Recognized {top_mood} ({confidence*100:.0f}% confidence | {reason_str})")

    return MoodResult(
        mood_key=top_mood,
        display_name=prof["display_name"],
        confidence=confidence,
        reason=reason_str,
        spoken_announcement=prof["spoken"],
        search_queries=prof["queries"],
        is_fallback=False,
        acoustic_features=acoustics,
    )
