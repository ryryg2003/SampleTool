# Add-on research: search free sample sources from inside the app

Status: **built in v1.0** as the Find Sounds tab (`samplelib/freesound.py`,
`samplelib/api_freesound.py`, `ui/findsounds.js`). The API facts below were
re-checked against the live Freesound docs on 16 November 2026; see
"Verified API facts" at the end.

## Recommendation

Build one integration, **Freesound**, and treat every other free source as a
curated link list plus a drop-folder workflow. Freesound is the only large free
sound library found with a documented public search API and machine-readable
licenses. The popular loop sites either have no API or forbid automated access.
Scraping them would break their terms and risk the account you use there.

## What each source allows (checked November 2026)

| Source | Programmatic search | License per sound | Verdict |
| --- | --- | --- | --- |
| Freesound | Yes. APIv2 text search with filters on tag, duration, `bpm`, `tonality`, `note_name`, file type, license, uploader. Previews via API key; original files need OAuth2. | Yes: Creative Commons 0, Attribution, Attribution NonCommercial | **Integrate** |
| Looperman | No API found. Terms prohibit "any robot, spider, rover, scraper" and downloading "using any computer program". | Site-wide royalty-free license for registered users, non-transferable | Link out only |
| Splice, Loopcloud | Catalogs are browsable on their sites; no public developer API found | Subscription licenses | Out of scope (paid) |
| Ghosthack, r-loops, Function Loops, other "free pack" pages | No API; packs are usually email-gated downloads | Varies per pack, often "royalty-free, no resale" | Link out only |
| Internet Archive | Yes. `advancedsearch.php` returns a `licenseurl` field and can filter on Creative Commons | Per item, mixed quality | Possible later; mostly field recordings and music, not production samples |

Freesound limits (official docs): standard calls **60 requests/minute and
2,000/day**; original-quality downloads **30/minute and 500/day**. Higher
limits are by request.

## Proposed feature

A **Find Sounds** tab next to Library:

1. Search box plus the same filter chips you already use (instrument tag, BPM,
   key, duration, one-shot vs loop), mapped to Freesound filters, e.g.
   `filter=tag:kick duration:[0 TO 2] license:"Creative Commons 0"`.
2. **License filter on by default: Creative Commons 0 only** (no attribution, commercial use OK).
   "Attribution" can be switched on. "NonCommercial" stays off and is labeled
   as unusable for released music.
3. Audition the MP3 preview in the existing player.
4. **Download** saves the original into `E:\Sample Packs\_Freesound\<uploader>\`
   with a sidecar `.license.txt` (sound URL, author, license). The next normal
   scan picks it up as a pack named `_Freesound`. Downloads never write into
   `Samples\` directly, so the review-and-approve flow stays the only way files
   enter the organized library.
5. The catalog export gains `license` and `attribution` columns for these
   files, so you have a credit list when you release a track.

For every other source: a "Free pack sources" panel of links that opens in your
browser. You download as usual into `E:\Sample Packs\` and run a scan.

## Cost, effort and risks

- **Startup cost:** $0. A Freesound account and API key are free.
- **Build effort:** about one to two days. Search and preview need only the API
  key. Original-file downloads need the OAuth2 login flow, which is most of the
  work.
- **Biggest risk: licensing.** A sound labeled CC0 can still be a re-upload of
  someone else's copyrighted sample. Treat Freesound as a source for building
  blocks (hits, foley, textures). Don't use it for recognizable loops. Keep the
  sidecar license file with every download.
- **Secondary risks:** the 500 originals/day limit, quality varies a lot, and
  the API key must stay on your PC (store it in the app data folder, never in
  the repo).

## Open questions (resolved November 2026)

- **License filter syntax.** The resources page lists `license` as a filterable
  field with exactly three values: `Attribution`, `Attribution NonCommercial`,
  `Creative Commons 0`. Filters use the Solr grammar (`field:value`, quoted
  multi-word values, `[a TO b]` ranges with uppercase `TO`, and
  `field:(a OR b)`). The app sends `license:"Creative Commons 0"` by default and
  `license:("Creative Commons 0" OR "Attribution")` when Attribution is switched
  on. The docs still give no verbatim license example, so the server also drops
  any NonCommercial (or unrecognised) license that comes back, whatever the
  filter did.
- **Preview caching.** Not settled by the docs, so the app does not cache
  previews on disk. It streams each preview through the local server and keeps
  only a small in-memory cache (about 20 previews) for replay.

## Verified API facts (checked 16 November 2026)

Pages read: [overview](https://freesound.org/docs/api/overview.html),
[resources](https://freesound.org/docs/api/resources_apiv2.html),
[authentication](https://freesound.org/docs/api/authentication.html).

- **Text search:** `GET https://freesound.org/apiv2/search/` with `query`,
  `filter`, `sort` (`score` default; `duration_desc|asc`, `created_desc|asc`,
  `downloads_desc|asc`, `rating_desc|asc`), `fields` (comma list; default
  `id,name,tags,username,license`), `page` (default 1), `page_size` (default 15,
  max 150). The app asks for `id,name,username,duration,license,previews,url,
  tags,bpm,tonality,type,samplerate,channels,filesize`.
- **Sound fields:** `license` is a plain string (one of the three names above);
  the app also accepts a Creative Commons URL in case the API returns one.
  `previews` has `preview-hq-mp3` (about 128 kbps), `preview-lq-mp3`,
  `preview-hq-ogg`, `preview-lq-ogg`. `bpm` is an integer and `tonality` a
  string such as `C minor` (there is no `key` field). `url` is the sound page.
- **Auth:** token auth with the API key as `token=` or the header
  `Authorization: Token <key>` (the app uses the header so the key never
  appears in a URL). Retrieving previews does not need OAuth2.
- **Original files need OAuth2:** `GET /apiv2/sounds/<id>/download/` with
  `Authorization: Bearer <access_token>`. Authorization-code flow:
  `https://freesound.org/apiv2/oauth2/authorize/?client_id&response_type=code&redirect_uri&state&code_challenge&code_challenge_method=S256`.
  PKCE is **required for applications registered from November 2026**. The
  `redirect_uri` must equal the redirect URL registered for the API credential;
  alternatively Freesound itself can be the redirect target and shows the code
  to copy. Codes live 10 minutes and work once. Token exchange and refresh are
  `POST https://freesound.org/apiv2/oauth2/access_token/` (`client_id`,
  `grant_type=authorization_code|refresh_token`, `code`/`refresh_token`,
  `redirect_uri`, `code_verifier`); apps are public clients, so no client
  secret is sent. Access tokens last 24 hours (`expires_in`), refresh returns a
  new access and refresh token. Expired token: 401 "Expired token".
- **Localhost redirect:** the docs neither allow nor forbid `http://127.0.0.1`.
  Forum answers from the Freesound developers say to register the address you
  use locally (for example `http://localhost:5000/callback`), so the app
  offers `http://127.0.0.1:<port>/api/freesound/oauth/callback` and, as a
  fallback, "paste the code" from Freesound's own redirect page. **Unverified
  until a user registers a credential and tries it.**
- **Rate limits:** 60 requests/minute and 2,000/day for standard calls; 30/minute
  and 500/day for original downloads (and other write-like calls). Over the
  limit the API answers **HTTP 429** with a `detail` field naming the limit.
  The docs mention no rate-limit or `Retry-After` headers, so the app honours
  `Retry-After` when present and otherwise waits 60 s for a minute limit and
  until next UTC midnight for a daily one. `GET /apiv2/usage/` reports usage
  and is itself not throttled (not used yet; the app counts locally).

## How the app implements it

- The browser never talks to freesound.org. The local server proxies search and
  preview audio, so the API key stays in `freesound.json` in the app data
  folder. The UI only sees `has_key` and a masked hint such as `abcd…wxyz`.
- Client-side throttle: a token bucket of 50 calls/minute for search and 25/minute
  for downloads, a local daily counter (`freesound_usage.json`, UTC day), and a
  block after any 429 until the retry time. While blocked no request leaves
  the PC.
- Download writes only to `<collection>\_Freesound\<uploader>\` (sanitized
  for Windows), via a temporary file in that folder and a no-overwrite
  link/exclusive create. A name already taken (case-insensitive) gets ` (2)`,
  ` (3)` and so on. Every download gets `<file>.license.txt` with the sound URL,
  title, author, license name and URL, date and an attribution line. Downloads
  are refused while an approved plan is running. The next normal scan, review
  and approval is what moves them into `Samples\`.
- Original files use the OAuth2 flow above (needs your own API credential's
  client ID). Without it, **Download preview (MP3)** saves the 128 kbps preview,
  which needs only the API key and is clearly labelled as lower quality.

Sources: [Freesound APIv2 overview](https://freesound.org/docs/api/overview.html),
[Freesound APIv2 resources](https://freesound.org/docs/api/resources_apiv2.html),
[Looperman terms](https://www.looperman.com/help/terms),
[Openverse issue on Internet Archive `licenseurl`](https://github.com/WordPress/openverse/issues/1777),
[Splice sounds](https://splice.com/sounds), [Ghosthack free packs](https://www.ghosthack.de/free_sample_packs/).
