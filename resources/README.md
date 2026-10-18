# 本地模型资源

模型独立存放在本目录，不打包到源代码，也不提交 Git。

```sh
conda activate audio_to_text
python -m audio_studio.models
```

默认使用第三方镜像 `https://hf-mirror.com`，固定查询到的仓库版本，下载后核对文件大小及 LFS SHA-256。镜像提供的校验元数据不是独立签名认证，请仅在信任该镜像时使用。镜像大文件可能跳转到 Hugging Face 的 CDN，所以国内可达性和速度不能保证。

也可选择官方源：

```sh
python -m audio_studio.models --endpoint https://huggingface.co
```

或只下载一个模型：

```sh
python -m audio_studio.models --model whisper
python -m audio_studio.models --model semantic
```

实际目录：

- `models/mlx-community--whisper-large-v3-turbo/`：约 1.61 GB 权重，本地音频转写。
- `models/mlx-community--Qwen3-8B-4bit/`：约 4.6 GB 权重及 tokenizer，本地语义分析。

应预留至少 8 GB 空间。下载工具支持由 Hugging Face Hub 管理的断点恢复；失败后运行相同命令。每个完成的目录会生成 `download_manifest.json`，记录镜像、仓库版本、文件大小及 SHA-256。

程序优先查找本目录中的 Whisper 模型。GUI 的「本地 MLX」语义后端直接加载 Qwen3 目录，无需 Ollama、HTTP 服务或 API Key。也可将完整 MLX 模型目录复制到离线电脑，填写绝对路径。不要放入 PyTorch 权重或 GGUF 文件，它们不能由本实现直接加载。

推理阶段无需镜像；可设置 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` 启动，以防止依赖尝试联网。首次准备模型需要联网，或由其他电脑下载后拷入。

## 本机验证记录

2026-10-18：两套模型已下载完成，文件大小和 LFS SHA-256 校验通过；占用约 1.5 GiB + 4.3 GiB（合计约 6.3 GB 十进制）。

- 强制 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`，Whisper 成功转写用户样本前 30 秒，输出两条带时间字幕；有错字，尚未对整段 107 分钟音频评估准确率。
- 同样强制离线，Qwen3 在人工构造的六句测试文本上成功返回结构化 JSON，识别两集和推广。模型将广告后的正文句一并包含在广告建议内，存在误删风险，必须试听审核。该测试不是对用户样本的真实广告检测评估。
- 27 项自动化测试通过；GUI 本地模型模式已渲染检查。

下载工具可双击项目根目录 `下载离线模型.command`，重复运行会复用已完成文件。程序界面需重新打开才能载入新代码，默认语义方式为「本地 MLX（resources，无需 Key）」。
