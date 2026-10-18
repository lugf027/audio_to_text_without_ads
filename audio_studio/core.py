from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = "1.0"
MEDIA_EXTENSIONS = {".m4a", ".mp3", ".mp4", ".mov", ".wav", ".aac", ".flac",
                    ".ogg", ".opus", ".mkv", ".webm", ".aiff", ".aif", ".wma", ".m4b"}


def number(value: object) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("时间必须是有限数值")
    return result


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    source_id: int | None = None


@dataclass
class Transcript:
    source: str
    duration: float
    language: str
    segments: list[Segment]
    model: str = ""

    def to_dict(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "time_unit": "seconds", **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict) -> Transcript:
        duration = number(data["duration"])
        if duration <= 0:
            raise ValueError("音频时长必须大于 0")
        segments = []
        ids = set()
        previous_start = -1.0
        for raw in data["segments"]:
            start, end = number(raw["start"]), number(raw["end"])
            sid = int(raw["id"])
            if not 0 <= start < end <= duration + 0.001 or start < previous_start or sid in ids:
                raise ValueError("字幕时间或 ID 无效，请检查导入文件")
            words = []
            for w in raw.get("words", []):
                ws, we = number(w["start"]), number(w["end"])
                if not start - 0.001 <= ws <= we <= end + 0.001:
                    raise ValueError("词级时间超出字幕范围")
                words.append(Word(ws, we, str(w["text"])))
            segments.append(Segment(sid, start, end, str(raw["text"]), words, raw.get("source_id")))
            ids.add(sid)
            previous_start = start
        return cls(str(data["source"]), duration, str(data.get("language", "zh")),
                   segments, str(data.get("model", "")))

    @classmethod
    def load(cls, path: Path) -> Transcript:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


@dataclass
class Chapter:
    title: str
    start: float
    end: float


@dataclass
class Ad:
    start: float
    end: float
    reason: str = ""


@dataclass
class EditPlan:
    chapters: list[Chapter]
    ads: list[Ad] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "time_unit": "seconds", **asdict(self)}

    @classmethod
    def whole(cls, duration: float) -> EditPlan:
        return cls([Chapter("完整音频", 0, duration)])

    @classmethod
    def from_dict(cls, data: dict, duration: float) -> EditPlan:
        chapters = [Chapter(str(c["title"]).strip() or "未命名章节",
                            number(c["start"]), number(c["end"])) for c in data["chapters"]]
        ads = [Ad(number(a["start"]), number(a["end"]), str(a.get("reason", "")))
               for a in data.get("ads", [])]
        plan = cls(chapters, ads)
        plan.validate(duration)
        return plan

    def validate(self, duration: float) -> None:
        if not self.chapters:
            raise ValueError("至少需要一个章节")
        cursor = 0.0
        for c in self.chapters:
            if not math.isfinite(c.start) or not math.isfinite(c.end):
                raise ValueError("章节时间必须是有限数值")
            if abs(c.start - cursor) > 0.001 or not c.start < c.end <= duration + 0.001:
                raise ValueError("章节必须连续、无重叠，且覆盖完整音频")
            cursor = c.end
        if abs(cursor - duration) > 0.001:
            raise ValueError("最后一章必须结束于原音频末尾")
        for a in self.ads:
            if not (math.isfinite(a.start) and math.isfinite(a.end) and
                    0 <= a.start < a.end <= duration):
                raise ValueError("广告区间必须在原音频范围内，且开始小于结束")


def retained_spans(start: float, end: float, ads: list[Ad]) -> list[tuple[float, float]]:
    """Subtract the union of removed intervals, keeping original-source coordinates."""
    cursor = start
    kept = []
    for ad in sorted(ads, key=lambda a: a.start):
        left, right = max(start, ad.start), min(end, ad.end)
        if left >= right or right <= cursor:
            continue
        if left > cursor:
            kept.append((cursor, left))
        cursor = max(cursor, right)
    if cursor < end:
        kept.append((cursor, end))
    return kept


def remap(transcript: Transcript, spans: list[tuple[float, float]]) -> list[Segment]:
    """Map subtitles to edited audio; use word timings to avoid retaining removed speech."""
    result = []
    offset = 0.0
    for left, right in spans:
        for s in transcript.segments:
            start, end = max(left, s.start), min(right, s.end)
            if start >= end:
                continue
            words = [Word(max(w.start, left) - left + offset,
                          min(w.end, right) - left + offset, w.text)
                     for w in s.words if left <= (w.start + w.end) / 2 < right]
            if s.words and not words:
                continue
            # Without word timings, preserve a clipped cue instead of inventing text.
            text = "".join(w.text for w in words).strip() if s.words else s.text
            if not text:
                continue
            result.append(Segment(len(result), start - left + offset,
                                  end - left + offset, text, words, s.id))
        offset += right - left
    return result


def stamp(seconds: float, kind: str = "srt") -> str:
    ms = max(0, round(seconds * 1000))
    h, remainder = divmod(ms, 3600000)
    m, remainder = divmod(remainder, 60000)
    s, fraction = divmod(remainder, 1000)
    if kind == "lrc":
        return f"[{h * 60 + m:02d}:{s:02d}.{fraction // 10:02d}]"
    separator = "," if kind == "srt" else "."
    return f"{h:02d}:{m:02d}:{s:02d}{separator}{fraction:03d}"


def write_subtitles(base: Path, transcript: Transcript) -> None:
    base.with_suffix(".json").write_text(
        json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    base.with_suffix(".jsonl").write_text(
        "".join(json.dumps({"schema_version": SCHEMA_VERSION, "time_unit": "seconds",
                            **asdict(s)}, ensure_ascii=False) + "\n" for s in transcript.segments),
        encoding="utf-8")
    srt, vtt, lrc, plain = [], ["WEBVTT\n"], [], []
    for i, s in enumerate(transcript.segments, 1):
        # Keep end greater than start after millisecond rounding.
        end = max(s.end, s.start + 0.001)
        text = s.text.replace("\r", " ").replace("\n", " ").strip()
        srt.append(f"{i}\n{stamp(s.start)} --> {stamp(end)}\n{text}\n")
        vtt.append(f"{stamp(s.start, 'vtt')} --> {stamp(end, 'vtt')}\n{text}\n")
        lrc.append(f"{stamp(s.start, 'lrc')}{text}")
        lrc.append(stamp(end, "lrc"))
        plain.append(f"[{stamp(s.start, 'vtt')} - {stamp(end, 'vtt')}] {text}")
    for ext, content in (("srt", "\n".join(srt)), ("vtt", "\n".join(vtt)),
                         ("lrc", "\n".join(lrc) + "\n"), ("txt", "\n".join(plain) + "\n")):
        base.with_suffix("." + ext).write_text(content, encoding="utf-8")


def safe_name(title: str) -> str:
    name = re.sub(r'[^\w\u4e00-\u9fff -]', "_", title).strip(" ._")
    # Avoid filesystem component limits even for long multibyte titles.
    return name[:60] or "chapter"
