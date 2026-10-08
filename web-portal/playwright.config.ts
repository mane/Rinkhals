import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
	testDir: './tests/browser',
	fullyParallel: true,
	forbidOnly: Boolean(process.env.CI),
	retries: process.env.CI ? 2 : 0,
	workers: 2,
	reporter: [['list'], ['html', { outputFolder: 'output/playwright/report', open: 'never' }]],
	outputDir: 'output/playwright/test-results',
	use: {
		baseURL: 'http://127.0.0.1:4173',
		trace: 'retain-on-failure',
		screenshot: 'only-on-failure'
	},
	projects: [{
		name: 'chromium',
		use: { ...devices['Desktop Chrome'], channel: process.env.PLAYWRIGHT_CHANNEL || undefined }
	}],
	webServer: {
		command: 'npm run preview -- --host 127.0.0.1 --port 4173 --strictPort',
		url: 'http://127.0.0.1:4173',
		reuseExistingServer: false,
		timeout: 30_000
	}
});
