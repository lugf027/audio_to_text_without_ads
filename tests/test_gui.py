import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from audio_studio.core import Ad, Chapter, EditPlan, Segment, Transcript
from audio_studio.gui import Studio


def test_gui_editing_and_state(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "sample.wav"
    source.touch()
    window = Studio()
    window.set_transcript(Transcript(str(source), 10, "zh", [Segment(0, 0, 10, "正文")]))
    assert window.cues.rowCount() == 1
    window.cues.item(0, 3).setText("修正正文")
    assert window.edited_transcript().segments[0].text == "修正正文"
    window.set_plan(EditPlan([Chapter("甲", 0, 5), Chapter("乙", 5, 10)], [Ad(2, 3, "推广")]))
    assert window.get_plan().ads[0].start == 2
    assert not window.remove_ads.isChecked()
    window.plan_to_json()
    window.apply_plan_json()
    assert window.chapters.rowCount() == 2
    window.show()
    app.processEvents()
    screenshot = os.environ.get("AUDIO_STUDIO_TEST_SCREENSHOT")
    if screenshot:
        window.grab().save(screenshot)
    window.source.setText(str(tmp_path / "different.mp3"))
    window.media_changed()
    assert window.transcript is None
    assert window.cues.rowCount() == 0
    window.close()
