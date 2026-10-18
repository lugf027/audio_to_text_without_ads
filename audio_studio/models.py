"""Independent mirror downloads and strictly local model resolution."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "resources" / "models"
WHISPER_REPO = "mlx-community/whisper-large-v3-turbo"
SEMANTIC_REPO = "mlx-community/Qwen3-8B-4bit"


def model_directory(repo: str) -> Path:
    return RESOURCES / repo.replace("/", "--")


def validate_local(path: Path, kind: str) -> Path:
    required = ["config.json"]
    if kind == "whisper":
        if not any((path / name).is_file() for name in ("weights.safetensors", "weights.npz")):
            raise ValueError(f"本地 Whisper 权重未下载完整：{path}")
    else:
        required.extend(["tokenizer_config.json", "tokenizer.json"])
        if not list(path.glob("*.safetensors")):
            raise ValueError(f"本地语义模型权重未下载完整：{path}")
    if any(not (path / name).is_file() for name in required):
        raise ValueError(f"本地模型配置未下载完整：{path}")
    return path.resolve()


def resolve_model(value: str, kind: str = "whisper", local_only: bool = False) -> str:
    path = Path(value).expanduser()
    if path.is_dir():
        return str(validate_local(path, kind))
    candidate = model_directory(value)
    if candidate.exists():
        return str(validate_local(candidate, kind))
    if local_only or path.is_absolute() or value.startswith((".", "~")):
        raise ValueError(f"本地模型不存在：{value}。请先运行 python -m audio_studio.models")
    return value


def download(repo: str, endpoint: str) -> Path:
    # Environment must be set before importing huggingface_hub.
    os.environ["HF_ENDPOINT"] = endpoint
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    from huggingface_hub import HfApi, snapshot_download
    info = HfApi(endpoint=endpoint).model_info(repo, files_metadata=True)
    target = model_directory(repo)
    target.mkdir(parents=True, exist_ok=True)
    print(f"下载 {repo}\n来源 {endpoint}\n版本 {info.sha}\n目录 {target}", flush=True)
    snapshot_download(repo_id=repo, revision=info.sha, local_dir=target,
                      max_workers=2, ignore_patterns=[".gitattributes"])
    kind = "whisper" if "whisper" in repo.lower() else "semantic"
    validate_local(target, kind)
    # Verify downloaded files against repository LFS SHA-256 and declared sizes.
    import hashlib
    files = {}
    for entry in info.siblings:
        path = target / entry.rfilename
        if not path.is_file():
            continue
        if entry.size is not None and path.stat().st_size != entry.size:
            raise ValueError(f"文件大小校验失败：{path.name}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(block)
        checksum = digest.hexdigest()
        if entry.lfs and checksum != entry.lfs.sha256:
            raise ValueError(f"SHA-256 校验失败：{path.name}")
        files[entry.rfilename] = {"size": path.stat().st_size, "sha256": checksum}
    (target / "download_manifest.json").write_text(json.dumps({
        "repo": repo, "revision": info.sha, "endpoint": endpoint, "files": files,
        "note": "校验依据由所选镜像提供，并非独立于镜像的签名认证。"
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"下载和校验完成：{target}", flush=True)
    return target


def main():
    parser = argparse.ArgumentParser(description="独立下载 MLX 模型到项目 resources")
    parser.add_argument("--model", choices=["whisper", "semantic", "all"], default="all")
    parser.add_argument("--endpoint", default="https://hf-mirror.com")
    args = parser.parse_args()
    if args.model in {"whisper", "all"}:
        download(WHISPER_REPO, args.endpoint)
    if args.model in {"semantic", "all"}:
        download(SEMANTIC_REPO, args.endpoint)


if __name__ == "__main__":
    main()
