"""Regression coverage for the September 12 Sentry fixes."""

import copy
import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import openshot
from qt_api import QComboBox, QLabel, QRect
from classes.query import Clip
from tests.qt_test_app import get_or_create_app
from tests.test_project_data import DummyApp, ensure_app_state, make_store


class SentrySeptemberTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, _ = get_or_create_app(DummyApp)
        ensure_app_state(cls.app)
        metrics = types.ModuleType("classes.metrics")
        metrics.track_metric_screen = Mock()
        metrics.track_metric_error = Mock()
        with patch.dict(sys.modules, {"classes.metrics": metrics}):
            cls.export = importlib.import_module("windows.export")
            cls.title = importlib.import_module("windows.title_editor")
            cls.video = importlib.import_module("windows.video_widget")
            cls.timeline = importlib.import_module("windows.views.timeline")

    def test_preview_title_before_worker_start_and_after_shutdown(self):
        dock = Mock()
        helper = Mock()
        helper.centeredViewport.return_value = QRect(0, 0, 320, 180)
        helper.devicePixelRatioF.return_value = 1
        helper.parent.return_value.parent.return_value = dock
        helper.settings = {"preview-fps": False}
        helper.region_enabled = False
        helper.transforming_effect = False
        helper.transforming_clips = False
        player = Mock()
        player.Mode.return_value = openshot.PLAYBACK_PLAY
        player.Speed.return_value = 2.0
        app = types.SimpleNamespace(_tr=lambda text: text)
        with patch.object(self.video, "get_app", return_value=app):
            for window in (types.SimpleNamespace(), types.SimpleNamespace(preview_thread=None)):
                helper.win = window
                self.video.VideoWidget.update_title(helper)
                dock.setWindowTitle.assert_called_with("Video Preview")
            helper.win = types.SimpleNamespace(preview_thread=types.SimpleNamespace(player=player))
            self.video.VideoWidget.update_title(helper)
            dock.setWindowTitle.assert_called_with("Video Preview (2.0x)")
            player.Mode.return_value = openshot.PLAYBACK_PAUSED
            self.video.VideoWidget.update_title(helper)
            dock.setWindowTitle.assert_called_with("Video Preview")

    def test_channel_layout_combo_clear_preserves_native_layout(self):
        combo = QComboBox()
        timeline = openshot.Timeline(320, 180, openshot.Fraction(30, 1),
                                     44100, 2, openshot.LAYOUT_STEREO)
        helper = Mock()
        helper.timeline = timeline
        helper.cboChannelLayout = combo
        for name, value in {"Width": 320, "Height": 180, "FrameRateNum": 30,
                            "FrameRateDen": 1, "SampleRate": 44100, "Channels": 2}.items():
            getattr(helper, "txt" + name).value.return_value = value
        errors = []

        def changed(index):
            try:
                self.export.Export.updateFrameRate(helper, False)
            except Exception as ex:
                errors.append(ex)

        app = types.SimpleNamespace(project={"fps": {"num": 30, "den": 1}})
        with patch.object(self.export, "get_app", return_value=app):
            combo.currentIndexChanged.connect(changed)
            combo.addItem("Mono", openshot.LAYOUT_MONO)
            self.assertEqual(timeline.info.channel_layout, openshot.LAYOUT_MONO)
            combo.clear()
            self.assertEqual(timeline.info.channel_layout, openshot.LAYOUT_MONO)
            combo.addItem("Stereo", openshot.LAYOUT_STEREO)
            self.assertEqual(timeline.info.channel_layout, openshot.LAYOUT_STEREO)
        self.assertEqual(errors, [])

    def test_failed_asset_creation_aborts_save_before_mutating_project(self):
        store = make_store()
        store._data = {"clips": [], "files": []}
        store.current_filepath = "original.osp"
        store.has_unsaved_changes = True
        original = copy.deepcopy(store._data)
        with tempfile.TemporaryDirectory() as root:
            destination = os.path.join(root, "new.osp")
            with patch("classes.assets.os.mkdir", side_effect=PermissionError("denied")), \
                    patch.object(store, "write_to_file") as write, \
                    patch.object(store, "_sync_asset_root") as sync:
                with self.assertRaisesRegex(OSError, "Unable to create project assets"):
                    store.save(destination)
                write.assert_not_called()
                sync.assert_not_called()
            self.assertEqual(store._data, original)
            self.assertEqual(store.current_filepath, "original.osp")
            self.assertTrue(store.has_unsaved_changes)
            # The user can retry in a writable location; no poisoned save state.
            with patch.object(store, "_sync_asset_root"), \
                    patch("classes.project_data.log.error") as error:
                store.move_temp_paths_to_project_folder(destination)
                error.assert_not_called()
            self.assertTrue(os.path.isdir(os.path.join(root, "new_assets", "title")))

    def test_late_clip_update_does_not_reinsert_deleted_clip(self):
        helper = Mock()
        helper.delete_invalid_timeline_item.return_value = False
        helper.show_wait_spinner = False
        partial = {"id": "deleted", "layer": 3000000, "position": 0.0,
                   "start": 0.0, "end": 115.593, "duration": 115.593}
        with patch.object(Clip, "get", return_value=None), \
                patch.object(Clip, "save") as save:
            self.timeline.TimelineView.update_clip_data(helper, json.dumps(partial))
            save.assert_not_called()
            helper.window.IgnoreUpdates.emit.assert_not_called()

    def test_new_clip_keeps_reader_and_existing_clip_uses_partial_update(self):
        helper = Mock()
        helper.delete_invalid_timeline_item.return_value = False
        helper.show_wait_spinner = False
        reader = openshot.DummyReader(openshot.Fraction(30, 1), 320, 180, 44100, 2, 10.0)
        native_clip = openshot.Clip(reader)
        data = json.loads(native_clip.Json())
        data["id"] = "clip"
        saved = []
        with patch.object(Clip, "get", return_value=None), \
                patch.object(Clip, "save", autospec=True,
                             side_effect=lambda clip: saved.append(copy.deepcopy(clip.data))):
            self.timeline.TimelineView.update_clip_data(helper, copy.deepcopy(data),
                                                        only_basic_props=True, ignore_reader=True)
        self.assertEqual(saved[0]["reader"], data["reader"])
        self.assertIn("alpha", saved[0])
        existing = Clip()
        existing.id, existing.type, existing.data = "clip", "update", data
        with patch.object(Clip, "get", return_value=existing), \
                patch.object(Clip, "save", autospec=True,
                             side_effect=lambda clip: saved.append(copy.deepcopy(clip.data))):
            self.timeline.TimelineView.update_clip_data(helper, copy.deepcopy(data))
        self.assertNotIn("reader", saved[1])
        self.assertEqual(saved[1]["id"], "clip")
        self.assertEqual(saved[1]["end"], data["end"])

    def test_svg_preview_recovers_after_unreadable_title(self):
        label = QLabel()
        label.resize(320, 180)
        helper = types.SimpleNamespace(lblPreviewLabel=label, thumbnailReady=Mock())
        app = types.SimpleNamespace(devicePixelRatio=lambda: 1)
        with tempfile.TemporaryDirectory() as root, \
                patch.object(self.title, "get_app", return_value=app):
            helper.filename = os.path.join(root, "title.svg")
            for contents in (None, "<svg", '<svg xmlns="http://www.w3.org/2000/svg" '
                             'width="320" height="180"><rect width="320" height="180" '
                             'fill="red"/></svg>'):
                if contents is not None:
                    with open(helper.filename, "w") as svg:
                        svg.write(contents)
                self.title.TitleEditor.display_svg(helper)
                pixmap = helper.thumbnailReady.emit.call_args[0][0]
                if contents is None or contents == "<svg":
                    self.assertTrue(pixmap.isNull())
                else:
                    self.assertFalse(pixmap.isNull())
                    self.assertGreater(pixmap.toImage().pixelColor(160, 90).red(), 200)

    def test_title_worker_is_reusable_after_failure(self):
        helper = types.SimpleNamespace(filename="title.svg", xmldoc=object(),
                                       is_thread_busy=False, writeToFile=Mock(),
                                       display_svg=Mock(side_effect=RuntimeError("render failed")))
        with self.assertRaisesRegex(RuntimeError, "render failed"):
            self.title.TitleEditor.save_and_reload_thread(helper)
        self.assertFalse(helper.is_thread_busy)
        helper.display_svg.side_effect = None
        self.title.TitleEditor.save_and_reload_thread(helper)
        self.assertFalse(helper.is_thread_busy)
        self.assertEqual(helper.display_svg.call_count, 2)

    def test_svg_thumbnail_failure_closes_reader_and_removes_temp_file(self):
        label = QLabel()
        label.resize(320, 180)
        helper = types.SimpleNamespace(filename="title.svg", lblPreviewLabel=label,
                                       thumbnailReady=Mock())
        reader = Mock()
        reader.info = types.SimpleNamespace(width=320, height=180)
        reader.GetFrame.return_value.Thumbnail.side_effect = RuntimeError("render failed")
        app = types.SimpleNamespace(devicePixelRatio=lambda: 1)
        with tempfile.TemporaryDirectory() as root:
            descriptor, path = tempfile.mkstemp(dir=root)
            with patch.object(self.title.openshot, "QtImageReader", return_value=reader), \
                    patch.object(self.title, "get_app", return_value=app), \
                    patch.object(self.title.tempfile, "mkstemp", return_value=(descriptor, path)):
                self.title.TitleEditor.display_svg(helper)
            reader.Close.assert_called_once_with()
            self.assertFalse(os.path.exists(path))
            self.assertTrue(helper.thumbnailReady.emit.call_args[0][0].isNull())
