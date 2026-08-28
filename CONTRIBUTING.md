# Contributing to cullr

Thanks for looking. cullr is small on purpose, and the bar for new code is
"does this help someone delete the right files faster".

## Ground rules

* **Standard library only.** No runtime dependencies, ever. No build step, no
  bundler, no framework, no CDN links. If a change needs a package, it is the
  wrong change.
* **Python 3.9+.** Do not use syntax newer than 3.9 (`match`, `X | Y` at
  runtime, `tomllib`).
* **Vanilla front end.** `cullr/static/` is plain HTML, CSS and JS served
  as-is. Keep it that way.
* **Deletion is destructive.** Any change that touches the delete path must
  keep every existing safety layer intact: confirmation, `--read-only`,
  `--dry-run`, and the audit log.

## Setting up

```bash
git clone https://github.com/LevonFrench/cullr && cd cullr
python -m cullr --check      # verify it can reach Radarr/Sonarr
python -m cullr --open       # run it
```

There is nothing to install. `pip install -e .` is optional and only gives you
a `cullr` command.

You do not need a real Radarr/Sonarr to work on the UI. Point cullr at a test
instance, or use `--dry-run` so nothing is ever deleted.

## Before you open a pull request

```bash
python -m compileall -q cullr        # must be silent
python -m cullr --help               # must print usage
python -m cullr --version            # must print a version
node --check cullr/static/app.js     # if you touched the JS
```

Then run it against a real library with `--dry-run` and click through the
feature you changed.

## Style

Match the file you are editing. Do not reformat, do not reorder imports, and do
not refactor code unrelated to your change. A diff that touches only what it
needs to is much easier to review, and much more likely to get merged.

## Reporting a bug

Include:

* what you ran (the exact command and flags)
* Radarr and/or Sonarr version
* your OS and Python version
* what you expected and what happened
* the server console output, and the browser console if it is a UI bug

**Never paste your API key.** Redact it from URLs and logs before posting.

## Security

Do not open a public issue for a security problem. See `SECURITY.md`.
