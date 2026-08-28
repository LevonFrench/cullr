# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-08-27

First public release.

### Added

* Poster wall over the whole Radarr + Sonarr library, sorted biggest-first.
* Hover synopsis: plot, studio, genres, rating, certificate, runtime, codec,
  HDR type, exact size and full path.
* Filters: search, studio, genre, quality tier, year range, size range, drive,
  monitored state — stackable.
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

[Unreleased]: https://github.com/LevonFrench/cullr/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/LevonFrench/cullr/releases/tag/v1.0.0
