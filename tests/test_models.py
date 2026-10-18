import json
import threading

import pytest

from audio_studio import models
from audio_studio.core import Segment, Transcript
from audio_studio.semantic import AISettings, analyze


def test_resources_resolution_and_incomplete(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "RESOURCES", tmp_path)
    path = models.model_directory(models.WHISPER_REPO)
    path.mkdir()
    with pytest.raises(ValueError, match="未下载完整"):
        models.resolve_model(models.WHISPER_REPO)
    (path / "config.json").write_text("{}")
    (path / "weights.safetensors").touch()
    assert models.resolve_model(models.WHISPER_REPO) == str(path.resolve())
    with pytest.raises(ValueError, match="不存在"):
        models.resolve_model("missing/repo", local_only=True)


def test_local_semantic_no_http(monkeypatch, tmp_path):
    import mlx_lm
    import httpx
    path = tmp_path / "local"
    path.mkdir()
    for name in ["config.json", "tokenizer_config.json", "tokenizer.json", "model.safetensors"]:
        (path / name).write_text("{}")
    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            assert kwargs["enable_thinking"] is False
            return "prompt"
    monkeypatch.setattr(mlx_lm, "load", lambda path: (object(), Tokenizer()))
    class Response:
        text = json.dumps({"chapters": [{"start_id": 0, "title": "第一章"}], "ads": []})
    monkeypatch.setattr(mlx_lm, "stream_generate", lambda *args, **kwargs: iter([Response()]))
    def forbidden(*args, **kwargs):
        raise AssertionError("离线后端不应创建 HTTP 客户端")
    monkeypatch.setattr(httpx, "Client", forbidden)
    transcript = Transcript("local.wav", 10, "zh", [Segment(0, 0, 10, "第一章")])
    plan = analyze(transcript, AISettings(model=str(path), backend="mlx"),
                   threading.Event(), lambda _: None)
    assert plan.chapters[0].title == "第一章"
