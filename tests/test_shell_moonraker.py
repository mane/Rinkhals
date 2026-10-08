import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

APP_SOURCE = Path(__file__).resolve().parents[1] / 'files/4-apps/home/rinkhals/apps/40-moonraker'


class MoonrakerSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='moonraker-lifecycle-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / 'app'
        self.app.mkdir()
        self.events = self.root / 'events'
        self.mode = self.root / 'mode'
        self.mode.write_text('run')
        self.pidfile = self.root / 'moonraker.pid'
        self.registry = self.root / 'processes'
        self.runtime = self.root / 'runtime'
        (self.runtime / 'logs').mkdir(parents=True)
        tools = self.root / 'tools.sh'
        tools.write_text(
            'RINKHALS_ROOT=' + shlex.quote(str(self.runtime)) + '\n'
            'APP_STATUS_STOPPED=stopped\nAPP_STATUS_STARTED=started\n'
            'report_status() { printf "%s\\n" "$*"; }\n'
        )
        useremain = self.root / 'useremain'
        userdata = self.root / 'userdata'
        (userdata / 'app/gk/printer_data/config').mkdir(parents=True)
        useremain.mkdir()
        for name in ('app.sh', 'moonraker.sh', 'moonraker-lifecycle.sh'):
            text = (APP_SOURCE / name).read_text()
            text = text.replace('/useremain/rinkhals/.current/tools.sh', str(tools))
            text = text.replace('/tmp/rinkhals/moonraker-supervisor.pid', str(self.pidfile))
            text = text.replace('/useremain', str(useremain)).replace('/userdata', str(userdata))
            text = text.replace('sleep 10 &', 'sleep 0.6 &')
            if name == 'moonraker.sh' and not Path('/proc/self/stat').exists():
                register = 'printf \'%s supervisor\\n\' "$$" >> '
                register += shlex.quote(str(self.registry)) + '\n'
                text = text.replace('SUPERVISOR_IDENTITY=',
                                    register + 'SUPERVISOR_IDENTITY=', 1)
            (self.app / name).write_text(text)

        if not Path('/proc/self/stat').exists():
            # macOS has no procfs and the sandbox blocks ps. Track only
            # fixture-owned processes; the actual procfs parser is tested below.
            with (self.app / 'moonraker-lifecycle.sh').open('a') as f:
                f.write('\nMOONRAKER_TEST_REGISTRY=' + shlex.quote(str(self.registry)) + '\n')
                f.write('''
moonraker_process_identity() {
    kill -0 "$1" 2>/dev/null || return 1
    printf '%s\\n' "$1"
}
moonraker_find_processes() {
    local kind="$1" pid recorded_kind identity
    [ -f "$MOONRAKER_TEST_REGISTRY" ] || return 0
    while read -r pid recorded_kind; do
        [ "$recorded_kind" = "$kind" ] || continue
        identity=$(moonraker_process_identity "$pid") || continue
        printf '%s %s\\n' "$pid" "$identity"
    done < "$MOONRAKER_TEST_REGISTRY"
}
''')

        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        (self.app / 'bin').mkdir()
        (self.app / 'bin/activate').write_text(':\n')
        python = bin_dir / 'python'
        python.write_text(
            '#!/bin/sh\n'
            'case "$1" in -m|/opt/rinkhals/scripts/process-cfg.py) exit 0 ;; esac\n'
            'exec ' + shlex.quote(sys.executable) + ' "$@"\n'
        )
        python.chmod(0o755)
        sysctl = bin_dir / 'sysctl'
        sysctl.write_text('#!/bin/sh\nexit 0\n')
        sysctl.chmod(0o755)
        self.env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])
        for name in ('kobra.py', 'memory_manager.py', 'mmu_ace.py', 'mmu_ace_metadata.py',
                     'moonraker.custom.conf', 'moonraker.conf'):
            (self.app / name).write_text('')
        (self.app / 'moonraker/moonraker/components').mkdir(parents=True)
        worker = self.app / 'moonraker/moonraker/moonraker.py'
        worker.write_text(
            'import os, signal, time\n'
            'from pathlib import Path\n'
            f'events = Path({str(self.events)!r})\n'
            'def event(name):\n'
            '    with events.open("a") as f: f.write(f"{name} {os.getpid()}\\n")\n'
            'def stop(*args):\n'
            '    event("term")\n'
            '    raise SystemExit(0)\n'
            'signal.signal(signal.SIGTERM, stop)\n'
            f'with Path({str(self.registry)!r}).open("a") as f: f.write(f"{{os.getpid()}} worker\\n")\n'
            'event("start")\n'
            f'if Path({str(self.mode)!r}).read_text() == "crash": raise SystemExit(1)\n'
            'while True: time.sleep(0.05)\n'
        )
        self.addCleanup(self.stop_fixture)

    def call(self, action):
        return subprocess.run(['sh', str(self.app / 'app.sh'), action], env=self.env,
                              capture_output=True, text=True, timeout=20, check=True)

    def stop_fixture(self):
        self.call('stop')

    def wait_until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.03)
        self.fail('Timed out waiting for fixture state')

    def events_named(self, name):
        if not self.events.exists():
            return []
        return [int(line.split()[1]) for line in self.events.read_text().splitlines()
                if line.startswith(name + ' ')]

    def test_stop_terminates_supervisor_and_child_without_respawn(self):
        self.call('start')
        self.wait_until(lambda: len(self.events_named('start')) == 1)
        child = self.events_named('start')[0]
        self.call('stop')
        self.assertIn(child, self.events_named('term'))
        self.assertFalse(self.pidfile.exists())
        time.sleep(0.8)
        self.assertEqual(len(self.events_named('start')), 1)
        self.assertIn('stopped', self.call('status').stdout)

    def test_repeated_start_waits_for_previous_child_shutdown(self):
        self.call('start')
        self.wait_until(lambda: len(self.events_named('start')) == 1)
        self.call('start')
        self.wait_until(lambda: len(self.events_named('start')) == 2)
        events = self.events.read_text().splitlines()
        self.assertTrue(events[1].startswith('term '), events)
        self.assertTrue(events[2].startswith('start '), events)
        self.assertIn('started', self.call('status').stdout)

    def test_stop_during_restart_delay_and_legacy_missing_pidfile(self):
        self.mode.write_text('crash')
        self.call('start')
        log = self.runtime / 'logs/app-moonraker.log'
        self.wait_until(lambda: log.exists() and 'Waiting 10 seconds' in log.read_text())
        self.pidfile.unlink(missing_ok=True)
        self.call('stop')
        starts = len(self.events_named('start'))
        time.sleep(0.8)
        self.assertEqual(len(self.events_named('start')), starts)
        self.assertFalse(self.pidfile.exists())

    def test_stale_pidfile_does_not_terminate_unrelated_process(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        def stop_unrelated():
            if unrelated.poll() is None:
                unrelated.terminate()
                unrelated.wait(timeout=3)
        self.addCleanup(stop_unrelated)
        self.pidfile.write_text(f'{unrelated.pid} stale-start-time\n')
        self.call('stop')
        self.assertIsNone(unrelated.poll())
        unrelated.terminate()
        unrelated.wait(timeout=3)


class MoonrakerProcIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='moonraker-proc-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc = self.root / 'proc'
        self.process = self.proc / '42'
        self.process.mkdir(parents=True)
        self.app = self.root / 'app'
        self.app.mkdir()
        (self.process / 'cwd').symlink_to(self.app, target_is_directory=True)
        self.stat('S', '1234')
        self.cmdline('sh', str(self.app / 'moonraker.sh'))
        self.helper = self.root / 'lifecycle.sh'
        self.helper.write_text((APP_SOURCE / 'moonraker-lifecycle.sh').read_text().replace(
            '/proc/', str(self.proc) + '/'
        ))

    def stat(self, state, starttime):
        (self.process / 'stat').write_text(
            f'42 (worker (test)) {state} ' + '0 ' * 18 + starttime + '\n'
        )

    def cmdline(self, *args):
        (self.process / 'cmdline').write_bytes(('\0'.join(args) + '\0').encode())

    def run_helper(self, command):
        script = 'APP_ROOT=' + shlex.quote(str(self.app)) + '\n'
        script += '. ' + shlex.quote(str(self.helper)) + '\n' + command
        return subprocess.run(['sh', '-c', script], capture_output=True, text=True, timeout=3)

    def test_starttime_accounts_for_spaces_and_parentheses_in_comm(self):
        result = self.run_helper('moonraker_process_identity 42')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '1234')
        self.stat('Z', '1234')
        self.assertNotEqual(self.run_helper('moonraker_process_identity 42').returncode, 0)

    def test_only_exact_interpreter_and_script_are_matched(self):
        self.assertEqual(self.run_helper('moonraker_process_matches 42 supervisor').returncode, 0)
        self.cmdline('sh', '-c', 'echo ' + str(self.app / 'moonraker.sh'))
        self.assertNotEqual(self.run_helper('moonraker_process_matches 42 supervisor').returncode, 0)
        self.cmdline('sh', str(self.app / 'moonraker.sh.backup'))
        self.assertNotEqual(self.run_helper('moonraker_process_matches 42 supervisor').returncode, 0)
        self.cmdline('sh', './moonraker.sh')
        self.assertEqual(self.run_helper('moonraker_process_matches 42 supervisor').returncode, 0)

    def test_recycled_pid_is_never_signalled(self):
        result = self.run_helper('kill() { echo unexpected-kill; }\nmoonraker_terminate 42 9999 0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
