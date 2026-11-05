# Dashboard source

Build with Node.js 18 or later. No npm install, package downloads, framework compiler, or production Node server is required.

```sh
npm --prefix frontend run build
npm --prefix frontend test
python scripts/prepare_dashboard.py --output dist/source-ui
```

`frontend/dist/` is ignored. `build.mjs --output <directory>` supports packaging directly into a staging UI directory. `prepare_dashboard.py` and the Debian packager run the same builder; set `PPGW_NODE` if Node is outside PATH. Output filenames contain the first 12 SHA256 characters of their content, and `manifest.json` describes the entry and assets. Text sources use LF for stable Windows/Linux builds. Rebuilding removes only the previous manifest's generated asset basenames, then emits the current graph. It never recursively deletes an output directory.

## Source boundaries

- `src/shell.js`, `auth.js`, and `shell.css` own login, navigation, history guards, mobile controls, theme and custom colors. Authentication retains the `PaoPaoGateWay` storage shape and unrelated preferences. SHA256 works on HTTP LAN origins without WebCrypto. A 401 offers an in-place login dialog that retains editor drafts.
- Settings and source selection have one authority: `integration/gateway.js`, `gateway_view.html`, and `gateway.css`. They are built directly. The shell supplies public mount/navigation/runtime hooks.
- `src/editor.js` owns the subscription form and `src/model.js` owns lossless document edits. The model retains every imported field; controls change only fields the user touches. `_clash_id` stays attached through edits and ordering. Copies remove the identity to create a new row. Clash provider structure and existing rule kinds remain restricted. PPsub supports provider plans, groups, rules, global monitor, Shadowsocks input, scheduled restarts, import/export, and extension JSON.
- `integration/ppsub_panel.js` remains the sole persistence/preview/task implementation. The source form implements its public `mount({header, exportConfig, importConfig, providers})` contract and retains its locking, revision checks, provider polling, update plans, task recovery, and navigation protection.
- `src/diagnostics.js` and `diagnostics.css` own the diagnostics and recovery route. Diagnostics reports and backups load on demand; only the requested backup page is checked. Restore/recovery require a fresh signed impact preview and explicit confirmation. An uncertain submission only polls its original job ID. Session storage retains the ID/action alone, and a reload returns to that task. Navigation, logout, browser Back, and unload share the pending task guard. A polling 401 retains the job and reauthentication resumes it; an explicit submit 4xx rejection unlocks the page for a new preview. Completion distinguishes verified, inactive, and staged acceptance and explains any preserved worker service state. Reports are projected onto fixed public fields and enums before display/export.
- Existing overview, proxies, rules, connections, traffic, distribution, logs, and region pages run in the same-origin `legacy.html` iframe. Its original JS bundle is copied byte-for-byte. `legacy-bridge.js` adapts public DOM navigation and messages; it does not reference Svelte internals. Both sides require the expected origin and window source. Tokens never enter URLs or messages. Runtime mode refresh independently updates the source navigation after an apply.

The original index CSS provides the established theme baseline. The builder only reads its standard `<style>` and module import entry. It does not patch compressed JS symbols. Updating the upstream dashboard requires checking the public DOM bridge and compatibility pages.

## Synthetic browser acceptance

These scripts intercept all requests on synthetic origins and never contact a deployed gateway. Install Playwright separately or set `PPGW_PLAYWRIGHT_MODULE` to the existing Playwright module. Chrome is used through Playwright's `chrome` channel. Set `PPGW_PYTHON` to the Python interpreter that can import the repository's backend dependencies.

```sh
python -m unittest discover -s tests -p test_dashboard.py
node scripts/verify-dashboard-fixture.cjs frontend/dist
node scripts/verify-runtime-navigation.cjs frontend/dist
node scripts/verify-settings-fixture.cjs frontend/dist
node scripts/verify-subscription-drafts.cjs frontend/dist
node scripts/verify-subscription-flow.cjs frontend/dist
node scripts/verify-source-editor.cjs frontend/dist
node scripts/verify-diagnostics-fixture.cjs frontend/dist
```

The dashboard build test checks the manifest hashes, untouched compatibility bundle, and byte-identical repeated builds. The existing browser suites retain their capability assertions with source DOM/frame selectors: settings validation and recovery, mobile themes, back guards, source changes, provider refresh and schedule drafts, and backend-driven change previews. The source editor suite additionally edits real fields in both kinds and calls backend `validate_ppsub`, `feature_options`, and `merge_editor` on the submitted JSON, verifying preserved hidden credentials and unknown fields. It tests login on a non-secure HTTP origin, copied/moved/deleted rows, reauthentication with drafts, stored preferences, and untrusted navigation messages.

The diagnostics fixture verifies paginated integrity checks, invalid backup controls, allowlisted exports, explicit backup confirmation, signed restore/recovery previews, stale preview rejection, uncertain submissions, interrupted connections, same-ID reload/poll continuation, and pending navigation/logout/Back/unload guards. It separately checks definite submit-401 rejection versus polling-401 with a historical password, all acceptance states, the post-completion worker restart command, custom/light themes, mobile 320px layouts, and text-only rendering of untrusted values. A missing job never unlocks automatically: read-only diagnostics are shown, pending recovery prevents release, and an explicit human acknowledgement plus a fresh diagnostic check can stop this browser's tracking without a server mutation or replay. A new accepted job continues polling automatically while an earlier completed job's runtime refresh is delayed. Visible desktop navigation is checked against the complete free-mode route list and readable text bounds. The Node diagnostics tests independently check report projection and persisted job metadata.

Browser evidence and screenshots go under ignored `dist/`. No production API or real subscription is needed.
