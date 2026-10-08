import { test as base, expect, type Route } from '@playwright/test';

type ApiRequest = { method: string; path: string; body: any; authorization?: string };

function newApiState() {
	return {
		requests: [] as ApiRequest[],
		unexpected: [] as string[],
		configFailure: null as 'http' | 'logical' | null,
		currentUsername: 'old-user',
		currentPassword: 'old-password',
		fileContent: 'original configuration\n',
		app: {
			id: 'mock-app', name: 'Mock app', description: 'Browser regression fixture',
			version: '1.0', source: 'user', enabled: false, running: false,
			properties: [
				{ key: 'TOKEN', display: 'Access token', type: 'string', default: '', value: '', overridden: false },
				{ key: 'LOGIN_URL', display: 'Login link', type: 'qr', default: '', value: '', overridden: false },
				{ key: 'STATUS', display: 'Connection status', type: 'report', default: '', value: '{"status":"Waiting"}', overridden: false }
			]
		}
	};
}

export type ApiState = ReturnType<typeof newApiState>;

export const test = base.extend<{ api: ApiState }>({
	api: async ({ page, baseURL }, use) => {
		const api = newApiState();
		const pageErrors: string[] = [];
		page.on('pageerror', (error) => pageErrors.push(error.message));
		const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body });

		// Every shell connection stays in-process. No backend or printer is used.
		await page.routeWebSocket('**/*', (socket) => {
			const url = new URL(socket.url());
			if (`http://${url.host}` !== baseURL || url.pathname !== '/api/terminal') {
				api.unexpected.push(`WebSocket ${socket.url()}`);
				return socket.close({ code: 1008, reason: 'No browser fixture for this socket' });
			}
			socket.onMessage(() => {});
		});
		await page.route('**/*', async (route) => {
			const request = route.request();
			const url = new URL(request.url());
			if (url.origin !== baseURL) {
				api.unexpected.push(request.url());
				return route.abort();
			}
			if (!url.pathname.startsWith('/api/')) return route.continue();
			const entry = {
				method: request.method(), path: url.pathname,
				body: request.postData() ? request.postDataJSON() : null,
				authorization: request.headers()['authorization']
			};
			api.requests.push(entry);
			const authenticated = entry.authorization === `Basic ${Buffer.from(`${api.currentUsername}:${api.currentPassword}`).toString('base64')}`;

			switch (url.pathname) {
				case '/api/auth/status':
					if (entry.authorization && !authenticated) return json(route, { error: 'Wrong current credentials' }, 401);
					return json(route, { is_default: false });
				case '/api/auth/change':
					return json(route, { success: authenticated }, authenticated ? 200 : 401);
				case '/api/apps':
					return json(route, [api.app]);
				case '/api/apps/mock-app/config':
					if (api.configFailure) return json(route, { success: false, output: 'Mock write failed' }, api.configFailure === 'http' ? 500 : 200);
					for (const property of api.app.properties) {
						if (entry.body[property.key] !== undefined) {
							property.value = entry.body[property.key];
							property.overridden = true;
						}
					}
					return json(route, { success: true });
				case '/api/apps/mock-app/enable':
					api.app.enabled = true;
					return json(route, { success: true });
				case '/api/apps/mock-app/action':
					api.app.running = entry.body.action === 'start';
					return json(route, { success: true });
				case '/api/catalog':
					return json(route, { release: 'mock', fetched_at: new Date().toISOString(), model: 'K3', asset_group: 'k2p-k3', apps: [] });
				case '/api/printer/state':
					return json(route, { state: 'standby', can_install: true });
				case '/api/firmware/status':
					return json(route, { model_code: 'K3', model_name: 'Kobra 3', current_firmware: '2.4.0', current_rinkhals: '20261007_01', firmware_update_available: false, rinkhals_update_available: false, fetched_at: new Date().toISOString() });
				case '/api/firmware/patches':
					return json(route, { binary_patches: [], script_hooks: [], compatible_for_run: true });
				case '/api/download':
					return route.fulfill({ status: 200, contentType: 'text/plain', body: api.fileContent });
				case '/api/saveFile':
					api.fileContent = entry.body.content;
					return json(route, { success: true });
				default:
					api.unexpected.push(`${entry.method} ${url.pathname}`);
					return json(route, { error: 'No browser fixture for this endpoint' }, 404);
			}
		});
		await use(api);
		expect(api.unexpected, 'Unmocked API/external traffic').toEqual([]);
		expect(pageErrors, 'Browser runtime errors').toEqual([]);
	}
});

export { expect };
