"""Exercise the release Dockerfile's parallel SWU assembly failure handling."""
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ParallelSWUBuildTests(unittest.TestCase):
    def assemble(self, failing_model=''):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        stage = dockerfile.split('FROM prepare-bundle AS build-swu', 1)[1]
        commands = stage.split('RUN <<EOT\n', 1)[1].split('\nEOT', 1)[0]
        with tempfile.TemporaryDirectory(prefix='swu-pipeline-') as folder:
            root = Path(folder)
            helper = root / 'tools.sh'
            helper.write_text('''
prepare_tgz() { :; }
compress_swu() {
    if [ "$1" = "$FAILING_MODEL" ]; then
        return 17
    fi
    sleep 0.1
    touch "$2"
}
''')
            commands = commands.replace('/swu', str(root / 'swu'))
            commands = commands.replace('/tools.sh', str(helper))
            commands = 'FAILING_MODEL=' + shlex.quote(failing_model) + '\n' + commands
            result = subprocess.run(['sh', '-c', commands], capture_output=True,
                                    text=True, timeout=5)
            return result, sorted(p.name for p in (root / 'swu').glob('*.swu'))

    def test_earlier_compressor_failure_is_not_hidden_by_later_success(self):
        result, files = self.assemble('K3')
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files, ['update-k3m.swu', 'update-ks1.swu', 'update-ks1m.swu'])

    def test_all_model_compressors_are_awaited(self):
        result, files = self.assemble()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files, ['update-k2p-k3.swu', 'update-k3m.swu',
                                 'update-ks1.swu', 'update-ks1m.swu'])
