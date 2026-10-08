import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

// Run the real component event handlers with controlled API responses. Svelte's
// DOM is irrelevant to these request flows; the selected app getter models the
// derived expression from the component, rather than duplicating its logic.
function handlers(component, names, state, derived = []) {
	const source = readFileSync(new URL(`../src/routes/${component}/+page.svelte`, import.meta.url), 'utf8');
	const script = source.slice(source.indexOf('>') + 1, source.indexOf('</script>'));
	const ast = ts.createSourceFile('component.ts', script, ts.ScriptTarget.Latest, true);
	const context = vm.createContext({ setTimeout: () => 0, btoa, console, ...state });
	const compile = (code) => ts.transpileModule(code, {
		compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext }
	}).outputText;
	for (const name of derived) {
		const declaration = ast.statements.filter(ts.isVariableStatement)
			.flatMap((s) => [...s.declarationList.declarations])
			.find((d) => d.name.getText(ast) === name);
		assert.equal(declaration.initializer.expression.getText(ast), '$derived');
		const expression = declaration.initializer.arguments[0].getText(ast);
		Object.defineProperty(context, name, {
			get: () => vm.runInContext(compile(`(${expression})`), context)
		});
	}
	for (const name of names) {
		const fn = ast.statements.find((s) => ts.isFunctionDeclaration(s) && s.name.text === name);
		assert.ok(fn, `Missing component handler: ${name}`);
		vm.runInContext(compile(fn.getText(ast)), context);
	}
	return context;
}

const app = { id: 'example', name: 'Example', properties: [{ key: 'LOGIN_URL', value: '' }] };
function appHandlers(overrides = {}) {
	return handlers('apps', ['fetchApps', 'openConfigure', 'hasPendingChanges', 'saveConfig', 'saveAndEnable'], {
		apps: [structuredClone(app)], configureAppId: 'example', pendingConfig: { TOKEN: 'new token' },
		configSaving: false, enablingApp: false, apiHost: '', loading: false, error: '',
		showToast() {}, ...overrides
	}, ['configureApp']);
}

for (const response of [
	{ ok: false, status: 500, json: async () => ({ output: 'Disk full' }) },
	{ ok: true, status: 200, json: async () => ({ success: false, output: 'Write failed' }) }
]) {
	test(`rejected configuration (${response.status}) never enables or starts the app`, async () => {
		const actions = [];
		const state = appHandlers({ fetch: async () => response, callApp: async (...args) => actions.push(args) });
		await state.saveAndEnable(app);
		assert.equal(actions.length, 0);
		assert.equal(state.pendingConfig.TOKEN, 'new token');
		assert.equal(state.enablingApp, false);
		assert.equal(state.configSaving, false);
	});
}

test('successful configuration is saved before enable and start', async () => {
	const calls = [];
	const state = appHandlers({
		fetch: async (url) => {
			calls.push(url);
			return { ok: true, json: async () => url.endsWith('/config') ? { success: true } : [app] };
		},
		callApp: async (_id, action) => calls.push(action)
	});
	await state.saveAndEnable(app);
	assert.deepEqual(calls, ['/api/apps/example/config', '/api/apps', '/enable', '/action']);
	assert.equal(Object.keys(state.pendingConfig).length, 0);
});

test('polling updates the open QR/report while preserving unsaved input', async () => {
	const fresh = { ...app, properties: [{ key: 'LOGIN_URL', value: 'https://example.test/login' }] };
	const state = appHandlers({ fetch: async () => ({ ok: true, json: async () => [fresh] }) });
	await state.fetchApps();
	assert.equal(state.configureApp.properties[0].value, 'https://example.test/login');
	assert.equal(state.pendingConfig.TOKEN, 'new token');
});

test('a completed save preserves edits made while the request was pending', async () => {
	let finishSave;
	const response = new Promise((resolve) => { finishSave = resolve; });
	const state = appHandlers({ fetch: async (url) => url.endsWith('/config') ? response : ({ ok: true, json: async () => [app] }) });
	const saving = state.saveConfig();
	state.pendingConfig = { TOKEN: 'even newer token' };
	finishSave({ ok: true, json: async () => ({ success: true }) });
	assert.equal(await saving, true);
	assert.equal(state.pendingConfig.TOKEN, 'even newer token');
});

test('changing username authenticates with the current username', async () => {
	const requests = [];
	const state = handlers('management', ['changePassword'], {
		p_currentUsername: 'old-user', p_username: 'new-user', p_current: 'old-password',
		p_new: 'new-password', p_confirm: 'new-password', p_error: '', p_success: '', authSaving: false,
		fetch: async (url, options) => { requests.push({ url, options }); return { ok: true }; }
	});
	await state.changePassword();
	assert.equal(requests.length, 2);
	for (const request of requests) {
		assert.equal(request.options.headers.Authorization, `Basic ${btoa('old-user:old-password')}`);
	}
	assert.equal(JSON.parse(requests[1].options.body).username, 'new-user');
});

test('the editor sends empty contents when clearing an existing file', async () => {
	let body;
	const state = handlers('editor', ['saveFile'], {
		isFileLoaded: true, filePath: '/tmp/example.cfg', content: '', saving: false, message: {},
		fetch: async (_url, options) => { body = JSON.parse(options.body); return { ok: true }; }
	});
	await state.saveFile();
	assert.deepEqual(body, { path: '/tmp/example.cfg', content: '' });
});
