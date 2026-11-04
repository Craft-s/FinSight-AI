# Browser regression tests

These tests start the actual Vue and FastAPI application on **4173 / 18000**.
They never load the repository `.env`, reuse a production server, write to the
normal snapshot database, contact SEC, or call a model. Both ports must be free.

Install backend dependencies in Python 3.12+ as described in the root README.
From `frontend`, run:

```sh
pnpm install --frozen-lockfile
pnpm test:e2e:install
pnpm test:e2e:check
pnpm test:e2e
```

`FINSIGHT_E2E_PYTHON` may point to the backend Python executable. Otherwise the
launcher uses the root `.venv` if present, then `python` from PATH. On Linux CI,
install Chromium OS dependencies with `pnpm exec playwright install --with-deps
chromium`.

If the Chromium CDN is unavailable but Chrome or Edge is already installed,
set `FINSIGHT_E2E_CHANNEL=chrome` or `msedge` to test that browser in an isolated
temporary profile. CI defaults to Playwright's downloaded Chromium. Record the
actual browser/channel in the test results; this does not reuse personal tabs.

The default creates a new temporary SQLite development database. All financial
data and filing HTML are the project's **real archived SEC snapshots**, shown as
historical archives in the UI, not live disclosures. No synthetic facts or AI
reports are inserted. SQLite deliberately cannot run production evidence search;
that branch verifies the explicit PostgreSQL requirement instead of inventing
search results.

To test actual PostgreSQL FTS, create an empty disposable database named, for
example, `finsight_e2e_test`. Set `FINSIGHT_E2E_DATABASE_URL` to its SQLAlchemy
`postgresql+psycopg://...` connection string, and optionally
`FINSIGHT_E2E_REDIS_URL` to a test-only Redis DB such as 14. The harness refuses
PostgreSQL database names not ending `_e2e_test`, binds the data mode to
`sec_snapshot`, and does not drop databases or schemas. CI uses this path with
separate job-local services. The browser API harness uses development inline
tasks; Linux RQ execution, model-protocol contracts and cancellation are covered
by the backend's dedicated PostgreSQL/Redis tests.

For Windows browsers with a WSL-only PostgreSQL, run
`python frontend/e2e/serve_api.py` in WSL using those isolated database settings
and `FINSIGHT_E2E_API_HOST=0.0.0.0`. The default API port is 18000, overridable
with `FINSIGHT_E2E_API_PORT`; port 8000 is rejected. In the Windows test shell set
`FINSIGHT_E2E_EXTERNAL_API=1` and `FINSIGHT_E2E_API_URL=http://<WSL-IP>:18000`,
then run `pnpm test:e2e`. Stop that test-only WSL API when finished. The harness
marker `/api/v1/_e2e/status` exists only in the test server and every browser test
checks it, so a normal production backend is rejected. Use a trusted local network
for this development-only, disposable-token harness.

Coverage includes company search, exact decimal display, quarterly data and
provenance, real filing/event links, research preselection, language preference
and session-token separation, no-model restrictions, real filing hash and search,
mobile overflow, and DOM XSS with an explicitly test-only intercepted metadata
response. It also checks light/dark/system themes, persisted preferences, global
search keyboard navigation, loading/error recovery, skip-link focus and toasts.

Two additional **browser-only synthetic fixtures** exercise report sections,
exact values, evidence, authorized Markdown download, and model settings
(disposable key, list/manual model IDs and unverified-configuration restrictions).
Their data is explicitly labeled `TEST ONLY` / `synthetic_fixture`; all relevant
responses are intercepted by Playwright. No fixture report or model configuration
is inserted into the backend. The report screenshot remains in ignored test
artifacts, outside the real-data gallery. These checks do not claim real model
generation, actual provider enumeration or a downloaded production AI report.

`cn.spec.ts` adds an explicitly `TEST ONLY` A-share browser fixture. It checks
market switching, exchange-qualified symbols without calling SEC CIK endpoints,
CNY precision and provenance, cumulative-period wording, controlled disclosure
links, unsupported AI capabilities, 390/320px usable viewport width and an
unauthorized sync that sends no POST. Its intercepted values never enter any
database or real-data screenshot gallery. Actual Eastmoney/CNINFO network and
Linux RQ verification are recorded separately in `docs/TEST_RESULTS.md`.

Playwright owns and stops auto-started test servers. It refuses existing test
ports. Failure screenshots/traces live in ignored `test-results/` and the HTML
report in ignored `playwright-report/`; CI retains failed-run diagnostics for
seven days. No API keys or production credentials belong in this test flow.

Official references: [Playwright CI](https://playwright.dev/docs/ci),
[webServer lifecycle](https://playwright.dev/docs/test-webserver).
