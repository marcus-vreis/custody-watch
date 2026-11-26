# Security policy

## Supported versions

Only the `main` branch is supported. The project is a pre-release (`0.0.1`);
there are no maintained release branches and no backported fixes.

## Reporting a vulnerability

Use GitHub private vulnerability reporting for this repository: the
**Security** tab, then **Report a vulnerability**
(<https://github.com/marcus-vreis/custody-watch/security/advisories/new>).
Please do not open a public issue for an unfixed vulnerability.

Include the affected file or route, the commit you tested, and steps to
reproduce. This is a single-maintainer research project: there is no response
time commitment, no bug bounty and no formal disclosure process.

## Threat model of the HTTP servers

This describes what the code does today, not a hardening guarantee.

**Live operator screen** (`scripts/serve.py`, `src/custody_watch/servidor.py`):

- Binds to `127.0.0.1` by default (`HOST_PADRAO`). Listening elsewhere needs an
  explicit `--host`.
- Refuses to start on a non-loopback address without TLS: `--cert` and `--key`
  are required. Only `localhost` and loopback IP literals count as local; any
  other name is treated as network, without resolving it. TLS 1.2 is the
  minimum version, and the TLS handshake has a 10 s deadline.
- Validates the `Host` header (DNS-rebinding defence) and answers `421` for
  names not in the accepted set: loopback names, the listen address and any
  `--allowed-host NAME` given (repeatable). A wildcard listen address also
  accepts IP literals.
- **Optional access token, required off loopback.** The token is read only
  from the environment variable `CUSTODY_WATCH_TOKEN`, never from a
  command-line argument (visible in process lists and shell history) or a
  versioned file. It must be 32 to 256 visible ASCII characters with at least
  8 distinct ones; a weak, empty or oversized value refuses startup with a
  message that does not echo it. Starting on a non-loopback address without
  the variable is refused, after the TLS check.
- With a token, every route except `GET /healthz` requires either
  `Authorization: Bearer <token>` or a session cookie obtained with it. The
  Bearer value is compared with `hmac.compare_digest` on bytes; a repeated
  `Authorization` header, another scheme or a header longer than
  `Bearer ` + 256 characters is rejected without comparison. Failures answer
  `401` with `WWW-Authenticate: Bearer`, after draining a small body (as for
  other refusals), and never echo the token in a response or on the terminal;
  the token is not written to the journal. The `Host` and `Origin` checks run
  first, so a foreign `Host` is still `421`.
- Browsers cannot add a Bearer header to `<img src=/video>`, so a
  `GET /` without credentials answers `401` with a minimal form. It sends the
  token once, in the `Authorization` header of `POST /sessao`, which returns
  `204` and a random session id (never the token) in
  `cw_sessao=...; Path=/; Max-Age=43200; HttpOnly; SameSite=Strict`, plus
  `Secure` under TLS. Sessions live in memory, expire after 12 h, are capped
  at 64 (oldest dropped) and vanish on restart; the cookie is checked against
  each live session in constant time. `POST /sessao` accepts only Bearer, so a
  cookie cannot renew itself. The token is never accepted in a query string.
  There is no logout route and no per-user identity: everyone with the token
  shares the same rights, and there is no rate limit on failed attempts.
- Without the variable, on loopback, there is **no authentication**: anyone
  who can reach the port on this machine can watch the video, read `/estado`
  and send the POST routes (`/ancora`, `/dono`). TLS encrypts traffic; it does
  not identify clients (no client certificates are requested).
- Browser-origin checks (not authentication): a request carrying an `Origin`
  header is answered `403` unless it has the same scheme, host and port as the
  accepted `Host`, and a repeated `Origin` is also `403`. The check runs on every
  method, before routing. A request without `Origin` (a client outside a
  browser) passes it. No CORS headers (`Access-Control-*`) are ever sent, so
  other origins cannot read responses cross-origin.
- POST routes answer `415` unless `Content-Type` is `application/json`
  (parameters such as `charset` are ignored), which forces a browser
  preflight on cross-origin requests. Bodies are limited to 4096 bytes and must
  be a JSON object (`400` otherwise); `NaN`, `Infinity` and floats that
  overflow to infinity are rejected (`400`), as is JSON nested deeper than the
  parser allows. Click coordinates must be fractions in [0, 1]. Every socket
  read or write has a 10 s deadline; a body that does not arrive in time
  gets `408`. Video is capped at 15 frames per second per tab. Beyond these,
  no request-rate limit exists.
- `GET /healthz` returns `200` with the fixed body `{"status":"ok"}` and
  `Cache-Control: no-store`. It reports only that the process answers: no
  video, people, bags, configuration or source state. It passes the same `Host`
  and `Origin` checks as every other route (`421` for a foreign `Host`), needs
  no token, and has no write form.

**Multicamera replay laboratory** (`scripts/serve_multicamera.py`,
`src/custody_watch/multicamera_server.py`):

- Always binds to `127.0.0.1`; only `--port` is configurable. No TLS and no
  authentication, because it is meant for the local machine only.
- Validates the `Host` header like the live screen (`421`, connection closed,
  no content leaked): only `localhost`, `127.0.0.1`, `[::1]` and any
  `--allowed-host NAME` on its own port are accepted. This blocks DNS
  rebinding; it is not authentication.
- Serves fixed assets and read-only GET/HEAD routes; query parameters must match
  an exact expected set (`400` otherwise). POST, PUT, PATCH and DELETE are
  refused with `501` and are never read beyond a small drained body. It does no
  `Origin` or CORS handling; the `Host` check is its only browser-facing
  defence. `GET /healthz` returns `200` with `{"status":"ok"}` and no query
  string, exposing nothing else. Responses carry `nosniff`, `no-referrer` and a
  `default-src 'self'` Content-Security-Policy.

Out of scope: the security of the machine running the software, of the video
sources, and of any proxy you place in front of it. Dependencies are locked in
`uv.lock` but have not been audited for this document.
