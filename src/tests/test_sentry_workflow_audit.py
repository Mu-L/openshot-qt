"""Cross-layer coverage for the committed September Sentry fixes."""

import copy
import json
import os
import tempfile
import types
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import openshot
from classes import info
from classes.json_data import JsonDataStore
from classes.query import Clip, Effect, QueryObject
from classes.timeline import TimelineSync
from classes.updates import UpdateManager
from tests.test_project_data import make_store
from tests import test_sentry_sep12


class SentryWorkflowAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_sentry_sep12.SentrySeptemberTests.setUpClass.__func__(cls)

    def test_clip_effect_edit_and_deletion_round_trip(self):
        store = make_store()
        store._data = {'clips': [], 'effects': [], 'layers': [], 'fps': {'num': 30, 'den': 1}}
        manager = UpdateManager()
        native = openshot.Timeline(320, 180, openshot.Fraction(30, 1), 44100, 2, openshot.LAYOUT_STEREO)
        self.addCleanup(native.Clear)
        window = Mock(proxy_service=None)
        sync = types.SimpleNamespace(timeline=native, window=window)
        window.timeline_sync = sync
        preview = types.SimpleNamespace(win=window, transforming_clips=[], transforming_clip_objects=[],
                                        transforming_clip=None, transforming_clip_object=None,
                                        transforming_effect=None, transforming_effect_object=None)
        preview.refreshTriggered = lambda **kw: self.video.VideoWidget.refreshTriggered(preview, **kw)
        window.videoPreview = preview
        manager.add_listener(types.SimpleNamespace(changed=lambda action: TimelineSync.changed(sync, action)))
        manager.add_listener(store)
        # Query consumers run after the store, as geometry/properties listeners do.
        observations = []
        manager.add_listener(types.SimpleNamespace(changed=lambda action: observations.append(
            (copy.deepcopy([c.data for c in Clip.filter()]), copy.deepcopy(store._data['clips'])))))
        helper = Mock(window=window, show_wait_spinner=False)
        helper.update_clip_data.side_effect = lambda data, **kw: self.timeline.TimelineView.update_clip_data(helper, data, **kw)
        helper.delete_invalid_timeline_item.side_effect = lambda item: self.timeline.TimelineView.delete_invalid_timeline_item(helper, item)
        reader = openshot.DummyReader(openshot.Fraction(30, 1), 320, 180, 44100, 2, 60.0)
        source = openshot.Clip(reader)
        source.Start(5)
        source.End(35)
        effect = openshot.Crop()
        effect.Id('crop')
        source.AddEffect(effect)
        with patch.object(self.app, 'project', store), patch.object(self.app, 'updates', manager), \
                patch.object(self.app, 'window', window):
            QueryObject._cache_version = -1
            # Import through the changed insertion path, including a complete reader.
            helper.update_clip_data(json.loads(source.Json()), ignore_reader=True)
            cid = store._data['clips'][0]['id']
            preview.transforming_clips = [Clip.get(id=cid)]
            preview.refreshTriggered()
            preview.transforming_effect = Effect.get(id='crop')
            preview.transforming_effect_object = native.GetClipEffect('crop')
            baseline = copy.deepcopy(store._data['clips'])
            edited = copy.deepcopy(Clip.get(id=cid).data)
            edited.update(position=12.0, start=7.0, end=25.0, duration=18.0, layer=2)
            edited['scale_x']['Points'][0]['co']['Y'] = 0.75
            edited['effects'][0]['left']['Points'][0]['co']['Y'] = 0.2
            helper.update_clip_data(edited, only_basic_props=False, ignore_reader=True)
            after_edit = copy.deepcopy(store._data['clips'])
            self.assertEqual(native.GetClip(cid).Position(), 12)
            self.assertEqual(native.GetClip(cid).Start(), 7)
            self.assertEqual(native.GetClip(cid).End(), 25)
            self.assertAlmostEqual(json.loads(native.GetClipEffect('crop').PropertiesJSON(1))['left']['value'], 0.2)
            self.assertEqual(int(preview.transforming_effect_object.this), int(native.GetClipEffect('crop').this))
            Clip.get(id=cid).delete()
            self.assertIsNone(preview.transforming_clip_object)
            self.assertIsNone(preview.transforming_effect_object)
            self.assertEqual(store._data['clips'], [])
            # A queued partial edit must not resurrect the deleted clip.
            helper.update_clip_data(dict(id=cid, position=12, start=7, end=25, duration=18, layer=2))
            self.assertEqual(store._data['clips'], [])
            self.assertEqual(len(manager.actionHistory), 3)
            for expected in (after_edit, baseline):
                manager.undo()
                self.assertEqual(store._data['clips'], expected)
                self.assertEqual(bool(native.GetClip(cid)), bool(expected))
            for expected in (after_edit, []):
                manager.redo()
                self.assertEqual(store._data['clips'], expected)
                self.assertEqual(bool(native.GetClip(cid)), bool(expected))
            self.assertTrue(observations)
            for query_data, stored_data in observations:
                self.assertEqual(query_data, stored_data)

    def test_save_and_save_as_reopen_title_recording_and_generated_clip_assets(self):
        store = make_store()
        JsonDataStore.__init__(store)
        with tempfile.TemporaryDirectory() as root, ExitStack() as stack:
            user_path = os.path.join(root, 'runtime')
            os.makedirs(os.path.join(user_path, 'recordings'))
            stack.enter_context(patch.object(info, 'USER_PATH', user_path))
            for name in ('THUMBNAIL_PATH', 'TITLE_PATH', 'BLENDER_PATH', 'PROTOBUF_DATA_PATH',
                         'CLIPBOARD_PATH', 'COMFYUI_OUTPUT_PATH', 'PROXY_PATH'):
                path = os.path.join(user_path, name.lower())
                os.makedirs(path)
                stack.enter_context(patch.object(info, name, path))
            title_path = os.path.join(info.TITLE_PATH, 'title.svg')
            svg = '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="18"><rect width="32" height="18" fill="red"/></svg>'
            with open(title_path, 'w') as stream:
                stream.write(svg)
            recording_path = os.path.join(user_path, 'recordings', 'capture.mov')
            recording_bytes = b'recording payload for relocation verification'
            with open(recording_path, 'wb') as stream:
                stream.write(recording_bytes)
            tracking_path = os.path.join(info.PROTOBUF_DATA_PATH, 'tracking.data')
            with open(tracking_path, 'wb') as stream:
                stream.write(b'tracking payload')
            title = json.loads(openshot.Clip(title_path).Json())
            title.update(id='title', file_id='F1', position=10, start=0, end=10, duration=10, layer=2)
            store._data = {'files': [{'id': 'F1', 'path': title_path}, {'id': 'F2', 'path': recording_path}],
                           'clips': [title, {'id': 'recording', 'file_id': 'F2', 'reader': {'path': recording_path}, 'effects': []},
                                     {'id': 'generated', 'reader': {}, 'effects': [{'protobuf_data_path': tracking_path}]}]}
            stack.enter_context(patch.object(store, 'add_to_recent_files'))
            stack.enter_context(patch.object(self.app, 'project', store))
            reopened = []
            for name in ('first', 'second'):
                destination = os.path.join(root, name + '.osp')
                store.save(destination)
                data = store.read_from_file(destination, path_mode='absolute')
                reopened.append(data)
                for record in data['files']:
                    self.assertTrue(os.path.isfile(record['path']))
                    self.assertTrue(record['path'].startswith(os.path.join(root, name + '_assets')))
                self.assertEqual(data['clips'][0]['reader']['path'], data['files'][0]['path'])
                self.assertEqual(data['clips'][1]['reader']['path'], data['files'][1]['path'])
                self.assertEqual(data['clips'][0]['end'], 10)
                with open(data['clips'][1]['reader']['path'], 'rb') as stream:
                    self.assertEqual(stream.read(), recording_bytes)
                with open(data['clips'][2]['effects'][0]['protobuf_data_path'], 'rb') as stream:
                    self.assertEqual(stream.read(), b'tracking payload')
                restored_title = openshot.Clip()
                restored_title.SetJson(json.dumps(data['clips'][0]))
                restored_title.Open()
                try:
                    self.assertEqual(restored_title.GetFrame(1).GetWidth(), 32)
                finally:
                    restored_title.Close()
            # Save As must leave the first project's media intact and independent.
            self.assertNotEqual(reopened[0]['files'][1]['path'], reopened[1]['files'][1]['path'])
            for data in reopened:
                with open(data['files'][1]['path'], 'rb') as stream:
                    self.assertEqual(stream.read(), recording_bytes)
