# ADR 0016: Google re-authorization from the control app

Date: 2026-10-20. Status: Accepted for Phase 31b (decisions by Carter, 2026-10-15
and 2026-10-20). Related: ADR 0015, Phase 16 (token reminder), Phase 30 (daily
"expired" notice).

## Context

The OAuth consent screen stays in **Testing**, so Google expires the refresh
token 7 days after consent (`invalid_grant`). On 2026-10-15 the token had
already expired, and the 07:00 recap failed with `RefreshError`. Until now the
only fix was a private consent helper script on the MacBook plus copying `.env`
to the Mini. Carter decided that re-authorization happens through the control
app, by either parent, usually from Safari on an iPhone, and that the first
real re-authorization after the expiry is the acceptance test of Phase 31b.

Constraints: keep the existing **Desktop** OAuth client (no Google Cloud
change), scope `calendar.events` only. Google retired the out-of-band
(copy-a-code) flow, and the device flow does not allow Calendar scopes.

## Decision

1. **Loopback redirect plus paste.** "Start" (password re-entry if the last
   entry is older than 15 min) creates an attempt in memory, bound to the
   session, for 10 minutes: a random `state` and a PKCE verifier. The page shows
   "Open Google" (new tab) with a consent URL for the Desktop client:
   `redirect_uri=http://127.0.0.1:53682/`, `response_type=code`,
   `scope=https://www.googleapis.com/auth/calendar.events`,
   `access_type=offline`, `prompt=consent`, `code_challenge` (S256) and `state`.
   After consent Google sends the browser to the loopback address; nothing
   listens there, so Safari shows "Cannot connect". The parent copies that
   address and pastes it into the app.
2. **Finish.** The app parses the pasted address, then checks in this order:
   attempt exists, attempt age ≤ 10 min, `state` matches (constant time), no
   `error` parameter, a `code` exists, and the granted `scope` (when present)
   includes `calendar.events`. It exchanges the code server-side (POST to
   `https://oauth2.googleapis.com/token` with client ID, client secret, code,
   redirect URI and verifier) through an injectable HTTP call. The response
   must contain a refresh token with only token-safe characters and the
   Calendar scope. Each failure has its own STE message and leaves `.env`
   unchanged; `state`/expiry/denied/exchange/scope failures end the attempt.
3. **Write.** `GOOGLE_REFRESH_TOKEN` and `GOOGLE_REFRESH_TOKEN_ISSUED_AT` (HKT
   ISO, seconds) are written in one atomic, line-preserving `.env` update with
   the ADR 0015 backup (mode 0600, last 3). Then the app probes the new token
   with `probe_google_client`, shows the new expiry date (issue date +
   `GOOGLE_TOKEN_TTL_DAYS`, default 7), audits the action without values, and
   posts one notice to the system-status channel.
4. **Restart prompt.** The listener loads `.env` only at start, so the success
   screen asks an admin to restart it (button for admin, "ask the administrator"
   for a member). The 07:00 and 10:00 jobs and the checker read the new values
   at their next run without a restart.
5. **Secrets.** The code, verifier, access token and refresh token are never
   rendered, logged, audited or posted. `control_oauth_failed` logs the failure
   category only (plus Google's short error code such as `invalid_grant`).
6. **One implementation.** `control/oauth_google.py` is the only copy of the
   PKCE, consent-URL and exchange logic in the repository. The private helper
   script is not in this repository (it lives outside Git on the MacBook) and is
   not run; any future helper should import these functions.

## Why not the alternatives

- **Web OAuth client with the app as redirect:** one tap less, but it needs a
  Google Cloud change and an HTTPS origin that Google accepts; the LAN HTTP
  origin does not qualify. Possible later with Tailscale HTTPS.
- **Publish the consent screen (Production):** removes the 7-day expiry but
  needs Google verification for a sensitive scope. Carter keeps Testing.
- **Run the helper on the Mini:** needs Terminal and a desktop browser on the
  Mini; it does not solve "fix it from the phone".

## Consequences

- Weekly task for either parent: 2 taps to start (banner "Re-authorize", then
  "Open Google"), Google consent, 1 paste, "Save access". The Overview banner
  warns from 2 days before expiry; the status channel posts daily after expiry.
- A member's re-authorization reaches Slack replies only after an admin restart
  (roles stay as approved; flagged for Carter).
- If Google ever stops accepting loopback redirects for Desktop clients, this
  flow breaks visibly at "Google did not accept this address"; the fallback is
  the helper on a desktop browser.
- Not verified offline: real Google consent screens on iOS Safari, the exact
  "Cannot connect" wording, and Google's acceptance of the loopback redirect
  for this client. That is the Phase 31b acceptance test on the Mini.
