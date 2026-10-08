import { test, expect } from './fixtures';

for (const failure of ['http', 'logical'] as const) {
	test(`a ${failure} config failure stops Enable app, and a retry can succeed`, async ({ page, api }) => {
		api.configFailure = failure;
		await page.goto('/apps/');
		await page.getByRole('button', { name: 'Configure', exact: true }).click();
		await page.getByLabel('Access token').fill('my-new-token');
		const enable = page.getByRole('button', { name: 'Enable app', exact: true });
		await enable.click();
		await expect(page.getByText('Mock write failed', { exact: true })).toBeVisible();
		await expect(enable).toBeEnabled();
		await expect(page.getByLabel('Access token')).toHaveValue('my-new-token');
		expect(api.requests.filter((r) => r.path.endsWith('/enable') || r.path.endsWith('/action'))).toEqual([]);

		api.configFailure = null;
		const requestCount = api.requests.length;
		await enable.click();
		await expect(page.getByText('Mock app enabled and started', { exact: true })).toBeVisible();
		await expect(enable).not.toBeVisible();
		expect(api.requests.slice(requestCount).filter((r) => r.method !== 'GET').map((r) => r.path)).toEqual([
			'/api/apps/mock-app/config', '/api/apps/mock-app/enable', '/api/apps/mock-app/action'
		]);
		expect(api.app.properties[0].value).toBe('my-new-token');
	});
}

test('an open drawer refreshes QR/report output while preserving edits and can close/reopen', async ({ page, api }) => {
	api.app.enabled = true;
	api.app.running = true;
	await page.clock.install();
	await page.goto('/apps/');
	await page.getByRole('button', { name: 'Configure', exact: true }).click();
	await page.getByLabel('Access token').fill('not saved yet');
	await expect(page.getByText('Waiting for the app to publish its login URL...')).toBeVisible();

	api.app.properties[1].value = 'https://example.invalid/login';
	api.app.properties[2].value = '{"status":"Connected"}';
	await page.clock.fastForward(5_100);
	await expect(page.getByRole('link', { name: 'https://example.invalid/login', exact: true })).toBeVisible();
	await expect(page.getByText('Connected', { exact: true })).toBeVisible();
	await expect(page.getByLabel('Access token')).toHaveValue('not saved yet');
	await page.getByRole('button', { name: 'Close', exact: true }).last().click();
	await expect(page.getByLabel('Access token')).not.toBeVisible();
	await page.getByRole('button', { name: 'Configure', exact: true }).click();
	await expect(page.getByLabel('Access token')).toHaveValue('');
	await expect(page.getByText('Connected', { exact: true })).toBeVisible();
});

test('clearing an existing file keeps Save enabled and sends an empty string', async ({ page, api }) => {
	await page.goto('/editor/?path=%2Ftmp%2Fbrowser-test.cfg');
	const editor = page.getByPlaceholder('File content...');
	await expect(editor).toHaveValue(api.fileContent);
	await editor.fill('');
	const save = page.getByRole('button', { name: 'Save file', exact: true });
	await expect(save).toBeEnabled();
	await save.click();
	await expect(page.getByText('File saved successfully', { exact: true })).toBeVisible();
	const saves = api.requests.filter((r) => r.path === '/api/saveFile');
	expect(saves).toHaveLength(1);
	expect(saves[0].body).toEqual({ path: '/tmp/browser-test.cfg', content: '' });
});

test('credential changes authenticate with the current username and submit the new username', async ({ page, api }) => {
	await page.goto('/management/');
	await page.getByRole('button', { name: 'Change credentials', exact: true }).click();
	await page.getByLabel('Current username', { exact: true }).fill(api.currentUsername);
	await page.getByLabel('Current password', { exact: true }).fill(api.currentPassword);
	await page.getByLabel('New username', { exact: true }).fill('new-user');
	await page.getByLabel('New password', { exact: true }).fill('new-password');
	await page.getByLabel('Confirm new password', { exact: true }).fill('new-password');
	await page.getByRole('button', { name: 'Save credentials', exact: true }).click();
	await expect(page.getByText(/Password updated successfully/)).toBeVisible();
	const auth = `Basic ${Buffer.from(`${api.currentUsername}:${api.currentPassword}`).toString('base64')}`;
	const statusCheck = api.requests.find((r) => r.path === '/api/auth/status' && r.authorization);
	expect(statusCheck?.authorization).toBe(auth);
	const change = api.requests.find((r) => r.path === '/api/auth/change');
	expect(change?.authorization).toBe(auth);
	expect(change?.body).toEqual({ username: 'new-user', password: 'new-password' });
});
