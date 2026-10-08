"""Exercise the real touch UI code without LVGL, network, or a printer."""

import contextlib
import importlib.util
import sys
import os
import tempfile
import unittest
import types
from pathlib import Path
from unittest import mock



UI = Path(__file__).resolve().parents[1] / 'files/3-rinkhals/opt/rinkhals/ui'


class Widget:
    def __init__(self):
        self.callbacks = []
        self.flags = set()
        self.states = {}
        self.text = ''

    def set_text(self, text):
        self.text = text

    def set_state(self, state, value):
        self.states[state] = value

    def add_flag(self, flag):
        self.flags.add(flag)

    def remove_flag(self, flag):
        self.flags.discard(flag)

    def has_flag(self, flag):
        return flag in self.flags

    def clear_event_cb(self):
        self.callbacks.clear()

    def add_event_cb(self, callback, _event, _data):
        self.callbacks.append(callback)

    def click(self):
        for callback in list(self.callbacks):
            callback(None)

    def clean(self):
        self.callbacks.clear()

    def __getattr__(self, name):
        if name.startswith('set_') or name in ('move_foreground', 'center'):
            return lambda *args: None
        raise AttributeError(name)


def load_common(stack, tmp_path):
    lv = types.ModuleType('lvgl')
    lv.helpers = types.SimpleNamespace(is_windows=lambda: False)
    lv.STATE = types.SimpleNamespace(DEFAULT=0, DISABLED=1)
    lv.OBJ_FLAG = types.SimpleNamespace(HIDDEN=1)
    lv.EVENT_CODE = types.SimpleNamespace(CLICKED=1)
    lv.lock = lv.unlock = lambda: None
    lv.pct = lambda value: value
    lvr = types.ModuleType('lvgl_rinkhals')
    lvr.lock = contextlib.nullcontext
    lvr.COLOR_PRIMARY = 'primary'
    lvr.COLOR_DANGER = 'danger'
    lvr.COLOR_TEXT = 'text'
    lvr.set_debug_rendering = lambda value: None
    stack.enter_context(mock.patch.dict(sys.modules, {'lvgl': lv, 'lvgl_rinkhals': lvr}))

    name = 'testsupport.touch_common'
    spec = importlib.util.spec_from_file_location(name, UI / 'common.py')
    module = importlib.util.module_from_spec(spec)
    # dataclasses looks up the class module while constructing its fields.
    stack.enter_context(mock.patch.dict(sys.modules, {name: module}))
    spec.loader.exec_module(module)
    module.RINKHALS_BASE = str(tmp_path / 'rinkhals')
    module.UpdateLock.path = str(tmp_path / 'update.lock')
    stack.enter_context(mock.patch.dict(os.environ))
    os.environ.pop('RINKHALS_UPDATE_LOCK_TOKEN', None)
    module.run_async = lambda callback: callback()
    return module


def make_installation(common, name):
    path = Path(common.RINKHALS_BASE) / name
    path.mkdir(parents=True)
    (path / '.version').write_text(name)
    return common.RinkhalsVersion(version=name, path=str(path))


def app_with_modal(common):
    app = common.BaseApp.__new__(common.BaseApp)
    app.root_modal = Widget()
    for modal_name in ('modal_ota_rinkhals', 'modal_ota_firmware'):
        modal = Widget()
        setattr(app, modal_name, modal)
        for name in ('label_title', 'label_description', 'label_warning', 'button_action',
                     'button_uninstall', 'button_usb', 'button_cancel', 'panel_progress',
                     'obj_progress_bar', 'label_progress_text'):
            setattr(modal, name, Widget())
    app.show_modal = mock.Mock()
    app.hide_modal = mock.Mock()
    app.show_screen = mock.Mock()
    app.show_ota_rinkhals = mock.Mock()
    app.show_text_dialog = mock.Mock()
    return app


class DownloadResponse:
    def __init__(self, chunks):
        self.chunks = chunks
        self.headers = {}  # Chunked responses need not contain a length.

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, **kwargs):
        return self.chunks()


class TouchUITests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp_path = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.common = load_common(self.stack, self.tmp_path)

    def test_installed_and_offline_versions_have_independent_defaults(self):
        common = self.common
        tmp_path = self.tmp_path
        installed = make_installation(common, '20260101_01')
        (Path(common.RINKHALS_BASE) / '.version').write_text(installed.version)
        current = common.Rinkhals.get_current_version()
        assert current.version == installed.version
        assert current.url == current.changes == current.sha256 == ''
        first, second = common.RinkhalsVersion(), common.RinkhalsVersion()
        first.supported_firmwares.append('2.4.0')
        assert second.supported_firmwares == []
        for version in common.Rinkhals.get_installed_versions():
            assert version.url == version.sha256 == version.changes == ''
            assert version.test is False
        assert common.FirmwareVersion().md5 == ''
        self.stack.enter_context(mock.patch.dict(sys.modules, {'requests': types.SimpleNamespace(get=mock.Mock(side_effect=OSError('offline')))}))
        common.PrinterInfo.get = lambda: types.SimpleNamespace(model_code='K3')
        assert common.Rinkhals.get_available_versions() == []


    def test_release_asset_is_an_exact_model_match(self):
        for model, group in [('K2P', 'k2p-k3'), ('K3', 'k2p-k3'), ('K3V2', 'k2p-k3'),
                             ('K3M', 'k3m'), ('KS1', 'ks1'), ('KS1M', 'ks1m')]:
            with self.subTest(model=model):
                common = self.common
                tmp_path = self.tmp_path
                assets = [{'name': f'update-{suffix}.swu', 'browser_download_url': suffix}
                          for suffix in ('k3m', 'ks1m', 'k2p-k3', 'ks1')]
                release = {'tag_name': '20261007_01', 'assets': assets}
                response = types.SimpleNamespace(status_code=200, json=lambda: [release])
                self.stack.enter_context(mock.patch.dict(sys.modules, {'requests': types.SimpleNamespace(get=lambda *args, **kwargs: response)}))
                common.PrinterInfo.get = lambda: types.SimpleNamespace(model_code=model)
                versions = common.Rinkhals.get_available_versions()
                assert len(versions) == 1
                assert versions[0].url == group
                assert versions[0].sha256 == ''

    def test_remove_callback_tracks_latest_modal_and_never_accumulates(self):
        common = self.common
        tmp_path = self.tmp_path
        current = make_installation(common, '20261007_01')
        first_old = make_installation(common, '20261001_01')
        second_old = make_installation(common, '20261002_01')
        (Path(common.RINKHALS_BASE) / '.version').write_text(current.version)
        app = app_with_modal(common)

        app.show_ota_rinkhals_modal(current)
        assert app.modal_ota_rinkhals.button_uninstall.callbacks == []
        app.show_ota_rinkhals_modal(first_old)
        app.show_ota_rinkhals_modal(second_old)
        assert len(app.modal_ota_rinkhals.button_uninstall.callbacks) == 1
        app.modal_ota_rinkhals.button_uninstall.click()
        assert Path(current.path).exists()
        assert Path(first_old.path).exists()
        assert not Path(second_old.path).exists()
        app.show_text_dialog.assert_not_called()


    def test_active_directory_and_active_symlink_cannot_be_removed(self):
        common = self.common
        tmp_path = self.tmp_path
        current = make_installation(common, '20261007_01')
        older = make_installation(common, '20261001_01')
        base = Path(common.RINKHALS_BASE)
        (base / '.version').write_text(current.version)
        (base / '.current').symlink_to(older.path)
        # Protect both pointers even if the saved version and symlink disagree.
        for path in (current.path, older.path, str(base / '.current')):
            with self.assertRaisesRegex(ValueError, 'active'):
                common.Rinkhals.remove_installed_version(common.RinkhalsVersion(path=path))
        assert Path(current.path).exists()
        assert Path(older.path).exists()


    def test_startup_hooks_patch_both_scripts_idempotently(self):
        common = self.common
        tmp_path = self.tmp_path
        gk = tmp_path / 'gk'
        gk.mkdir()
        patch = tmp_path / 'start.sh.patch'
        patch.write_text('# Rinkhals/begin\necho start\n# Rinkhals/end\n')
        for name in ('start.sh', 'restart_k3c.sh'):
            (gk / name).write_text('#!/bin/sh\necho stock\n')
        common.ensure_startup_hooks(str(gk), str(patch))
        common.ensure_startup_hooks(str(gk), str(patch))
        for name in ('start.sh', 'restart_k3c.sh'):
            contents = (gk / name).read_text()
            assert contents.count('Rinkhals/begin') == 1
            assert 'echo stock' in contents


    def test_failed_installer_never_patches_launchers_marks_or_reboots(self):
        common = self.common
        tmp_path = self.tmp_path
        app = common.BaseApp.__new__(common.BaseApp)
        opened = mock.mock_open(read_data='#!/bin/sh\nreboot\n')
        common.open = opened
        run = mock.Mock(return_value=9)
        common.system = run
        hooks = mock.Mock()
        common.ensure_startup_hooks = hooks
        assert app.install_swu(env={'RINKHALS_UPDATE_LOCK_TOKEN': 'test'}) is False
        run.assert_called_once_with('/useremain/update_swu/update.sh ', env={'RINKHALS_UPDATE_LOCK_TOKEN': 'test'})
        hooks.assert_not_called()
        assert [call.args[0] for call in opened.call_args_list] == ['/useremain/update_swu/update.sh']


    def test_update_lock_excludes_other_operation_and_releases_after_exception(self):
        common = self.common
        tmp_path = self.tmp_path
        path = Path(common.UpdateLock.path)
        with self.assertRaisesRegex(RuntimeError, 'test failure'):
            with common.UpdateLock() as lock:
                assert (path / 'owner').read_text().strip() == lock.token
                with self.assertRaisesRegex(RuntimeError, 'Another update'):
                    with common.UpdateLock():
                        self.fail('second operation entered the lock')
                raise RuntimeError('test failure')
        assert not path.exists()
        with common.UpdateLock():
            assert path.is_dir()
        assert not path.exists()


    def test_inherited_lock_is_shared_without_removing_owners_lock(self):
        common = self.common
        tmp_path = self.tmp_path
        path = Path(common.UpdateLock.path)
        with common.UpdateLock() as owner:
            os.environ['RINKHALS_UPDATE_LOCK_TOKEN'] = owner.token
            with common.UpdateLock() as inherited:
                assert inherited.token == owner.token
                assert inherited.owned is False
            assert path.exists()
        assert not path.exists()


    def test_install_transaction_holds_lock_across_extract_and_install(self):
        for extract_ok in (False, True):
            with self.subTest(extract_ok=extract_ok):
                common = self.common
                tmp_path = self.tmp_path
                app = common.BaseApp.__new__(common.BaseApp)
                path = Path(common.UpdateLock.path)
                events = []

                def extract(source):
                    assert path.is_dir()
                    assert source == '/tmp/download.swu'
                    events.append('extract')
                    return extract_ok

                def install(params, env):
                    assert path.is_dir()
                    assert env['RINKHALS_UPDATE_LOCK_TOKEN'] == (path / 'owner').read_text().strip()
                    assert params == 'async'
                    events.append('install')
                    return True

                app.extract_swu = extract
                app.install_swu = install
                assert app.install_download('/tmp/download.swu', 'async') is extract_ok
                assert events == (['extract', 'install'] if extract_ok else ['extract'])
                assert not path.exists()

    def test_each_diagnostic_row_opens_its_own_diagnostic(self):
        common = self.common
        tmp_path = self.tmp_path
        self.stack.enter_context(mock.patch.dict(sys.modules, {'common': common}))
        name = 'testsupport.touch_installer'
        spec = importlib.util.spec_from_file_location(name, UI / 'rinkhals-install.py')
        installer = importlib.util.module_from_spec(spec)
        self.stack.enter_context(mock.patch.dict(sys.modules, {name: installer}))
        spec.loader.exec_module(installer)

        lv, lvr = common.lv, common.lvr
        lv.BORDER_SIDE = types.SimpleNamespace(BOTTOM=1)
        lv.ALIGN = types.SimpleNamespace(LEFT_MID=1, RIGHT_MID=2)
        lv.LABEL_LONG_MODE = types.SimpleNamespace(WRAP=1)
        lv.OBJ_FLAG.CLICKABLE = 2
        lv.dpx = lambda value: value
        lv.color_make = lambda *rgb: rgb
        panels = []

        def new_panel(*args, **kwargs):
            panel = Widget()
            panels.append(panel)
            return panel

        lvr.panel = new_panel
        lvr.label = lvr.tag = lambda *args, **kwargs: Widget()
        diagnostics = [common.Diagnostic(common.DiagnosticType.WARNING, str(index), '',
                                         fix_action=common.DiagnosticFixes.REINSTALL_FIRMWARE)
                       for index in range(2)]
        common.Diagnostic.collect = lambda: diagnostics
        app = installer.RinkhalsInstallApp.__new__(installer.RinkhalsInstallApp)
        app.screen_diagnostics = types.SimpleNamespace(panel_diagnostics=Widget())
        app.show_diagnostic_modal = mock.Mock()
        app.show_diagnostics()
        assert len(panels) == 2
        for panel in panels:
            panel.click()
        assert app.show_diagnostic_modal.call_args_list == [mock.call(d) for d in diagnostics]

    def test_cancelled_callback_cannot_disable_the_replacement_modal(self):
        common = self.common
        common.Firmware.get_current_version = lambda: '1.0'
        for kind, version_type in [('rinkhals', common.RinkhalsVersion), ('firmware', common.FirmwareVersion)]:
            with self.subTest(kind=kind):
                app = app_with_modal(common)
                show = getattr(app, f'show_ota_{kind}_modal')
                modal = getattr(app, f'modal_ota_{kind}')
                show(version_type(version='old', url='https://example.test/old.swu'))
                old_callback = modal.button_action.callbacks[0]
                show(version_type(version='new', url='https://example.test/new.swu'))
                old_callback(None)
                assert modal.button_action.states[common.lv.STATE.DISABLED] is False
                assert modal.button_action.text == 'Download'
                assert modal.label_progress_text.text != 'Starting...'

    def test_failed_download_removes_partial_file(self):
        common = self.common
        common.Firmware.get_current_version = lambda: '1.0'
        common.PrinterInfo.get = lambda: types.SimpleNamespace(model_code='K3')

        def chunks():
            yield b'partial SWU'
            raise OSError('connection lost')

        requests = types.SimpleNamespace(get=lambda *args, **kwargs: DownloadResponse(chunks))
        with mock.patch.dict(sys.modules, {'requests': requests}):
            for kind, version_type in [('rinkhals', common.RinkhalsVersion), ('firmware', common.FirmwareVersion)]:
                with self.subTest(kind=kind):
                    app = app_with_modal(common)
                    getattr(app, f'show_ota_{kind}_modal')(version_type(version='new', url='https://example.test/update.swu'))
                    modal = getattr(app, f'modal_ota_{kind}')
                    modal.button_action.click()
                    assert not Path(modal.pending_download.path).exists()
                    assert modal.label_progress_text.text == 'Failed'
                    assert modal.button_action.states[common.lv.STATE.DISABLED] is False

    def test_replacing_modal_cancels_stream_without_changing_new_actions(self):
        common = self.common
        app = app_with_modal(common)
        app.show_ota_rinkhals_modal(common.RinkhalsVersion(version='old', url='https://example.test/old.swu'))
        modal = app.modal_ota_rinkhals
        previous = modal.pending_download

        def chunks():
            yield b'partial SWU'
            app.show_ota_rinkhals_modal(common.RinkhalsVersion(version='new', url='https://example.test/new.swu'))
            yield b'more old SWU'

        requests = types.SimpleNamespace(get=lambda *args, **kwargs: DownloadResponse(chunks))
        with mock.patch.dict(sys.modules, {'requests': requests}):
            modal.button_action.click()
        assert previous.cancelled is True
        assert not Path(previous.path).exists()
        assert modal.pending_download is not previous
        assert modal.label_title.text == 'Rinkhals new'
        assert modal.button_action.text == 'Download'
        assert modal.button_action.states[common.lv.STATE.DISABLED] is False

    def test_hiding_ready_download_removes_staged_swu(self):
        common = self.common
        app = app_with_modal(common)
        app.show_ota_rinkhals_modal(common.RinkhalsVersion(version='new', url='https://example.test/update.swu'))
        modal = app.modal_ota_rinkhals
        requests = types.SimpleNamespace(get=lambda *args, **kwargs: DownloadResponse(lambda: iter([b'complete SWU'])))
        with mock.patch.dict(sys.modules, {'requests': requests}):
            modal.button_action.click()
        path = Path(modal.pending_download.path)
        assert path.read_bytes() == b'complete SWU'
        assert modal.label_progress_text.text == 'Ready to install'
        app.modal_current = modal
        common.BaseApp.hide_modal(app)
        assert not path.exists()

    def test_late_install_callback_ignores_closed_or_replaced_modal(self):
        common = self.common
        common.Firmware.get_current_version = lambda: '1.0'
        common.PrinterInfo.get = lambda: types.SimpleNamespace(model_code='K3')
        requests = types.SimpleNamespace(get=lambda *args, **kwargs: DownloadResponse(lambda: iter([b'complete SWU'])))
        for kind, version_type in [('rinkhals', common.RinkhalsVersion), ('firmware', common.FirmwareVersion)]:
            for action in ('close', 'replace'):
                with self.subTest(kind=kind, action=action), mock.patch.dict(sys.modules, {'requests': requests}):
                    app = app_with_modal(common)
                    app.install_download = mock.Mock(return_value=True)
                    show = getattr(app, f'show_ota_{kind}_modal')
                    modal = getattr(app, f'modal_ota_{kind}')
                    show(version_type(version='old', url='https://example.test/old.swu'))
                    modal.button_action.click()
                    assert modal.label_progress_text.text == 'Ready to install'
                    staged_path = Path(modal.pending_download.path)
                    old_callback = modal.button_action.callbacks[0]
                    if action == 'close':
                        app.modal_current = modal
                        common.BaseApp.hide_modal(app)
                    else:
                        show(version_type(version='new', url='https://example.test/new.swu'))
                    assert not staged_path.exists()
                    before = self.modal_state(app, modal)
                    old_callback(None)
                    app.install_download.assert_not_called()
                    assert self.modal_state(app, modal) == before

    def test_late_uninstall_callback_ignores_closed_or_replaced_modal(self):
        common = self.common
        current = make_installation(common, '20261007_01')
        (Path(common.RINKHALS_BASE) / '.version').write_text(current.version)
        for action in ('close', 'replace'):
            with self.subTest(action=action):
                older = make_installation(common, f'20260101_{action}')
                app = app_with_modal(common)
                app.show_ota_rinkhals_modal(older)
                modal = app.modal_ota_rinkhals
                old_callback = modal.button_uninstall.callbacks[0]
                if action == 'close':
                    app.modal_current = modal
                    common.BaseApp.hide_modal(app)
                else:
                    app.show_ota_rinkhals_modal(common.RinkhalsVersion(version='new', url='https://example.test/new.swu'))
                before = self.modal_state(app, modal)
                old_callback(None)
                assert Path(older.path).is_dir()
                assert Path(current.path).is_dir()
                assert not Path(common.UpdateLock.path).exists()
                app.show_screen.assert_not_called()
                assert self.modal_state(app, modal) == before

    @staticmethod
    def modal_state(app, modal):
        widgets = [app.root_modal, modal]
        widgets += [value for value in vars(modal).values() if isinstance(value, Widget)]
        return [(dict(widget.states), widget.text, set(widget.flags), list(widget.callbacks))
                for widget in widgets]


if __name__ == "__main__":
    unittest.main()
