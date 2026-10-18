import json
import threading
from pathlib import Path

import httpx
import pytest

from audio_studio.core import (Ad, Chapter, EditPlan, Segment, Transcript, Word,
                               remap, retained_spans, safe_name, stamp, write_subtitles)
from audio_studio.media import Cancelled, export, probe, run, tool, transcribe
from audio_studio.semantic import AISettings, analyze, parse_response


def sample():
    return Transcript("source.wav", 10, "zh", [
        Segment(0, 0, 4, "故事推广故事", [
            Word(0, 1, "故事"), Word(1, 3, "推广"), Word(3, 4, "故事")]),
        Segment(1, 4, 10, "后续正文")])


def test_overlapping_ads():
    assert retained_spans(0, 10, [Ad(2, 4), Ad(3, 6), Ad(7, 9)]) == [(0, 2), (6, 7), (9, 10)]
    assert retained_spans(3, 5, [Ad(1, 10)]) == []
    assert retained_spans(3, 5, [Ad(0, 2), Ad(6, 8)]) == [(3, 5)]


def test_remap_words_and_origin():
    mapped = remap(sample(), [(0, 1), (3, 10)])
    assert [(s.start, s.end, s.text) for s in mapped] == [
        (0, 1, "故事"), (1, 2, "故事"), (2, 8, "后续正文")]
    assert [s.source_id for s in mapped] == [0, 0, 1]
    assert mapped[1].words[0].start == 1


def test_chapter_local_time():
    mapped = remap(sample(), [(6, 10)])
    assert (mapped[0].start, mapped[0].end) == (0, 4)


@pytest.mark.parametrize("plan", [
    EditPlan([]), EditPlan([Chapter("a", 1, 10)]),
    EditPlan([Chapter("a", 0, 4), Chapter("b", 3, 10)]),
    EditPlan([Chapter("a", 0, 9)]),
    EditPlan([Chapter("a", 0, 10)], [Ad(5, 4)]),
    EditPlan([Chapter("a", 0, 10)], [Ad(0, float("nan"))]),
])
def test_invalid_plans(plan):
    with pytest.raises(ValueError):
        plan.validate(10)


def test_formats(tmp_path):
    write_subtitles(tmp_path / "source", sample())
    assert "00:00:00,000 --> 00:00:04,000" in (tmp_path / "source.srt").read_text()
    assert (tmp_path / "source.vtt").read_text().startswith("WEBVTT\n")
    assert "[00:00.00]故事推广故事" in (tmp_path / "source.lrc").read_text()
    assert Transcript.load(tmp_path / "source.json").segments[0].words[0].text == "故事"
    assert len((tmp_path / "source.jsonl").read_text().splitlines()) == 2
    assert stamp(3601.123, "lrc") == "[60:01.12]"
    assert "/" not in safe_name("../../坏名字.mp3")
    assert len(safe_name("长" * 100)) == 60


def test_invalid_transcript():
    data = sample().to_dict()
    data["segments"][1]["id"] = 0
    with pytest.raises(ValueError):
        Transcript.from_dict(data)
    data = sample().to_dict()
    data["segments"][0]["words"][0]["end"] = 30
    with pytest.raises(ValueError):
        Transcript.from_dict(data)


def test_semantic_response():
    assert parse_response('```json\n{"chapters":[],"ads":[]}\n```')["ads"] == []
    with pytest.raises(ValueError):
        parse_response('{"chapters":"bad","ads":[]}')


def test_semantic_ids(monkeypatch):
    def respond(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "chapters": [{"start_id": 1, "title": "第二章"}],
            "ads": [{"start_id": 0, "end_id": 0, "reason": "推广"}]})}}]})
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs))
    plan = analyze(sample(), AISettings(), threading.Event(), lambda _: None)
    assert [(c.start, c.end) for c in plan.chapters] == [(0, 4), (4, 10)]
    assert plan.ads[0].end == 4


def test_semantic_rejects_hallucinated_id(monkeypatch):
    original = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": '{"chapters":[{"start_id":999,"title":"假"}],"ads":[]}'}}]}))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    with pytest.raises(ValueError, match="不存在"):
        analyze(sample(), AISettings(), threading.Event(), lambda _: None)


@pytest.fixture
def audio(tmp_path):
    cancel = threading.Event()
    path = tmp_path / "input.wav"
    run([tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=10", str(path)], cancel)
    return path


def test_export_mp3_and_id3(audio, tmp_path):
    from mutagen.id3 import ID3
    transcript = sample()
    transcript.source = str(audio)
    plan = EditPlan([Chapter("第一章", 0, 5), Chapter("第二章", 5, 10)], [Ad(1, 3), Ad(2, 4)])
    result = export(audio, transcript, plan, tmp_path / "exports", "128k",
                    threading.Event(), lambda _: None)
    manifest = json.loads((result / "manifest.json").read_text())
    assert [t["duration"] for t in manifest["tracks"]] == [2, 5]
    for track in manifest["tracks"]:
        mp3 = result / track["audio"]
        assert abs(probe(mp3, threading.Event()) - track["duration"]) < 0.1
        tags = ID3(mp3)
        assert tags.getall("SYLT")[0].format == 2
        assert tags.getall("USLT")
        assert tags.getall("TIT2")[0].text[0] == track["title"]
        assert mp3.with_suffix(".lrc").exists()
        assert mp3.with_suffix(".jsonl").exists()
    assert manifest["tracks"][0]["timeline_map"][1]["output_start"] == 1
    assert "推广" not in (result / manifest["tracks"][0]["subtitle"]).read_text()
    # A second export never overwrites the first.
    second = export(audio, transcript, plan, tmp_path / "exports", "128k",
                    threading.Event(), lambda _: None)
    assert second != result and result.exists()


def test_cancel_does_not_leave_partial_export(audio, tmp_path):
    cancel = threading.Event()
    def log(_):
        cancel.set()
    with pytest.raises(Cancelled):
        export(audio, sample(), EditPlan.whole(10), tmp_path / "exports",
               "128k", cancel, log)
    assert not list((tmp_path / "exports").iterdir())
    assert audio.exists()


def test_transcription_checkpoint(audio, tmp_path, monkeypatch):
    import mlx_whisper
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls = []
    def fake(wav, **kwargs):
        calls.append(wav)
        return {"language": "zh", "segments": [{"start": 0, "end": 1, "text": "正文",
                                                "words": [{"start": 0, "end": 1, "word": "正文"}]}]}
    monkeypatch.setattr(mlx_whisper, "transcribe", fake)
    result = transcribe(audio, "test-model", "zh", "", threading.Event(), lambda _: None, 5)
    assert [s.start for s in result.segments] == [0, 5]
    resumed = transcribe(audio, "test-model", "zh", "", threading.Event(), lambda _: None, 5)
    assert len(calls) == 2 and resumed.to_dict() == result.to_dict()


@pytest.mark.parametrize("extension", ["m4a", "mp3", "mp4", "mov", "flac", "ogg"])
def test_media_containers(audio, tmp_path, extension):
    target = tmp_path / ("container." + extension)
    run([tool("ffmpeg"), "-v", "error", "-i", str(audio), str(target)], threading.Event())
    assert abs(probe(target, threading.Event()) - 10) < 0.1


def test_sample_real_media_smoke(tmp_path):
    """Real decoding/encoding only, not a claim of transcription accuracy."""
    source = Path(__file__).resolve().parents[1] / "样本_正篇第1P_第1-5集.m4a"
    if not source.exists():
        pytest.skip("项目样本不存在")
    duration = probe(source, threading.Event())
    assert duration > 6000
    clip = tmp_path / "sample.m4a"
    run([tool("ffmpeg"), "-v", "error", "-i", str(source), "-t", "8",
         "-map", "0:a:0", "-c:a", "aac", str(clip)], threading.Event())
    length = probe(clip, threading.Event())
    transcript = Transcript(str(clip), length, "zh", [])
    result = export(clip, transcript, EditPlan.whole(length), tmp_path / "real_sample",
                    "128k", threading.Event(), lambda _: None)
    track = next(result.glob("*.mp3"))
    assert abs(probe(track, threading.Event()) - length) < 0.1
