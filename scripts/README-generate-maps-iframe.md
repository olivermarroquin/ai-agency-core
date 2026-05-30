# generate-maps-iframe.py

Produces the v17-styled Google Maps iframe HTML for a Core 30 page. Replaces
the manual google.com/maps → Share → Embed → Copy walkthrough.

**Companion docs:**

- Pattern: [`pattern-core-30-page-design-system.md`](../../../second-brain/05_shared-intelligence/patterns/pattern-core-30-page-design-system.md) — Section 8 "Google Maps iframe" subsection points at this script.
- Sibling automation: [`publish-core-30-page.py`](publish-core-30-page.py) — runs downstream of this one (you build the HTML draft with the iframe pasted in, then publish).

## What it does

1. Takes a city + state (one city, or a batch file with many).
2. Builds a Google Maps Embed API place-mode URL with the cross-Keelworks API key.
3. Wraps it in the v17 `<div class="evp-map">…</div>` block matching the design system.
4. Caches the result per `(city, state, zoom)` in `cache/maps-iframes.json` so subsequent runs return the same bytes instantly with no API call.
5. Prints the paste-ready HTML to stdout. Pipe to `pbcopy` on macOS to put it straight on the clipboard.

## What it does NOT do

- **No HTTP call from the script.** The Embed API URL is fully deterministic from `(city, state, zoom)` plus the key — the iframe itself (rendered in a visitor's browser) is what calls Google. Our script just constructs the URL.
- **No image swap into HTML drafts.** This is an upstream-of-publish step: the operator pastes the output into the page draft's Section 8, then runs `publish-core-30-page.py` to deploy. A future enhancement could replace `<!-- MAP:Vienna,VA -->` markers in drafts automatically; the script's API is designed to make that an easy add-on.
- **No `?pb=…` URL generation.** That undocumented URL format is only produced by google.com/maps → Share → Embed. The Embed API URL (`/embed/v1/place?key=…`) renders an essentially identical map and is documented + free.

## Setup

### One-time per machine

```bash
# Python 3.8+ is enough. No third-party dependencies — uses only the stdlib.
python3 --version
```

That's it. No `pip install` needed.

### One-time per Keelworks (shared across all clients)

1. **Create a Google Cloud project** at console.cloud.google.com.
2. **Enable the Maps Embed API** for that project (APIs & Services → Library → search "Maps Embed API" → Enable).
3. **Create an API key:** APIs & Services → Credentials → Create credentials → API key. Restrict the key to "Maps Embed API" only (don't leave it broad).
4. **Store the key in the tier-3 vault** at `~/workspace/second-brain-tier3/personal/business-keelworks.md`, under a Keelworks-tools section. Same vault file as `WP_APP_PASSWORD`. One key serves every client; no per-client keys needed.

   Suggested entry shape:

   ```markdown
   ## Google Maps Embed API key (cross-client)

   - **GCP project:** keelworks-tools (or whatever name you picked)
   - **Key restriction:** Maps Embed API only
   - **Env var name:** GOOGLE_MAPS_EMBED_API_KEY
   - **Created:** 2026-05-25
   - **Billing:** Free tier — place-mode embed has no quota cap.
   - **Move-to-client steps (if ever needed):** see README-generate-maps-iframe.md
   ```

5. **Add a pointer entry** to each client's `credentials.reference.md` listing `GOOGLE_MAPS_EMBED_API_KEY` so future-you knows where to look. (Already done for EV Electric on 2026-05-25.)

### Per session

Export the key into the shell. Don't add it to `.bashrc` or commit it anywhere.

```bash
export GOOGLE_MAPS_EMBED_API_KEY="AIzaSyD…"
```

Sanity check without revealing the key:

```bash
echo "GOOGLE_MAPS_EMBED_API_KEY is ${#GOOGLE_MAPS_EMBED_API_KEY} characters long"
```

Real Google API keys are usually 39 characters.

## Usage

### One city, paste-ready

```bash
python3 ~/workspace/repos/ai-agency-core/scripts/generate-maps-iframe.py \
    --city "Vienna" --state "VA"
```

Output: the wrapped HTML on stdout, a `[cache]` or `[fresh]` marker on stderr.

To put the HTML on the clipboard directly:

```bash
python3 ~/workspace/repos/ai-agency-core/scripts/generate-maps-iframe.py \
    --city "Vienna" --state "VA" | pbcopy
```

### Many cities at once

Create a text file with one `City, ST` per line:

```text
# cities.txt — EV Electric Core 30 service-area cities
Vienna, VA
Fairfax, VA
McLean, VA
Oakton, VA
Tysons, VA
Burke, VA
Annandale, VA
Falls Church, VA
Rockville, MD
Bethesda, MD
```

Then:

```bash
python3 generate-maps-iframe.py --batch ./cities.txt
```

Each city's iframe is printed separated by a blank line. Easy to split on paste.

### Force a fresh fetch

If you ever need to regenerate (e.g., changed the zoom level in the config):

```bash
python3 generate-maps-iframe.py --city "Vienna" --state "VA" --force-refresh
```

### Audit the cache

```bash
python3 generate-maps-iframe.py --list
```

Prints every cached entry with its generation timestamp.

### Skip writing to the cache

```bash
python3 generate-maps-iframe.py --city "Vienna" --state "VA" --no-save
```

Handy for ad-hoc experiments where you don't want to pollute the canonical cache.

## Config

The script's baked-in defaults match EV Electric Services. For another client,
write a JSON config:

```json
{
  "api_key_env":  "GOOGLE_MAPS_EMBED_API_KEY",
  "client_name":  "S&H Contracting",
  "default_zoom": 12,
  "cache_path":   "cache/maps-iframes.json"
}
```

Then pass `--config /path/to/sh-contracting.maps.config.json`. The `client_name`
field changes the iframe `title` attribute (used for accessibility) to reflect
the right client.

`cache_path` resolves relative to the script directory unless you give an
absolute path. Default points to `cache/maps-iframes.json` next to the script.

## Output format

Identical to the v17 page-1 wrapper in
[`pattern-core-30-page-design-system.md`](../../../second-brain/05_shared-intelligence/patterns/pattern-core-30-page-design-system.md)
Section 8:

```html
<div class="evp-map" style="background:none; padding:0; overflow:hidden; border-radius:20px; border:none; display:block;">
  <iframe src="https://www.google.com/maps/embed/v1/place?key=…&q=Vienna%2C+VA&zoom=12"
          width="600"
          height="450"
          style="width:100%; height:100%; min-height:380px; border:0; display:block;"
          allowfullscreen=""
          loading="lazy"
          referrerpolicy="no-referrer-when-downgrade"
          title="Map of Vienna, VA — EV Electric Services service area"></iframe>
</div>
```

Paste into Section 8 of the page draft, replacing the existing `evp-map` block.

## Billing — who pays

Per the EV Electric engagement, tool subscriptions go on the client's card.
The Maps Embed API's place-mode is free with no quota cap, so today there's no
spend to allocate. The key is on a Keelworks-owned GCP project for convenience
(same project that can later hold the GSC Indexing API service account, the
Geocoding API key, and other shared tooling).

**If billing ever moves to paid usage** — for instance, if we switch from the
place-mode embed to a feature with a per-request charge — the move-to-client
steps are:

1. Create a new GCP project under Ahmad's Google account (`evelectric1800@gmail.com`).
2. Enable the relevant API on that project and create a new restricted key.
3. Update the key in `~/workspace/second-brain-tier3/personal/business-keelworks.md` (replace, don't append).
4. Re-export `GOOGLE_MAPS_EMBED_API_KEY` and run `--force-refresh` on every cached city to regenerate iframes with the new key.
5. Update the credentials pointer to note that the key now belongs to the client.

For now: free tier, Keelworks key, no per-client setup needed.

## Common errors

### `ERROR: $GOOGLE_MAPS_EMBED_API_KEY not set in the environment`

You didn't export the key in this shell. Re-export from the tier-3 vault. The
script does not read the vault directly (that would defeat the air-gap).

### Iframe renders "This page can't load Google Maps correctly"

The key is invalid, the Maps Embed API isn't enabled on the GCP project, the
key restrictions are too tight (e.g., HTTP referrer restrictions block
evelectric.pro), or billing is disabled on a project that requires it. Check
the GCP console under APIs & Services → Credentials.

### Cache miss on what should be a cache hit

Cache keys include the zoom level: `"Vienna, VA @z12"`. If the config's
`default_zoom` changed, old entries miss. Either set the zoom back, or run
`--force-refresh` on the cities you want at the new zoom.

## Future enhancements

- **Marker-substitution in HTML drafts.** Replace `<!-- MAP:Vienna,VA -->` markers in `draft-vN-WP-WRAPPED.html` files in one pass. Wire into `publish-core-30-page.py` as a pre-publish step.
- **Custom map styling.** The Embed API doesn't support Google Maps Styled Maps, but the Embed API's `view` mode plus the Static Maps API together can render brand-colored maps. Worth exploring if a future client wants navy-and-yellow maps to match their site palette.
- **Zoom auto-tuning.** Smaller cities (Oakton, Tysons) often look better at zoom 13–14. The script could lookup a per-city zoom override from the config, falling back to `default_zoom`.

## Related

- Pattern: [`pattern-core-30-page-design-system.md`](../../../second-brain/05_shared-intelligence/patterns/pattern-core-30-page-design-system.md)
- Sibling deploy script: [`publish-core-30-page.py`](publish-core-30-page.py) and its [README](README-publish-core-30-page.md)
- Google's Embed API docs: <https://developers.google.com/maps/documentation/embed/embedding-map>
- Cache folder: [`cache/_README.md`](cache/_README.md)
