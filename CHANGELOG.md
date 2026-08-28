# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.2] - 2026-08-27

Fixes from a review of the Media-Hoarder work. Nothing here changes what cullr
does; it changes what it does when something is wrong.

### Fixed

* A Media-Hoarder delete that removed no files reported success to the browser.
  If the source became unreachable between loading the page and confirming, the
  server had no path for any marked item, deleted nothing, and answered `ok`,
  so the UI showed "deleted, N GB reclaimed" and cleared the marks while the
  audit log recorded a failure. Deleting nothing is now an explicit error.
* A damaged or older Media-Hoarder database no longer takes the whole library
  down. Its queries raised a bare `sqlite3.Error` that escaped to `/api/data` as
  a 500, blanking the grid including working Radarr and Sonarr entries, which
  could hit a pure-Radarr user who merely had a `media-hoarder.db` on disk.
  Every query path now reports through the source's own error type.
* The delete guard compares resolved paths. It matched raw strings, so a `..`
  segment inside a recorded path could pass the source-path check and then
  unlink something outside it.
* The read-only database URI is percent-encoded. SQLite ends a URI filename at
  the first `#` or `?`, so a user directory such as `bob#1` silently dropped
  `mode=ro` and opened a different path read-write.
* Season counting and the series folder split on either path separator. On a
  macOS or Linux install a three-season show reported one season per episode.
* Two hosts exporting the same share name get separate drive chips. Both
  collapsed into one label that measured only the first host's free space, so
  the reclaim preview was wrong for every item on the second.
* A drive filter is cleared when it no longer matches the visible library.
  Switching source with a drive chip active left an invisible filter applied
  and an empty grid with no chip left to switch off.
* A saved preset naming a source this server does not have falls back to an
  available one instead of leaving the picker blank and the grid empty.
* The confirmation no longer offers the re-download toggle's explanation for a
  selection that is entirely Media-Hoarder, where it does not apply.

## [1.1.1] - 2026-08-27

### Changed

* The library selector is now two controls: a source (Radarr / Sonarr, or
  Media-Hoarder) and a media type (movies or TV series). Previously all four
  combinations shared one dropdown, which made the source implicit. The source
  picker is hidden when only one source is configured, and the choice persists
  and is stored with presets.
* Drive chips show only the drives the selected source actually uses. A drive
  belonging to another source could never be changed by what you marked.

## [1.1.0] - 2026-08-27

### Added

* Media-Hoarder as a third library source. cullr reads its SQLite database
  read-only and shows the library on the same poster wall, as
  *Media-Hoarder movies* and *Media-Hoarder series*. The database is found
  automatically in the usual install locations on Windows, macOS and Linux, or
  pointed at with `--mh-db` / `CULLR_MH_DB`, and ignored with
  `--no-mediahoarder`.
* `--mh-allow-delete`, off by default. Media-Hoarder has no API, so deleting one
  of its files means removing it from disk directly. Without the flag the source
  is browsable and every delete is refused. `--read-only` and `--dry-run` still
  take precedence, and the confirmation dialog counts Media-Hoarder items and
  their files separately.
* Drive chips understand UNC shares, so a library on a NAS reports real free
  space per share.

### Changed

* `--check` reports Media-Hoarder alongside Radarr and Sonarr.
* An item can say it has no artwork, so a library whose posters were never
  cached locally renders placeholders instead of one failed image request per
  card.

### Fixed

* Free space is now resolved per drive rather than by assuming every root in the
  library has the same shape. A mix of drive letters and UNC shares previously
  left the lettered drives unmeasured.

## [1.0.0] - 2026-08-27

First public release.

### Added

* Poster wall over the whole Radarr + Sonarr library, sorted biggest-first.
* Hover synopsis: plot, studio, genres, rating, certificate, runtime, codec,
  HDR type, exact size and full path.
* Filters: search, studio, genre, quality tier, year range, size range, drive,
  monitored state. All stackable.
* Grouping by studio, genre, quality, drive or decade, with a per-group total
  footprint and a *mark all* button.
* Per-drive chips showing live free space and projected reclaim, turning red
  under 50 GB.
* Batch delete through the *arr API with a full confirmation list and a live
  log, optionally adding an import exclusion.
* Downsizing (Radarr only): search indexers for a smaller release of a movie
  you already have, with a size cap, a savings column, and an automatic quality
  profile switch so the downgrade is not rejected at cutoff.
* Audit log of every deletion and downsize to `cullr-deletions.jsonl`.
* Ten sort orders, including *most oversized for its quality tier* and
  *most GB per hour*.
* Keyboard-driven triage: `/`, `Space`, arrows, `Enter`, `a`/`i`/`c`, `g`, `r`,
  `t`, `?`.
* Configuration by flag, environment variable, config file, or auto-discovery
  of a local Radarr/Sonarr `config.xml` on Windows, macOS, Linux and
  linuxserver.io containers.
* Safety switches: `--read-only`, `--dry-run`, `--no-audit`, `--check`.

[Unreleased]: https://github.com/LevonFrench/cullr/compare/v1.1.2...HEAD
[1.1.2]: https://github.com/LevonFrench/cullr/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/LevonFrench/cullr/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/LevonFrench/cullr/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/LevonFrench/cullr/releases/tag/v1.0.0
