# Add-on research: search free sample sources from inside the app

Status: **shelved** (November 2026). Research only; nothing here is built.

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

## Open questions to confirm before building

- The exact syntax for the license filter (`license:"Creative Commons 0"` is
  inferred from the documented filter grammar, not shown verbatim in the docs).
- Whether Freesound's terms permit the planned caching of preview MP3s
  (proposal: no caching, stream only).

Sources: [Freesound APIv2 overview](https://freesound.org/docs/api/overview.html),
[Freesound APIv2 resources](https://freesound.org/docs/api/resources_apiv2.html),
[Looperman terms](https://www.looperman.com/help/terms),
[Openverse issue on Internet Archive `licenseurl`](https://github.com/WordPress/openverse/issues/1777),
[Splice sounds](https://splice.com/sounds), [Ghosthack free packs](https://www.ghosthack.de/free_sample_packs/).
