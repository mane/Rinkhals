"""Run the real shell helpers against temporary filesystem roots."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'files/3-rinkhals/tools.sh'
LOCK = ROOT / 'files/3-rinkhals/opt/rinkhals/tools/update-lock.sh'


class RuntimeToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rinkhals-tools-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.user = self.root / 'user'
        self.builtin = self.root / 'builtin'
        self.builtin.mkdir()
        self.lock = self.root / 'update.lock'
        self.prelude = (
            f'. {shlex.quote(str(TOOLS))} 2>/dev/null\n'
            f'. {shlex.quote(str(LOCK))}\n'
            f'RINKHALS_UPDATE_LOCK_DIR={shlex.quote(str(self.lock))}\n'
            f'RINKHALS_HOME={shlex.quote(str(self.user))}\n'
            f'BUILTIN_APP_PATH={shlex.quote(str(self.builtin))}\n'
            'USER_APP_PATH="$RINKHALS_HOME/apps"\n'
        )

    def run_shell(self, script, env=None):
        return subprocess.run(['bash', '-c', self.prelude + script],
                              text=True, capture_output=True, timeout=5, env=env)

    def test_builtin_disable_and_enable_do_not_create_shadow_app(self):
        app = self.builtin / '25-mainsail'
        app.mkdir()
        (app / '.enabled').touch()
        result = self.run_shell('''
is_app_enabled 25-mainsail
disable_app 25-mainsail || exit 1
is_app_enabled 25-mainsail
enable_app 25-mainsail || exit 1
is_app_enabled 25-mainsail
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['1', '0', '1'])
        self.assertFalse((self.user / 'apps/25-mainsail').exists())

    def test_user_app_flags_still_work(self):
        (self.user / 'apps/custom').mkdir(parents=True)
        result = self.run_shell('''
enable_app custom || exit 1
is_app_enabled custom
disable_app custom || exit 1
is_app_enabled custom
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['1', '0'])

    def test_unknown_printer_is_rejected(self):
        result = self.run_shell('log(){ :; }; quit(){ exit 23; }; KOBRA_MODEL_CODE=UNKNOWN; check_compatibility')
        self.assertEqual(result.returncode, 23)
        for model in ('K2P', 'K3', 'KS1', 'K3M', 'K3V2', 'KS1M'):
            with self.subTest(model=model):
                result = self.run_shell(f'KOBRA_MODEL_CODE={model}; check_compatibility')
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_preflight_and_boot_use_same_firmware_policy(self):
        result = self.run_shell('''
KOBRA_MODEL_CODE=K3
KOBRA_VERSION=2.4.6.7
is_supported_firmware
is_supported_firmware K3 2.4.0.4
is_supported_firmware KS1M 2.7.2.1
is_supported_firmware
''')
        self.assertEqual(result.stdout.splitlines(), ['1', '0', '1', '1'])

    def test_process_wait_expires(self):
        result = self.run_shell('''
get_by_name(){ :; }
msleep(){ :; }
log(){ :; }
quit(){ exit 24; }
wait_for_name nonexistent 500
''')
        self.assertEqual(result.returncode, 24)

    def test_free_space_uses_available_column(self):
        source = (ROOT / 'files/update.sh').read_text()
        check = source[source.index('FREE_SPACE='):source.index('# Backup the machine-specific')]
        for used, available, status in [(100000, 1900000, 0), (1950000, 50000, 25)]:
            with self.subTest(used=used, available=available):
                result = self.run_shell(
                    f"df(){{ echo '/dev/fake 2000000 {used} {available} 5% /useremain'; }}\n"
                    'log(){ :; }; quit(){ exit 25; }\n' + check)
                self.assertEqual(result.returncode, status, result.stderr)

    def test_lock_rejects_competing_process_and_allows_inherited_owner(self):
        owner_script = self.prelude + '''
acquire_update_lock || exit 1
trap release_update_lock EXIT
echo ready
read -r finish
'''
        env = os.environ.copy()
        env.pop('RINKHALS_UPDATE_LOCK_TOKEN', None)
        owner = subprocess.Popen(['bash', '-c', owner_script], env=env,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(owner.stdout.readline().strip(), 'ready')
            contender = self.run_shell('acquire_update_lock', env)
            self.assertNotEqual(contender.returncode, 0)
            token = (self.lock / 'owner').read_text().strip()
            inherited = dict(env, RINKHALS_UPDATE_LOCK_TOKEN=token)
            child = self.run_shell('acquire_update_lock && release_update_lock', inherited)
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertTrue(self.lock.exists(), 'child must not release its parent lock')
        finally:
            owner.communicate('done\n', timeout=5)
        self.assertEqual(owner.returncode, 0)
        self.assertFalse(self.lock.exists())

    def test_failed_extraction_releases_lock(self):
        # Rewrite only the device root in a copy; execute the original function.
        device_root = self.root / 'device'
        source = TOOLS.read_text().replace('/useremain', str(device_root))
        copied_tools = self.root / 'tools.sh'
        copied_tools.write_text(source)
        payload = self.root / 'input.swu'
        payload.touch()
        (device_root / 'update_swu').mkdir(parents=True)
        result = self.run_shell(
            f'. {shlex.quote(str(copied_tools))} 2>/dev/null\n'
            'KOBRA_MODEL_CODE=K3\n'
            'unzip(){ return 9; }\n'
            f'install_swu {shlex.quote(str(payload))}\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.lock.exists(), result.stderr)

    def test_rinkhals_installer_waits_for_child_failure(self):
        device = self.root / 'device'
        copied_tools = self.root / 'tools.sh'
        copied_tools.write_text(TOOLS.read_text().replace('/useremain', str(device)))
        payload = self.root / 'payload'
        payload.mkdir()
        (payload / 'rinkhals').mkdir()
        (payload / '.version').write_text('dev')
        finished = self.root / 'finished'
        (payload / 'update.sh').write_text(
            '#!/bin/sh\n'
            'if [ "$1" != "async" ]; then\n'
            '  nohup "$0" async >/dev/null &\n'
            '  exit 0\n'
            'fi\n'
            f'test -f {shlex.quote(str(self.lock / "owner"))} || exit 88\n'
            'sleep 0.1\n'
            f'touch {shlex.quote(str(finished))}\n'
            'exit 7\n')
        archive = self.root / 'setup.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(payload, arcname='.')
        swu = self.root / 'input.swu'
        with zipfile.ZipFile(swu, 'w') as zip_file:
            zip_file.write(archive, 'update_swu/setup.tar.gz')
        result = self.run_shell(
            f'. {shlex.quote(str(copied_tools))} 2>/dev/null\n'
            'KOBRA_MODEL_CODE=K3\n'
            f'install_swu {shlex.quote(str(swu))}\n')
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertTrue(finished.exists())
        self.assertFalse(self.lock.exists())

    @unittest.skipUnless(shutil.which('jq'), 'jq is required for configuration helpers')
    def test_property_values_are_quoted_and_failed_writes_preserve_config(self):
        value = 'A "quoted" value\\with\\slashes\nand a newline'
        result = self.run_shell(f'set_app_property custom token {shlex.quote(value)}')
        self.assertEqual(result.returncode, 0, result.stderr)
        config_path = self.user / 'apps/custom.config'
        import json
        self.assertEqual(json.loads(config_path.read_text())['token'], value)
        config_path.write_text('malformed JSON')
        result = self.run_shell('set_app_property custom token new-value')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(config_path.read_text(), 'malformed JSON')


if __name__ == '__main__':
    unittest.main()
