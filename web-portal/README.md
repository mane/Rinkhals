# Rinkhals web portal

The Svelte portal is compiled to static files and embedded in the Go backend.

## Development

```sh
npm ci
npm run dev
```

Run the Go backend on `localhost:8090`. Vite proxies `/api`, including terminal
and log WebSockets, so browser requests use the same origin in development.

## Checks and production build

```sh
npm run check
npm test
npm run build
```

The Node tests exercise component request handling and transactional app downloads.
The build writes the static portal to `build/`.

## Browser regression tests

```sh
npx playwright install chromium
npm run build
npm run test:browser
```

`test:browser` checks the browser tests' TypeScript and runs Chromium against a
local preview of the production build at `http://127.0.0.1:4173`. Port 4173 must
be free. Playwright starts and stops that server automatically.

The fixtures intercept every API request and WebSocket; unexpected endpoints or
external traffic fail the tests. These tests do not need or contact a printer.
They cover rejected configuration saves, successful enable/start sequencing,
QR/status polling with unsaved edits, closing/reopening the configuration drawer,
saving an empty file, and changing credentials with a different username.

To use an existing local Chrome installation instead of downloading Chromium:

```sh
PLAYWRIGHT_CHANNEL=chrome npm run test:browser
```

On Linux CI, install Chromium's system dependencies before running the tests:

```sh
npx playwright install --with-deps chromium
npm run test:browser
```

Failure screenshots, traces, and the HTML report are saved under
`output/playwright/` (ignored by Git).
