# 听书工坊 · Bedtime Audio Studio

Apple Silicon 桌面音频准备工具。输入音频或视频，使用 MLX Whisper 本地转写，输出带时间的字幕；按语义审核章节、剪除广告，统一导出 MP3。此项目不包含移动播放器。

## 启动

本机已创建 `audio_to_text` Conda 环境并安装依赖。双击项目中的 `启动听书工坊.command`，或运行：

```sh
conda activate audio_to_text
audio-studio
```

也可打开指定文件：

```sh
audio-studio "/Users/donaldlu/Documents/workspace/my_resources/audio_to_text/样本_正篇第1P_第1-5集.m4a"
```

其他机器安装：

```sh
conda env create -f environment.yml
conda activate audio_to_text
python -m pip install -e '.[test]'
```

需在本项目目录执行安装命令。当前转写实现针对 macOS arm64，使用 Apple MLX / Metal，不使用 CUDA。支持 M 系列芯片，包括您的 M5 Pro。

## 使用流程

1. 选择媒体或粘贴路径。支持 m4a、mp3、mp4、mov、wav、aac、flac、ogg、opus、mkv、webm、aiff、wma、m4b；视频提取第一条音轨，不保留画面。实际可读性仍取决于 FFmpeg 支持的编码，DRM 文件不支持。
2. 选转写模型及语言，点击「本地转写 / 续传」。默认 `mlx-community/whisper-large-v3-turbo`；较小模型可用 `mlx-community/whisper-small-mlx`。首次从 Hugging Face 下载模型，此后使用本地缓存，也可以直接填已下载 MLX 模型目录。
3. 查看字幕，双击定位原音频试听；可直接修改字幕和秒数。修改的句子会清除旧词级对齐，防止错误沿用旧词时间。
4. 可选：连接 AI 识别章节和广告。编辑剪辑要求，比如「只删除某某平台的订阅推广，保留书名和开场介绍」。也可不使用 AI，手工填章节及广告时间，或导入剪辑 JSON。
5. 审核章节表、广告表，双击区间定位原音频，并使用下方播放器试听边界。章节必须从 0 开始、连续覆盖整个文件；广告可重叠，导出会合并。广告识别是建议，不保证识别正确或完整。
6. 默认不剪广告。确认后勾选「已审核，导出时剪除广告」。导出会再次确认并创建独立目录，原媒体不修改，旧输出不覆盖。

长音频按 180 秒转写，完成每段后保存在 `~/Library/Caches/BedtimeAudioStudio/`。取消后，相同原文件、模型、语言及提示可续传。取消须等当前 MLX 推理或 AI HTTP 请求结束，关闭窗口时也会先停止任务。固定转写片段边界可能截断一句话，需要审核；目前没有强制对齐或声纹分离。

## AI 服务

### 项目 resources 全离线模式（默认）

当前 GUI 默认「本地 MLX」，直接从项目加载 Qwen3，不需要 Ollama 或 API Key。先独立下载模型：

```sh
conda activate audio_to_text
python -m audio_studio.models
```

默认第三方国内镜像 `https://hf-mirror.com`，支持恢复下载、固定模型版本和文件校验。Whisper Turbo 与 Qwen3 8B 四位量化模型分别存放于 `resources/models/`。Whisper 填仓库名时优先查找该目录；语义模型输入框默认是项目本地路径。详见 [资源说明](resources/README.md)。

完成下载后可强制离线启动：

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 audio-studio
```

您的 M5 Pro 48 GB 可以从这组模型开始。运行时不发送字幕，MLX 不加载远端自定义代码。API 方式仍可在「语义推理方式」中切换。

### 本地 Ollama

仅选择 Ollama 后端时需要单独安装；默认 MLX 后端不需要。安装后：

```sh
ollama pull qwen3:8b
ollama serve
```

已启动 Ollama App 时无需重复执行 `serve`。GUI 使用：

- Base URL：`http://localhost:11434/v1`
- 模型：`qwen3:8b`，也可选择已安装的其他指令模型
- API Key：留空

本地转写模型与 Ollama 大模型会占用统一内存，建议从小模型开始。服务必须支持 `/v1/chat/completions`，模型必须能输出所约定的 JSON。

### 兼容 OpenAI 的服务

填写服务的 Base URL（通常以 `/v1` 结尾）、模型和 Key。API Key 仅在内存使用，不写配置。程序会发送转写文本和剪辑要求，不发送音视频文件；非默认本地地址会出现隐私确认。API 可能产生费用。

AI 按有重叠的文本批次分析，只能引用已有字幕 ID，再由程序转换成真实时间。非法 ID 或无效响应会停止，不自动执行剪除。没有语义模型时仍可完整转写、导出、手动剪辑。

## 导出数据

每个成功结果目录包含：

| 文件 | 用途 |
| --- | --- |
| `source.json` | 原始完整转写，含源路径、时长、模型、句级及词级时间，适合进一步 AI 处理 |
| `source.jsonl` | 每行一个字幕段，适合流式处理；媒体元数据见同名 JSON |
| `source.srt / .vtt / .lrc / .txt` | 原时间轴字幕、同步歌词、带时间纯文本 |
| `edit_plan.json` | 实际采用的章节和广告剪除方案 |
| `01_章节名.mp3` | 固定码率 MP3，默认 128 kbps、44.1 kHz、双声道 |
| `01_章节名.json / .jsonl / .srt / .vtt / .lrc / .txt` | 分集及剪广告后的新时间轴字幕 |
| `manifest.json` | 播放列表和每段保留音频的原始 → 输出时间映射 |

JSON 顶层带 `schema_version: "1.0"` 和 `time_unit: "seconds"`，所有时间单位为秒、区间为 `[start, end)`。字幕段包含 `id/start/end/text/words/source_id`。剪辑后的 `source_id` 指向原字幕 ID；一句跨越剪辑边界时可生成多个字幕段。没有词级时间的手动字幕只能保留整句文字并裁剪时间，无法可靠判断应删除哪些字。

MP3 写入 ID3v2.3：

- `SYLT`：同步字幕，毫秒时间（format=2），UTF-16。
- `USLT`：无时间完整文本，UTF-16。
- `TIT2 / TRCK`：章节名和序号。

**不同播放器对 SYLT 支持差异较大。** LRC 是外置文件，不是 ID3 标签，已与 MP3 一起导出。后续自己的移动播放器建议以 `manifest.json` + 分集 JSON 为主要数据来源，LRC 作为兼容回退。每集字幕从自身 0 秒时间轴计算，不沿用原文件时间。

导出每段保留内容为临时 PCM，拼接后只编码一次 MP3，避免重复压缩。临时磁盘需求约每小时 635 MB（立体声 PCM），加输出文件；请保证空间。MP3 编码延迟和边界采样取整可能造成几十毫秒差异。此版本不做音量标准化、淡入淡出、批量队列或词级强制对齐。

## 剪辑方案示例

以下仅说明结构，不是样本的真实章节或广告：

```json
{
  "schema_version": "1.0",
  "time_unit": "seconds",
  "chapters": [
    {"title": "第一集", "start": 0, "end": 1200},
    {"title": "第二集", "start": 1200, "end": 2400}
  ],
  "ads": [
    {"start": 25.5, "end": 40.8, "reason": "审核确认的平台推广"}
  ]
}
```

保存方案与导出方案分离：取消勾选剪除广告时，导出的 `edit_plan.json` 不含未实际采用的广告。请先保存审核方案，以便后续重复使用。

## 验证

```sh
conda activate audio_to_text
python -m pytest -q
```

本机 27 项测试通过，包括时间轴、重叠广告、导出 MP3、内嵌字幕读取、取消清理、模拟转写断点续传、模拟 AI JSON、GUI 状态、本地资源优先加载、本地语义模式不创建 HTTP 客户端及 m4a/mp3/mp4/mov/flac/ogg 输入。另对用户样本前 8 秒完成真实解码和 MP3 导出验证，测试产物在临时目录。模拟测试不代表真实识别准确率。

实际样本音轨已通过 FFprobe 读取：AAC / 44.1 kHz，约 6429.791 秒（107.2 分钟）。初版 Hugging Face 官方源连接超时，后续增加国内镜像独立下载和 MLX 本地语义后端。下载及实际推理状态以 `resources/README.md` 中的验证记录为准。不要把普通 Whisper PyTorch 权重直接当作 MLX 模型目录。

## 开源参考与授权

本项目自行实现 GUI、剪辑管线和导出格式，使用这些开源依赖及其公开接口：

- [MLX Examples / Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper)：`mlx-whisper` 在 Apple Silicon 上运行 Whisper，MIT。
- [OpenAI Whisper](https://github.com/openai/whisper)：多语言语音识别、段及词时间戳接口，MIT。
- [stable-ts](https://github.com/jianfch/stable-ts)：字幕精细时间处理的后续参考，本版本未集成或宣称具有其对齐能力，MIT。
- [FFmpeg](https://github.com/FFmpeg/FFmpeg)：媒体解码、剪辑、MP3 编码。许可取决于所用构建；本环境 conda-forge 构建包含 GPL 组件。
- [PySide / Qt for Python](https://github.com/pyside/pyside-setup)：桌面 GUI 和媒体试听，LGPL/GPL 或商业授权。
- [Mutagen](https://github.com/quodlibet/mutagen)：ID3 SYLT / USLT 写入，GPL-2.0-or-later。

当前 GitHub CLI 没有认证，仓库详情查询未成功；实现验证依据已安装 `mlx-whisper` 源码接口及 MLX / Mutagen 官方文档，未复制仓库实现。若重新分发应用或打包商业产品，应审查全部依赖许可，尤其 Mutagen、Qt 和 FFmpeg；本版本未附加一个与依赖不匹配的宽松许可证。
