from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

from .core import (MEDIA_EXTENSIONS, SCHEMA_VERSION, EditPlan, Segment, Transcript,
                   Word, remap, retained_spans, safe_name, write_subtitles)
from .models import resolve_model

Log = Callable[[str], None]


class Cancelled(Exception):
    pass


def check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise Cancelled("已取消；已完成的转写进度会保留")


def tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"未找到 {name}，请从 audio_to_text Conda 环境启动")
    return path


def run(args: list[str], cancel: threading.Event, timeout: float = 3600) -> str:
    check_cancel(cancel)
    # A temporary stderr file prevents pipe deadlocks during long encodes.
    with tempfile.TemporaryFile() as err, tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(args, stdout=out, stderr=err)
        try:
            import time
            deadline = time.monotonic() + timeout
            while proc.poll() is None:
                if cancel.wait(0.15):
                    raise Cancelled("已取消")
                if time.monotonic() > deadline:
                    raise RuntimeError("媒体处理超时")
            err.seek(0)
            if proc.returncode:
                raise RuntimeError(err.read().decode("utf-8", errors="replace")[-4000:])
            out.seek(0)
            return out.read().decode("utf-8", errors="replace")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


def probe(path: Path, cancel: threading.Event) -> float:
    if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
        raise ValueError("请选择支持的音频或视频文件")
    data = json.loads(run([tool("ffprobe"), "-v", "error", "-show_format",
                           "-show_streams", "-of", "json", str(path)], cancel, 60))
    if not any(s.get("codec_type") == "audio" for s in data["streams"]):
        raise ValueError("此文件没有音轨")
    duration = float(data["format"]["duration"])
    if duration <= 0:
        raise ValueError("无法读取有效音频时长")
    return duration


def atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def transcribe(source: Path, model: str, language: str, prompt: str,
               cancel: threading.Event, log: Log, chunk_seconds: int = 180) -> Transcript:
    duration = probe(source, cancel)
    model = resolve_model(model)
    stat = source.stat()
    key = hashlib.sha256(json.dumps([str(source.resolve()), stat.st_size, stat.st_mtime_ns,
                                   model, language, prompt, chunk_seconds]).encode()).hexdigest()
    cache = Path.home() / "Library/Caches/BedtimeAudioStudio"
    cache.mkdir(parents=True, exist_ok=True)
    checkpoint = cache / f"{key}.json"
    state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {
        "completed": 0, "segments": [], "language": language}
    log(f"加载本地 Whisper：{model}" if Path(model).is_dir() else
        "加载 MLX Whisper；尚无项目本地资源，首次使用需下载模型")
    import mlx_whisper
    segments = [Segment(s["id"], s["start"], s["end"], s["text"],
                        [Word(**w) for w in s.get("words", [])]) for s in state["segments"]]
    with tempfile.TemporaryDirectory(prefix="audio-studio-") as temp:
        for index, offset in enumerate(range(0, int(duration) + 1, chunk_seconds)):
            if offset >= duration or index < state["completed"]:
                continue
            check_cancel(cancel)
            length = min(chunk_seconds, duration - offset)
            log(f"转写 {offset / 60:.1f}–{(offset + length) / 60:.1f} 分钟 / 共 {duration / 60:.1f} 分钟")
            wav = Path(temp) / "chunk.wav"
            run([tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-ss", str(offset),
                 "-i", str(source), "-t", str(length), "-map", "0:a:0",
                 "-ac", "1", "-ar", "16000", str(wav)], cancel)
            result = mlx_whisper.transcribe(
                str(wav), path_or_hf_repo=model, language=None if language == "auto" else language,
                initial_prompt=prompt or None, word_timestamps=True, verbose=None,
                hallucination_silence_threshold=2.0)
            check_cancel(cancel)
            for s in result["segments"]:
                start = max(offset, offset + float(s["start"]))
                end = min(offset + length, offset + float(s["end"]))
                if end <= start or not s["text"].strip():
                    continue
                words = []
                for w in s.get("words", []):
                    ws, we = max(start, offset + w["start"]), min(end, offset + w["end"])
                    if ws <= we:
                        words.append(Word(ws, we, w["word"]))
                segments.append(Segment(len(segments), start, end, s["text"].strip(), words))
            state = {"completed": index + 1, "segments": [s.__dict__ | {
                "words": [w.__dict__ for w in s.words]} for s in segments],
                     "language": result.get("language", language)}
            atomic_json(checkpoint, state)
    return Transcript(str(source.resolve()), duration, state["language"], segments, model)


def embed_subtitles(mp3: Path, transcript: Transcript, title: str, track: int) -> None:
    from mutagen.id3 import ID3, ID3NoHeaderError, SYLT, TIT2, TRCK, USLT
    try:
        tags = ID3(mp3)
    except ID3NoHeaderError:
        tags = ID3()
    lang = {"zh": "zho", "en": "eng", "ja": "jpn", "ko": "kor"}.get(transcript.language, "und")
    tags.add(TIT2(encoding=1, text=title))
    tags.add(TRCK(encoding=1, text=str(track)))
    tags.add(USLT(encoding=1, lang=lang, desc="Transcript",
                  text="\n".join(s.text for s in transcript.segments)))
    cues = []
    for s in transcript.segments:
        cues.extend([(s.text, round(s.start * 1000)), ("", round(s.end * 1000))])
    tags.add(SYLT(encoding=1, lang=lang, format=2, type=1, desc="Transcript", text=cues))
    tags.save(mp3, v2_version=3)


def export(source: Path, transcript: Transcript, plan: EditPlan, destination: Path,
           bitrate: str, cancel: threading.Event, log: Log) -> Path:
    if bitrate not in {"96k", "128k", "192k", "256k", "320k"}:
        raise ValueError("不支持的 MP3 码率")
    actual_duration = probe(source, cancel)
    if abs(actual_duration - transcript.duration) > 0.1:
        raise ValueError("当前音频时长与字幕不匹配，请重新转写")
    plan.validate(transcript.duration)
    destination.mkdir(parents=True, exist_ok=True)
    # Isolated staging + unique final directory: never overwrite existing output.
    staging = Path(tempfile.mkdtemp(prefix=".audio-studio-", dir=destination))
    manifest = {"schema_version": SCHEMA_VERSION, "time_unit": "seconds",
                "source": str(source.resolve()), "source_duration": transcript.duration, "tracks": []}
    try:
        write_subtitles(staging / "source", transcript)
        atomic_json(staging / "edit_plan.json", plan.to_dict())
        for i, chapter in enumerate(plan.chapters, 1):
            check_cancel(cancel)
            spans = retained_spans(chapter.start, chapter.end, plan.ads)
            if not spans:
                log(f"跳过完全被剪除的章节：{chapter.title}")
                continue
            name = f"{i:02d}_{safe_name(chapter.title)}"
            mp3 = staging / (name + ".mp3")
            log(f"导出 {i}/{len(plan.chapters)}：{chapter.title}")
            # Decode each retained span only once; concat PCM before a single MP3 encode.
            parts = []
            for j, (left, right) in enumerate(spans):
                pcm = staging / f"_part_{j}.wav"
                run([tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-ss", f"{left:.6f}",
                     "-i", str(source), "-t", f"{right - left:.6f}", "-map", "0:a:0",
                     "-vn", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(pcm)], cancel)
                parts.append(pcm)
            concat = staging / "_concat.txt"
            concat.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
            run([tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "1",
                 "-i", str(concat), "-map_metadata", "-1", "-c:a", "libmp3lame",
                 "-b:a", bitrate, str(mp3)], cancel)
            for p in parts:
                p.unlink()
            concat.unlink()
            mapped = Transcript(str(mp3.name), sum(b - a for a, b in spans),
                                transcript.language, remap(transcript, spans), transcript.model)
            write_subtitles(mp3.with_suffix(""), mapped)
            embed_subtitles(mp3, mapped, chapter.title, i)
            cursor = 0.0
            mapping = []
            for left, right in spans:
                mapping.append({"source_start": left, "source_end": right,
                                "output_start": cursor, "output_end": cursor + right - left})
                cursor += right - left
            manifest["tracks"].append({"title": chapter.title, "audio": mp3.name,
                                       "duration": mapped.duration, "timeline_map": mapping,
                                       "subtitle": mp3.with_suffix(".json").name})
        if not manifest["tracks"]:
            raise ValueError("所有章节都被剪除，无法导出")
        atomic_json(staging / "manifest.json", manifest)
        check_cancel(cancel)
        from datetime import datetime
        final = destination / f"{safe_name(source.stem)}_{datetime.now():%Y%m%d_%H%M%S}_{staging.name[-6:]}"
        staging.rename(final)
        return final
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
