# publish-core-30-page.py

Path-2 automation for Core 30 page deployment on client WordPress sites. Replaces the 15-30-minute manual paste-through-wp-admin workflow with a ~30-second scripted publish.

**Canonical SOP:** [`sop-wordpress-rest-api-deploy.md`](../../../second-brain/05_shared-intelligence/workflows/workflow-marketing-seo-engagement/sops/sop-wordpress-rest-api-deploy.md) — read that for the full procedure. This README covers script-level usage.

## What it does

1. Picks the canonical HTML version from a Core 30 page folder (highest `draft-vN-WP-WRAPPED.html` by default).
2. Parses the v1 markdown frontmatter + AIOSEO metadata block.
3. Validates the embedded JSON-LD schema (parses every `<script type="application/ld+json">` block, confirms `LocalBusiness`, `Service`, and `FAQPage` are present).
4. POSTs to `/wp-json/wp/v2/pages` via Basic Auth. **Creates** if the slug is new, **updates** if a page with this slug already exists. On update, the script **preserves the existing publish status** — a published page stays published, a draft stays a draft. The `default_status` in the config only applies to NEW page creates.
5. **Populates AIOSEO meta automatically** via the Keelworks AIOSEO Bridge plugin (`POST /wp-json/keelworks/v1/aioseo-meta/<id>`). Writes title, meta description, focus keyword, and additional keywords directly to AIOSEO's custom DB table. Falls back to printing a paste-friendly block if the bridge plugin isn't installed on the target site. (AIOSEO uses a custom DB table — not wp_postmeta — and AIOSEO's own REST routes reject application-password Basic Auth, so we ship a small bridge plugin per client site. See `wordpress-plugins/keelworks-aioseo-bridge/`.)
6. Writes a per-page execution-log entry to `repos/<client_slug>/.kos/execution-logs/`.

## Setup

### One-time per client site

1. **Install Python 3.8+ and `requests`:**
   ```bash
   pip3 install requests --break-system-packages
   ```
   (The `--break-system-packages` flag is needed on modern macOS where system Python is locked down.)

2. **Enable WordPress application passwords.** Many security plugins disable them by default. For All-In-One Security (AIOS):
   - wp-admin → **Users → Profile** → scroll to **Application Passwords** section
   - If you see "Application passwords have been disabled by All-In-One Security plugin", click **Change setting**
   - In the AIOS panel: toggle **Disable application password** to OFF
   - Click **Save settings**

3. **Generate a WordPress application password:**
   - wp-admin → **Users → Profile** → **Application Passwords** section
   - Name field: `core-30-publish-script`
   - Click **Add New Application Password**
   - WP shows the password ONCE: 24 alphanumeric characters displayed as 6 groups of 4 separated by spaces (total 29 characters including spaces). Copy it now.

4. **Store the password in the Tier-3 vault**, NOT in the config file:
   - File: `~/workspace/second-brain-tier3/business-keelworks.md` (per-client section)
   - Use the scalable template (see [`sop-wordpress-rest-api-deploy.md`](../../../second-brain/05_shared-intelligence/workflows/workflow-marketing-seo-engagement/sops/sop-wordpress-rest-api-deploy.md) Prereq 4 for the template block)

5. **Install the Keelworks AIOSEO Bridge plugin** on the client site. Required for AIOSEO meta auto-population. Without it, the script falls back to printing a paste-friendly block. See [`sop-keelworks-aioseo-bridge-install.md`](../../../second-brain/05_shared-intelligence/workflows/workflow-marketing-seo-engagement/sops/sop-keelworks-aioseo-bridge-install.md) for the procedure. Plugin source + .zip at `repos/ai-agency-core/wordpress-plugins/keelworks-aioseo-bridge/`.

6. **(Optional) Set up the GSC Indexing API** to auto-submit every publish to Google's priority crawl queue. See [`sop-gsc-indexing-api-setup.md`](../../../second-brain/05_shared-intelligence/workflows/workflow-marketing-seo-engagement/sops/sop-gsc-indexing-api-setup.md) — one-time GCP service account + GSC owner add. Without this step, indexing falls back to the manual click in GSC's URL Inspection. Install adds `gsc_service_account_path` to the client config and `google-auth` to the Python env.

7. **Create the client config JSON:**
   - File: `~/workspace/repos/ai-agency-core/scripts/<client-slug>.config.json`
   - Template: copy from [`ev-electric.config.example.json`](ev-electric.config.example.json)
   - Critical fields to set:
     - `wp_base_url` — `https://<client-domain>` (no trailing slash)
     - `wp_username` — your WP login username
     - `default_author_id` — **set to `null`** (lets WP assign authorship to whoever authenticated). Setting a wrong number returns HTTP 400 `rest_invalid_author`.
     - `client_slug` — used to find the `repos/<client-slug>/.kos/execution-logs/` folder

### Per session

Export the app password from the Tier-3 vault into your shell. **Don't add it to `.bashrc` or commit it anywhere.**

```bash
export WP_APP_PASSWORD="abcd efgh ijkl mnop qrst uvwx"
```

Sanity check without revealing the password:

```bash
echo "WP_APP_PASSWORD is ${#WP_APP_PASSWORD} characters long"
```

Should print `29`.

## Usage

### Dry run (always do this first on any new page)

```bash
python3 ~/workspace/repos/ai-agency-core/scripts/publish-core-30-page.py \
    --page-folder ~/workspace/second-brain/04_projects/clients/_active/<client>/website-archive/new/core-30/<NN>-<slug> \
    --config       ~/workspace/repos/ai-agency-core/scripts/<client>.config.json \
    --dry-run
```

Validates schema + extracts slug/title/AIOSEO meta. Doesn't POST. Catches issues before any state change.

### Real publish

Same command without `--dry-run`:

```bash
python3 ~/workspace/repos/ai-agency-core/scripts/publish-core-30-page.py \
    --page-folder ~/workspace/second-brain/04_projects/clients/_active/<client>/website-archive/new/core-30/<NN>-<slug> \
    --config       ~/workspace/repos/ai-agency-core/scripts/<client>.config.json
```

Default `default_status` is `"draft"` so the page lands as a draft for visual review before going live. Flip to publish via wp-admin bulk-edit. Change `default_status` to `"publish"` in the config if you want auto-publish.

### Bulk publish (multiple pages in one shell loop)

```bash
for folder in 02-panel-upgrade-vienna-va 03-ev-charger-vienna-va 04-electrical-troubleshooting-fairfax-va; do
  echo "=== Publishing $folder ==="
  python3 ~/workspace/repos/ai-agency-core/scripts/publish-core-30-page.py \
      --page-folder ~/workspace/second-brain/04_projects/clients/_active/<client>/website-archive/new/core-30/$folder \
      --config       ~/workspace/repos/ai-agency-core/scripts/<client>.config.json
done
```

Each page's AIOSEO META block prints under its publish output. Scroll back through the terminal to copy each one into wp-admin.

### Choose a specific version

```bash
python3 publish-core-30-page.py \
    --page-folder /path/to/02-panel-upgrade-vienna-va \
    --config       /path/to/<client>.config.json \
    --version v3
```

## What the script does NOT do (yet)

- **Image upload / swap.** Hero, Ahmad portrait, and Google Maps placeholders remain as placeholder divs after publish. Swap them in wp-admin → page → edit when the AI-generated images are uploaded to the Media Library.
- **LiteSpeed cache purge.** WP REST publish doesn't trigger LiteSpeed purge. Manual via wp-admin → LiteSpeed → Toolbox → Empty Entire Cache after publishing.
- **Flip draft to publish.** Default `default_status: "draft"` means each new page lands as a draft. Manual flip via wp-admin → Pages → Drafts filter → Bulk Edit → Status: Published.

## What the script DOES do (newly wired 2026-05-27)

- **GSC indexing request.** When the client config has `gsc_service_account_path` pointing to a valid service account JSON key (Tier-3 vault), every publish auto-submits the live URL to Google's Indexing API at `urlNotifications:publish` with `type: URL_UPDATED`. The publish output shows `→ GSC indexing: submitted (timestamp)` on success or `→ GSC indexing: [error] ...` on failure. Indexing failures never block the publish — the page still goes live. One-time setup procedure: [`sop-gsc-indexing-api-setup.md`](../../../second-brain/05_shared-intelligence/workflows/workflow-marketing-seo-engagement/sops/sop-gsc-indexing-api-setup.md).

## Path 1 vs Path 2

| | Path 1 (manual) | Path 2 (this script) |
|---|---|---|
| Time per page (deploy only) | 15-30 min | ~30 sec script + 1 min cache purge (~2 min faster with bridge plugin installed) |
| Schema validation | "did I forget anything?" | automated check before POST |
| AIOSEO meta entry | manual | automatic via Keelworks AIOSEO Bridge plugin; falls back to paste-friendly print block if plugin not installed |
| Hero image swap | manual | manual (same in both paths) |
| Maps embed | manual | manual (same in both paths) |
| GSC indexing request | manual click | automatic via Indexing API when `gsc_service_account_path` is configured; falls back to manual click otherwise |
| Status preservation on re-publish | manual care | automatic — won't demote published to draft |
| Risk of "wrong slug pasted" | non-zero | zero (slug derived from folder name) |

## Common errors

### `ERROR: \`requests\` is not installed`

```bash
pip3 install requests --break-system-packages
```

### `FATAL: WP create_page failed: 400 — rest_invalid_author`

Your config's `default_author_id` is wrong. Set it to `null` in the config:

```json
"default_author_id": null,
```

### `FATAL: WP create_page failed: 401`

Wrong app password, or user doesn't have permission to create pages. Verify the user is Admin or Editor, the password is current (not revoked), and `$WP_APP_PASSWORD` is exported in this terminal.

### Page publishes but AIOSEO META block prints empty values

The `draft-v1.md` file is missing the AIOSEO metadata block, or the values aren't backtick-quoted. Format:

```markdown
- **Page title (browser tab + AIOSEO Title):** `Title here`
- **Meta description (AIOSEO Description, 152 chars):** `Description here`
- **Focus keyword (AIOSEO):** `keyword here`
- **Additional keywords:** `kw1`, `kw2`, `kw3`
```

The script parses these via regex on the backtick markers.

### Page renders with white gap or duplicate H1

The HTML's CSS is missing the theme-wrapper override or hide-list extensions. See [`pattern-core-30-page-design-system.md`](../../../second-brain/05_shared-intelligence/patterns/pattern-core-30-page-design-system.md) Overrides 1 + 2 + 3. Apply to the HTML and re-run the script (which will update the existing page).

## When NOT to use this script

- The FIRST page of any new client where the template still needs visual iteration. Use Path 1 (manual) for that. After page 1 is locked, Path 2 for everything else.
- Pages that don't follow the v9 template (11 sections, JSON-LD `@graph` shape). The script assumes the schema-validation contract.
- A client whose WordPress site uses Yoast or RankMath instead of AIOSEO. Script still works — the AIOSEO meta print block just isn't useful. Fill in Yoast/RankMath by hand.

## Related

- SOP: [`sop-wordpress-rest-api-deploy.md`](../../../second-brain/05_shared-intelligence/workflows/workflow-marketing-seo-engagement/sops/sop-wordpress-rest-api-deploy.md)
- Pattern: [`pattern-core-30-page-design-system.md`](../../../second-brain/05_shared-intelligence/patterns/pattern-core-30-page-design-system.md)
- Canonical template HTML: `second-brain/04_projects/clients/_active/ev-electric-services/website-archive/new/core-30/01-electrical-troubleshooting-vienna-va/draft-v9-WP-WRAPPED.html`
- WP REST API pages endpoint: https://developer.wordpress.org/rest-api/reference/pages/
- AIOSEO REST endpoint research (for future automation): `wp-content/plugins/all-in-one-seo-pack/app/Common/Api/` in any AIOSEO install
