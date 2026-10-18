#!/bin/zsh
set -e
ROOT="${0:A:h}"
CONDA="${CONDA_EXE:-$HOME/miniforge3/bin/conda}"
if [[ ! -x "$CONDA" ]]; then
  CONDA="$HOME/miniconda3/bin/conda"
fi
if [[ ! -x "$CONDA" ]]; then
  print "未找到 Conda，请激活 audio_to_text 后运行 python -m audio_studio.models"
  read "?按回车退出"
  exit 1
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
print "将从第三方 hf-mirror.com 下载约 6.3 GB 模型到项目 resources/models。"
"$CONDA" run --no-capture-output -n audio_to_text python -m audio_studio.models "$@"
read "?下载完成，按回车退出"
