from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QSettings, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QSlider, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt

from .core import Ad, Chapter, EditPlan, Transcript, number, stamp, write_subtitles
from .media import Cancelled, export, transcribe
from .semantic import AISettings, analyze
from .models import SEMANTIC_REPO, model_directory


class Worker(QThread):
    message = Signal(str)
    success = Signal(object)
    failed = Signal(str)

    def __init__(self, task):
        super().__init__()
        self.task = task
        self.cancel = threading.Event()

    def run(self):
        try:
            self.success.emit(self.task(self.cancel, self.message.emit))
        except Cancelled as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("听书工坊 · Bedtime Audio Studio")
        self.resize(1200, 850)
        self.settings = QSettings("BedtimeAudioStudio", "Studio")
        self.transcript = None
        self.worker = None
        self.busy = False
        self.last_export = None
        self.controls = []
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        heading = QLabel("听书工坊")
        heading.setStyleSheet("font-size: 28px; font-weight: 600; color: #e3ccb0;")
        layout.addWidget(heading)
        layout.addWidget(QLabel("本地转写 · 语义分集 · 广告审核 · 睡前音频准备"))
        self.source = QLineEdit()
        self.source.setPlaceholderText("选择文件，或粘贴音频 / 视频的绝对路径")
        self.source.editingFinished.connect(self.media_changed)
        choose = QPushButton("选择媒体")
        choose.clicked.connect(self.choose_media)
        self.controls.extend([self.source, choose])
        row = QHBoxLayout()
        row.addWidget(self.source, 1)
        row.addWidget(choose)
        layout.addLayout(row)
        split = QSplitter()
        layout.addWidget(split, 1)
        options = QWidget()
        form = QFormLayout(options)
        split.addWidget(options)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(["mlx-community/whisper-large-v3-turbo",
                             "mlx-community/whisper-small-mlx",
                             "mlx-community/whisper-medium-mlx",
                             "mlx-community/whisper-large-v3-mlx"])
        self.language = QComboBox()
        for title, value in [("中文", "zh"), ("自动识别", "auto"), ("英语", "en"), ("日语", "ja")]:
            self.language.addItem(title, value)
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("可选：人名、地名、专有名词")
        self.start = QPushButton("1. 本地转写 / 续传")
        self.start.clicked.connect(self.start_transcribe)
        self.import_button = QPushButton("导入转写 JSON")
        self.import_button.clicked.connect(self.import_transcript)
        form.addRow("Whisper 模型", self.model)
        form.addRow("语言", self.language)
        form.addRow("转写提示", self.prompt)
        form.addRow(self.start)
        form.addRow(self.import_button)
        form.addRow(QLabel("AI 语义分析（可选）"))
        self.backend = QComboBox()
        self.backend.addItem("本地 MLX（resources，无需 Key）", "mlx")
        self.backend.addItem("Ollama / 在线兼容 API", "api")
        self.backend.currentIndexChanged.connect(self.backend_changed)
        form.addRow("语义推理方式", self.backend)
        self.base_url = QLineEdit(self.settings.value("base_url", "http://localhost:11434/v1"))
        self.ai_model = QLineEdit(str(model_directory(SEMANTIC_REPO)))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("本地 Ollama 可留空；不持久化保存")
        self.instruction = QPlainTextEdit(AISettings().instruction)
        self.instruction.setMaximumHeight(95)
        self.ai_button = QPushButton("2. 识别章节及广告")
        self.ai_button.clicked.connect(self.start_analyze)
        form.addRow("API Base URL", self.base_url)
        form.addRow("AI 模型", self.ai_model)
        form.addRow("API Key", self.key)
        form.addRow("剪辑要求", self.instruction)
        form.addRow(self.ai_button)
        form.addRow(QLabel("MLX 模式不联网；云端模式会发送字幕文本。"))
        self.remove_ads = QCheckBox("已审核，导出时剪除广告")
        self.remove_ads.setChecked(False)
        self.bitrate = QComboBox()
        self.bitrate.addItems(["128k", "96k", "192k", "256k", "320k"])
        self.export_button = QPushButton("3. 导出分集 MP3 + 字幕")
        self.export_button.clicked.connect(self.start_export)
        self.sub_button = QPushButton("仅导出原始字幕")
        self.sub_button.clicked.connect(self.export_subtitles)
        self.open_button = QPushButton("打开最近导出目录")
        self.open_button.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self.last_export))) if self.last_export else None)
        form.addRow(self.remove_ads)
        form.addRow("MP3 码率", self.bitrate)
        form.addRow(self.export_button)
        form.addRow(self.sub_button)
        form.addRow(self.open_button)
        self.controls.extend([self.backend, self.model, self.language, self.prompt, self.start,
                              self.import_button, self.base_url, self.ai_model, self.key,
                              self.instruction, self.ai_button, self.remove_ads,
                              self.bitrate, self.export_button, self.sub_button])
        tabs = QTabWidget()
        split.addWidget(tabs)
        split.setSizes([380, 820])
        self.cues = QTableWidget(0, 4)
        self.cues.setHorizontalHeaderLabels(["ID", "开始（秒）", "结束（秒）", "字幕（可编辑）"])
        self.cues.horizontalHeader().setStretchLastSection(True)
        self.cues.cellDoubleClicked.connect(self.seek_cue)
        self.controls.append(self.cues)
        tabs.addTab(self.cues, "字幕 · 双击试听")
        self.chapters = QTableWidget(0, 3)
        self.chapters.setHorizontalHeaderLabels(["章节名", "开始（秒）", "结束（秒）"])
        self.chapters.horizontalHeader().setStretchLastSection(True)
        self.chapters.cellDoubleClicked.connect(
            lambda row, col: self.seek_table(self.chapters, row, 1))
        self.ads = QTableWidget(0, 3)
        self.ads.setHorizontalHeaderLabels(["开始（秒）", "结束（秒）", "剪除原因"])
        self.ads.horizontalHeader().setStretchLastSection(True)
        self.ads.cellDoubleClicked.connect(lambda row, col: self.seek_table(self.ads, row, 0))
        self.ads.itemChanged.connect(lambda item: self.remove_ads.setChecked(False))
        for table, label in [(self.chapters, "章节审核"), (self.ads, "广告审核")]:
            panel = QWidget()
            box = QVBoxLayout(panel)
            box.addWidget(QLabel("原音频时间轴，单位：秒。章节需连续覆盖完整音频。"))
            box.addWidget(table)
            buttons = QHBoxLayout()
            add = QPushButton("添加区间")
            delete = QPushButton("删除选中行")
            add.clicked.connect(lambda checked=False, t=table: t.insertRow(t.rowCount()))
            delete.clicked.connect(lambda checked=False, t=table: self.delete_rows(t))
            buttons.addWidget(add)
            buttons.addWidget(delete)
            box.addLayout(buttons)
            tabs.addTab(panel, label)
            self.controls.extend([table, add, delete])
        self.plan_editor = QPlainTextEdit()
        self.plan_editor.setPlaceholderText("可粘贴或导入 edit_plan.json；点击应用后更新审核表格")
        plan_panel = QWidget()
        plan_layout = QVBoxLayout(plan_panel)
        plan_layout.addWidget(self.plan_editor)
        plan_buttons = QHBoxLayout()
        for label, callback in [("表格 → JSON", self.plan_to_json),
                                ("应用 JSON", self.apply_plan_json),
                                ("导入方案", self.import_plan), ("保存方案", self.save_plan)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            plan_buttons.addWidget(button)
            self.controls.append(button)
        plan_layout.addLayout(plan_buttons)
        self.controls.append(self.plan_editor)
        tabs.addTab(plan_panel, "剪辑 JSON")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        tabs.addTab(self.log, "处理日志")
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.7)
        self.player.setAudioOutput(self.audio)
        self.play = QPushButton("播放 / 暂停")
        self.play.clicked.connect(self.toggle_play)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(lambda duration: self.slider.setRange(0, duration))
        self.player.errorOccurred.connect(lambda err, message: self.log.appendPlainText(f"试听错误：{message}"))
        self.position = QLabel("00:00:00.000")
        playback = QHBoxLayout()
        playback.addWidget(self.play)
        playback.addWidget(self.slider, 1)
        playback.addWidget(self.position)
        layout.addLayout(playback)
        self.status = QLabel("就绪 · 首次转写需联网下载模型；随后可离线使用")
        self.cancel_button = QPushButton("取消当前任务")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_task)
        footer = QHBoxLayout()
        footer.addWidget(self.status, 1)
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)
        self.setStyleSheet("""
            QWidget { background: #20272b; color: #e6e5df; font-size: 13px; }
            QLineEdit, QPlainTextEdit, QTableWidget, QComboBox {
                background: #293338; border: 1px solid #455158; border-radius: 5px; padding: 5px; }
            QPushButton { background: #3d5357; border: 1px solid #536f73;
                border-radius: 5px; padding: 8px; }
            QPushButton:hover { background: #506b70; }
            QPushButton:disabled { color: #808d90; background: #303b40; }
            QHeaderView::section { background: #36454b; padding: 7px; }
            QTabBar::tab { padding: 9px; }
            QTabBar::tab:selected { color: #e3ccb0; }
        """)
        self.backend_changed()
        if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
            self.source.setText(str(Path(sys.argv[1]).resolve()))
            self.media_changed()

    def message(self, text):
        self.status.setText(text)
        self.log.appendPlainText(text)

    def backend_changed(self):
        local = self.backend.currentData() == "mlx"
        self.base_url.setEnabled(not local)
        self.key.setEnabled(not local)
        self.ai_model.setText(str(model_directory(SEMANTIC_REPO)) if local else
                              self.settings.value("ai_model", "qwen3:8b"))

    def error(self, exc):
        self.message(str(exc))
        QMessageBox.warning(self, "请检查", str(exc))

    def path(self):
        return Path(self.source.text().strip().strip("'\"")).expanduser().resolve()

    def choose_media(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择音频或视频", str(Path.home()),
            "音频视频 (*.m4a *.mp3 *.mp4 *.mov *.wav *.aac *.flac *.ogg *.opus *.mkv *.webm *.aiff *.aif *.wma *.m4b)")
        if path:
            self.source.setText(path)
            self.media_changed()

    def media_changed(self):
        path = self.path()
        if self.transcript and path != Path(self.transcript.source).resolve():
            self.transcript = None
            self.cues.setRowCount(0)
            self.chapters.setRowCount(0)
            self.ads.setRowCount(0)
            self.plan_editor.clear()
            self.remove_ads.setChecked(False)
        self.player.setSource(QUrl.fromLocalFile(str(path)))

    def begin(self, task, done):
        if self.busy:
            return
        self.busy = True
        for control in self.controls:
            control.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.worker = Worker(task)
        self.worker.message.connect(self.message)
        self.worker.success.connect(done)
        self.worker.failed.connect(self.task_failed)
        self.worker.finished.connect(self.finish)
        self.worker.start()

    def task_failed(self, message):
        if message.startswith("已取消"):
            self.message(message)
        else:
            self.error(message)

    def finish(self):
        self.busy = False
        for control in self.controls:
            control.setEnabled(True)
        local = self.backend.currentData() == "mlx"
        self.base_url.setEnabled(not local)
        self.key.setEnabled(not local)
        self.cancel_button.setEnabled(False)
        if self.worker:
            self.worker.deleteLater()
            self.worker = None

    def cancel_task(self):
        if self.worker:
            self.worker.cancel.set()
            self.message("正在取消；需等待当前 MLX 推理或 AI 请求返回")

    def start_transcribe(self):
        source, model = self.path(), self.model.currentText().strip()
        language, prompt = self.language.currentData(), self.prompt.text().strip()
        self.player.pause()
        self.begin(lambda cancel, log: transcribe(source, model, language, prompt, cancel, log),
                   self.set_transcript)

    def set_transcript(self, transcript):
        self.transcript = transcript
        self.source.setText(transcript.source)
        self.player.setSource(QUrl.fromLocalFile(transcript.source))
        self.cues.setRowCount(len(transcript.segments))
        for row, s in enumerate(transcript.segments):
            for col, value in enumerate([s.id, f"{s.start:.3f}", f"{s.end:.3f}", s.text]):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.cues.setItem(row, col, item)
        self.cues.setColumnWidth(0, 55)
        self.cues.setColumnWidth(1, 110)
        self.cues.setColumnWidth(2, 110)
        self.set_plan(EditPlan.whole(transcript.duration))
        self.message(f"转写已就绪：{len(transcript.segments)} 条字幕，{transcript.duration / 60:.1f} 分钟")

    def edited_transcript(self):
        if not self.transcript:
            raise ValueError("请先转写或导入 JSON")
        if Path(self.transcript.source).resolve() != self.path():
            raise ValueError("媒体已变更，请重新转写")
        data = self.transcript.to_dict()
        for row, s in enumerate(data["segments"]):
            start = number(self.cues.item(row, 1).text())
            end = number(self.cues.item(row, 2).text())
            text = self.cues.item(row, 3).text().strip()
            # User corrections invalidate the corresponding model word alignment.
            if start != round(s["start"], 3) or end != round(s["end"], 3) or text != s["text"]:
                s["words"] = []
                s.update(start=start, end=end, text=text)
        return Transcript.from_dict(data)

    def import_transcript(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入原音频转写 JSON", "", "JSON (*.json)")
        if not path:
            return
        try:
            transcript = Transcript.load(Path(path))
            if not Path(transcript.source).is_file():
                if not self.path().is_file():
                    raise ValueError("JSON 中的原文件不存在，请先选择对应媒体")
                transcript.source = str(self.path())
            self.set_transcript(transcript)
        except Exception as exc:
            self.error(exc)

    def set_plan(self, plan):
        for table, rows in [(self.chapters, [[c.title, c.start, c.end] for c in plan.chapters]),
                            (self.ads, [[a.start, a.end, a.reason] for a in plan.ads])]:
            table.setRowCount(len(rows))
            for i, row in enumerate(rows):
                for j, value in enumerate(row):
                    table.setItem(i, j, QTableWidgetItem(
                        f"{value:.9f}" if isinstance(value, (int, float)) else str(value)))
        self.plan_editor.setPlainText(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
        self.remove_ads.setChecked(False)

    def get_plan(self):
        transcript = self.edited_transcript()
        def cell(table, row, col):
            item = table.item(row, col)
            if item is None or not item.text().strip():
                raise ValueError("请填写所有区间单元格")
            return item.text()
        chapters = [Chapter(cell(self.chapters, r, 0), number(cell(self.chapters, r, 1)),
                            number(cell(self.chapters, r, 2))) for r in range(self.chapters.rowCount())]
        ads = [Ad(number(cell(self.ads, r, 0)), number(cell(self.ads, r, 1)),
                  self.ads.item(r, 2).text() if self.ads.item(r, 2) else "")
               for r in range(self.ads.rowCount())]
        plan = EditPlan(chapters, ads)
        plan.validate(transcript.duration)
        return plan

    def delete_rows(self, table):
        for row in sorted({i.row() for i in table.selectedIndexes()}, reverse=True):
            table.removeRow(row)
        self.remove_ads.setChecked(False)

    def start_analyze(self):
        try:
            transcript = self.edited_transcript()
            settings = AISettings(self.base_url.text().strip(), self.ai_model.text().strip(),
                                  self.key.text(), self.instruction.toPlainText(),
                                  self.backend.currentData())
            if settings.backend == "api" and not settings.base_url.startswith(
                    ("http://localhost", "http://127.0.0.1", "http://[::1]")):
                if QMessageBox.question(self, "字幕隐私确认",
                                        "此操作会向填写的 AI 服务发送字幕文本，是否继续？") != QMessageBox.Yes:
                    return
            self.settings.setValue("base_url", settings.base_url)
            if settings.backend == "api":
                self.settings.setValue("ai_model", settings.model)
            self.begin(lambda cancel, log: analyze(transcript, settings, cancel, log),
                       self.analysis_done)
        except Exception as exc:
            self.error(exc)

    def analysis_done(self, plan):
        self.set_plan(plan)
        self.message(f"发现 {len(plan.chapters)} 个章节、{len(plan.ads)} 个广告区间，请审核并试听")

    def start_export(self):
        try:
            transcript, plan = self.edited_transcript(), self.get_plan()
            remove = self.remove_ads.isChecked()
            if remove:
                if QMessageBox.question(self, "确认剪辑",
                                        f"将剪除 {len(plan.ads)} 个广告区间。已试听检查边界吗？") != QMessageBox.Yes:
                    return
            else:
                plan = EditPlan(plan.chapters, [])
            path = QFileDialog.getExistingDirectory(self, "选择导出父目录", str(self.path().parent))
            if not path:
                return
            bitrate, source = self.bitrate.currentText(), self.path()
            self.begin(lambda cancel, log: export(source, transcript, plan, Path(path), bitrate, cancel, log),
                       self.export_done)
        except Exception as exc:
            self.error(exc)

    def export_done(self, path):
        self.last_export = path
        self.message(f"导出完成：{path}")

    def export_subtitles(self):
        try:
            transcript = self.edited_transcript()
            path = QFileDialog.getExistingDirectory(self, "选择字幕导出父目录", str(self.path().parent))
            if path:
                import tempfile
                folder = Path(tempfile.mkdtemp(prefix="subtitles_", dir=path))
                write_subtitles(folder / "source", transcript)
                self.export_done(folder)
        except Exception as exc:
            self.error(exc)

    def plan_to_json(self):
        try:
            self.plan_editor.setPlainText(json.dumps(self.get_plan().to_dict(), ensure_ascii=False, indent=2))
        except Exception as exc:
            self.error(exc)

    def apply_plan_json(self):
        try:
            self.set_plan(EditPlan.from_dict(json.loads(self.plan_editor.toPlainText()),
                                             self.edited_transcript().duration))
        except Exception as exc:
            self.error(exc)

    def import_plan(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入剪辑方案", "", "JSON (*.json)")
        if path:
            try:
                self.plan_editor.setPlainText(Path(path).read_text(encoding="utf-8"))
                self.apply_plan_json()
            except Exception as exc:
                self.error(exc)

    def save_plan(self):
        try:
            plan = self.get_plan()
            path, _ = QFileDialog.getSaveFileName(self, "保存剪辑方案", "edit_plan.json", "JSON (*.json)")
            if path:
                target = Path(path)
                if target.suffix != ".json":
                    target = target.with_suffix(".json")
                target.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self.error(exc)

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def position_changed(self, position):
        if not self.slider.isSliderDown():
            self.slider.setValue(position)
        self.position.setText(stamp(position / 1000, "vtt"))

    def seek_cue(self, row, _col):
        self.seek_table(self.cues, row, 1)

    def seek_table(self, table, row, col):
        try:
            item = table.item(row, col)
            if item is None:
                return
            self.player.setPosition(round(number(item.text()) * 1000))
            self.player.play()
        except Exception as exc:
            self.error(exc)

    def closeEvent(self, event):
        if self.busy:
            self.cancel_task()
            self.message("正在停止任务，请任务结束后关闭窗口")
            event.ignore()
            return
        self.player.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("BedtimeAudioStudio")
    studio = Studio()
    studio.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
