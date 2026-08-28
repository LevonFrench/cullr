# Security Policy

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private reporting:
**Security → Report a vulnerability** on this repository.

Include what you found, how to reproduce it, and what an attacker could do with
it. You will get an acknowledgement within a few days.

## Threat model

cullr is a single-user local tool. It assumes:

* it runs on the same trust boundary as your Radarr/Sonarr API keys
* it binds to `127.0.0.1` by default and has **no authentication**
* the only thing it protects is you from your own mis-clicks

Because of this, the following are **not** vulnerabilities:

* no login on the web UI — bind it to loopback, or put a reverse proxy with
  auth in front of it
* the API key being readable from your own config file or `config.xml`

The following **are** vulnerabilities and are in scope:

* the API key leaking anywhere it should not go — into a URL that gets logged,
  an error message, the served HTML, or a request to a host that is not your
  configured Radarr/Sonarr
* path traversal or arbitrary file read through any HTTP route, including the
  static file handler and the poster proxy
* server-side request forgery — getting cullr to fetch a host you did not
  configure
* deleting anything without the confirmation step, or a way around
  `--read-only` or `--dry-run`
* stored XSS through library metadata (a title or plot from your *arr rendering
  as HTML)

## If you bind to a routable address

`--host 0.0.0.0` exposes an unauthenticated delete API to your network. Do not
do this unless a reverse proxy in front of it is doing authentication.
