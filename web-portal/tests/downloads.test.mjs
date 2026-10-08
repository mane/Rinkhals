import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const repo = fileURLToPath(new URL('../../', import.meta.url));
for (const [app, directory, version] of [
	['mainsail', '25-mainsail', '2.17.0'], ['fluidd', '26-fluidd', '1.35.0']
]) {
	for (const mode of ['download-fails', 'extract-fails', 'missing-index', 'publish-fails', 'success']) {
		test(`${app}: ${mode} preserves a usable previous or new download`, () => {
			const root = mkdtempSync(join(tmpdir(), 'rinkhals-download-'));
			try {
				const bin = join(root, 'bin');
				const files = join(root, 'files');
				const target = join(files, '4-apps/home/rinkhals/apps', directory);
				mkdirSync(bin);
				mkdirSync(join(target, app), { recursive: true });
				writeFileSync(join(target, app, 'index.html'), 'previous UI');
				writeFileSync(join(target, 'app.json'), '{"version": "old"}');
				writeFileSync(join(bin, 'wget'), '#!/bin/sh\n[ "$MODE" != download-fails ] || exit 1\n: > "$2"\n', { mode: 0o755 });
				writeFileSync(join(bin, 'unzip'), '#!/bin/sh\n[ "$MODE" != extract-fails ] || exit 1\nmkdir -p "$2"\n[ "$MODE" != missing-index ] || exit 0\nprintf "new UI" > "$2/index.html"\nprintf "hidden asset" > "$2/.asset"\n', { mode: 0o755 });
				// Fail after the new tree is in place to exercise rollback too.
				writeFileSync(join(bin, 'mv'), '#!/bin/sh\nif [ "$MODE" = publish-fails ] && [ "$(basename "$1")" = app.json ]; then exit 1; fi\nexec /bin/mv "$@"\n', { mode: 0o755 });
				const result = spawnSync('sh', [join(repo, 'build/4-apps', directory, `get-${app}.sh`)], {
					env: { ...process.env, FILES_DIR: files, MODE: mode, PATH: `${bin}:${process.env.PATH}` },
					encoding: 'utf8'
				});
				assert.equal(result.status === 0, mode === 'success', result.stderr);
				assert.equal(readFileSync(join(target, app, 'index.html'), 'utf8'), mode === 'success' ? 'new UI' : 'previous UI');
				assert.equal(JSON.parse(readFileSync(join(target, 'app.json'), 'utf8')).version, mode === 'success' ? version : 'old');
				assert.equal(readdirSync(target).some((name) => name.startsWith('.download.')), false);
				if (mode === 'success') assert.equal(readFileSync(join(target, app, '.asset'), 'utf8'), 'hidden asset');
			} finally {
				rmSync(root, { recursive: true, force: true });
			}
		});
	}
}
