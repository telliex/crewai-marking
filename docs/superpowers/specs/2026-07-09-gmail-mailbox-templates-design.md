# Apollo-style Gmail Mailbox + Reply Detection + Email Templates

## Context

The outreach app currently sends every email through the Resend API (`send/mailer.py` is the single chokepoint) and gets bounce/open/click signals from Resend webhooks. The user wants to replicate Apollo.io's mailbox model:

1. **Connect a Gmail account via OAuth** (admin page), send sequence emails *as that Gmail account* — better cold-outreach deliverability, replies land in the real inbox, follow-ups thread naturally.
2. **Reply detection (auto-stop)**: poll the connected Gmail inbox, match senders against leads, mark `replied` and stop their sequence. Works regardless of send channel (replies always land in the reply-to mailbox).
3. **Email Template library** (Apollo "New Template" page): standalone templates CRUD with name/subject/body, live preview against an example contact, "send test email to me" (delivered to the mailbox's own address), and one-click insert into a campaign's sequence steps.

User decisions (final): Mailbox abstraction layer (Resend stays as the implicit default channel — `campaign.mailbox_id IS NULL` ⇒ exact current behavior); support both Workspace (Internal app, long-lived tokens) and personal @gmail.com (Testing mode, refresh token dies every 7 days → loud "needs reconnect" UI); standalone template library; test send goes to the mailbox's own email.

## Design decisions

- **No new dependencies.** Google OAuth + Gmail REST are 6 trivial httpx calls (token exchange/refresh, send, list, get, profile, revoke). `google-api-python-client` can't be mocked with respx (the repo's test idiom); skip it.
- **Tokens in plaintext DB columns** — single-operator self-hosted tool; DB already holds all lead PII and `.env` holds the Resend key in plaintext. Add a "why" comment; Fernet encryption is a noted future hardening.
- **Resend stays implicit** — no synthetic mailbox row. NULL `mailbox_id` = today's behavior, matching the existing `sender_identity` env-fallback philosophy.
- **`send_outreach_email` signature and `SendResult` contract unchanged** — engine/sequencer/tests untouched. Dispatch happens inside the mailer; DB session obtained via `object_session(lead)`.
- **Threading**: `lead.thread_ref` stores Gmail `threadId` (set by the mailer on first send); new column `lead.last_message_id` stores the RFC-822 Message-ID we generate ourselves (`email.utils.make_msgid(domain=mailbox email domain)`) so follow-ups can set `In-Reply-To`/`References` without an extra API call. Mailer mutates the lead; engine commits it atomically with the `sent` Event (prominent why-comment required).
- **Identity interaction**: Gmail mailbox forces `from_email = mailbox.email` (Gmail rewrites arbitrary From anyway); `reply_to` defaults to mailbox email; explicit campaign `sender_identity.from_name` still wins over `mailbox.display_name` over env.
- **OAuth state (CSRF)**: no server sessions exist — self-validating HMAC state token (same pattern as `compliance.make_unsub_token`, keyed on `outreach_unsubscribe_secret`, 10-min validity).
- **Consent URL**: `access_type=offline&prompt=consent` (always — re-mints refresh_token for previously-consented users), scopes `gmail.send` + `gmail.readonly`, redirect = `app_base_url + /oauth/google/callback`. Callback sits under the admin Basic-auth router (browser replays Basic creds after Google redirects back).
- **Refresh failure (`invalid_grant`** — includes the personal-Gmail 7-day expiry) → `mailbox.status = "needs_reconnect"` + `last_error`; sends through it fail fast with `SendResult(ok=False, error="mailbox needs reconnect")`, **zero network**.
- **Reply polling**: second APScheduler job in the existing `scheduler.py` cron process (default 5 min) + `poll-replies` CLI command + per-mailbox "Check replies now" button. Query `messages.list q="in:inbox -from:me after:<last_poll_at − 10 min overlap>"` (first poll: `newer_than:2d`), then `messages.get?format=metadata` per hit; match From (lowercased, `email.utils.parseaddr`) against leads in `active|sending|completed|paused`, any campaign. On match: `status="replied"`, `replied_at`, `next_action_at=None`, `Event(type="reply", detail=<gmail msg id>)`. Idempotency: watermark advanced only on success + overlap-window dedupe via existing reply Event with same detail. Replies from a different address: out of scope (docstring note).
- **Templates**: `email_template` table (name/subject/body). Preview = server-side render with a hard-coded example contact through the same `_SafeDict` + compliance footer as `render_step`. Test send: page selects a mailbox (connected Gmail → recipient is `mailbox.email`; implicit Resend → recipient is `settings.outreach_from`). Sequence editor gets an "insert template" dropdown per step (templates serialized as JSON in the page; tiny vanilla JS copies subject/body into the step fields).

## New module layout

```
src/awkns_outreach/gmail/
    __init__.py
    oauth.py      # consent URL, HMAC state, exchange_code, refresh, revoke, NeedsReconnect
    api.py        # httpx wrappers: ensure_fresh_token, fetch_profile_email, send_raw,
                  # list_message_ids, get_message_metadata
    mime.py       # build_raw_message: stdlib EmailMessage, text+html alternative,
                  # Message-ID / List-Unsubscribe / In-Reply-To headers, base64url
    replies.py    # poll_mailbox_replies / poll_all_mailboxes -> PollSummary
src/awkns_outreach/web/routes/mailboxes.py
src/awkns_outreach/web/routes/templates_lib.py        # avoid clashing with Jinja "templates"
src/awkns_outreach/web/templates/mailboxes.html
src/awkns_outreach/web/templates/template_list.html
src/awkns_outreach/web/templates/template_edit.html   # new+edit, preview pane, test-send
src/awkns_outreach/db/migrations/versions/0004_mailboxes_templates.py
tests/test_gmail_oauth.py  test_gmail_send.py  test_gmail_replies.py
tests/test_mailboxes_web.py  test_templates_web.py
```

## Ordered tasks

1. **Config** — `config.py`: add `google_client_id` / `google_client_secret` (env `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`, default `""`). Redirect URI derives from `app_base_url`; no other env vars.

2. **Models + migration 0004** — `db/models.py`:
   - `Mailbox`: id, provider ("gmail"), email (unique), display_name, access_token, refresh_token, token_expiry, scopes, status (connected|needs_reconnect|disconnected), last_error, last_poll_at, connected_at, created_at, updated_at.
   - `Campaign.mailbox_id` FK (`ondelete="SET NULL"`, nullable) + relationship.
   - `Lead.last_message_id` (nullable string).
   - `EmailTemplate`: id, name, subject, body, created_at, updated_at.
   - `0004_mailboxes_templates.py` chained from `0003_campaign_description`; downgrade drops in reverse; no backfill.

3. **`gmail/oauth.py`** — pure functions + httpx against `oauth2.googleapis.com/token` and `/revoke`; `TokenBundle` dataclass; `NeedsReconnect` raised on `invalid_grant`.

4. **`gmail/mime.py` + `gmail/api.py`** — MIME via stdlib `EmailMessage` (set_content text, add_alternative html); `ensure_fresh_token` refreshes when expiry within 120 s, sets `needs_reconnect` + re-raises on failure; caller's session commit persists token updates.

5. **Mailer dispatch** — `send/mailer.py`: after the dry-run early return, branch: campaign's mailbox is a connected Gmail → `_send_via_gmail` (Message-ID generation, `threadId=lead.thread_ref` when step > 0, sets `lead.thread_ref`/`last_message_id` on success); `needs_reconnect` → fast `ok=False`, no network; otherwise existing Resend path untouched. Same broad try/except → `SendResult(ok=False)`.

6. **Reply polling** — `gmail/replies.py` per design; `scheduler.py` second job (`--poll-interval 5`); `cli.py` `poll-replies` command.

7. **Mailboxes web** — `web/routes/mailboxes.py` (admin-gated): `GET /mailboxes` (list + status badges + token expiry + last poll), `GET /mailboxes/connect` (302 to Google), `GET /oauth/google/callback` (verify state → exchange → `fetch_profile_email` → upsert row → 303), `POST /mailboxes/{id}/reconnect` (connect URL with `login_hint`), `POST /mailboxes/{id}/disconnect` (best-effort revoke, clear tokens), `POST /mailboxes/{id}/poll`. Register router in `web/app.py`; nav link in `base.html`. Callback errors: `access_denied` → message; missing refresh_token → instruct removing app access at myaccount.google.com/permissions and reconnecting.

8. **Campaign ↔ mailbox UI** — `campaign_edit.html` + `admin.py:save_campaign_edit`/`edit_campaign_form`: `<select name="mailbox_id">` with "Default (Resend)" empty option + connected Gmail mailboxes; warning badge on campaign.html when its mailbox is `needs_reconnect`.

9. **Template library** — `web/routes/templates_lib.py` (admin-gated): list / new / edit / delete; POST actions on the edit page: `save`, `preview` (re-render page with example-contact preview pane), `test_send` (render + send via selected mailbox to that mailbox's own email; Resend fallback → `settings.outreach_from`). Placeholder cheatsheet reused from sequence editor (`SEQUENCE_PLACEHOLDERS`). Sequence editor: "insert template" dropdown per step (vanilla JS, templates as JSON blob in page).

10. **Tests** (respx + TestClient, existing fixture patterns; dry-run tests assert **zero** respx calls):
    - oauth: consent URL params, state round-trip/expiry/tamper, exchange happy path, refresh `invalid_grant` → NeedsReconnect.
    - send: Gmail-mailbox campaign posts to Gmail not Resend; raw MIME contains List-Unsubscribe/-Post + both parts; step 0 no threadId, step 1 threadId + In-Reply-To; thread_ref/last_message_id persisted; needs_reconnect → fast fail, zero calls; expired token triggers refresh first.
    - replies: match → replied + Event; re-poll no duplicate Event; non-lead sender ignored; watermark advances.
    - mailboxes web: list renders statuses; connect 302; callback with valid state creates row (mock token+profile); bad state 400; disconnect clears tokens; campaign edit persists mailbox_id.
    - templates web: CRUD; preview renders placeholders with example contact; test_send posts to the right provider with recipient = mailbox email.

## Operator prerequisites (document in README)

Google Cloud Console: create project → OAuth consent screen (Workspace: **Internal**; personal Gmail: External + Testing, add self as test user — refresh token expires every 7 days) → OAuth Client ID (Web application) with redirect URI `{APP_BASE_URL}/oauth/google/callback` → set `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` in `.env`. Run `uv run alembic upgrade head`.

## Risks (flagged in PR)

1. A `needs_reconnect` mailbox burns lead error budget (3 errors → lead `failed`) since the engine isn't touched; mitigated by fast-fail + loud badges. Future: engine-level `summary.blocked` guard.
2. Personal-Gmail 7-day expiry is the *primary* path, not an edge case — test and surface it.
3. Mailer mutating `lead` for threading is subtle; why-comment required.
4. Poll watermark: `after:` is second-granular, ordering unguaranteed → 10-min overlap + Event dedupe, tested explicitly.
5. No bounce feedback on the Gmail path (Resend webhooks won't fire); mailer-daemon parsing out of scope.

## Execution mode

Per user instruction: implementation is dispatched to a **Sonnet subagent** (same as the campaign-archive feature). The main session (Opus) writes the spec commit, dispatches Sonnet with the task list above, then reviews the diff, fixes anything found, and runs the full runtime verification below itself. Work happens on a new branch `feat/gmail-mailbox` off `feat/campaign-archive` (which contains the uncommitted archive work — commit that first or branch from it as approved by user).

## Verification

1. `uv run pytest -q` — full suite green (new tests above).
2. Runtime: `uv run alembic upgrade head`; start uvicorn; with real `GOOGLE_CLIENT_ID/SECRET` walk the connect flow against a real Google account (or with respx-style fake endpoints via a staging base URL): connect → mailbox row appears with green badge → assign to a campaign → `run` dry-run shows normal preview → live send 1 email → confirm it appears in Gmail Sent and threads on step 2 → reply from another account → `poll-replies` marks the lead replied and dashboard Active count drops.
3. Template page: create template → preview shows example-contact substitution → "Send test email to me" arrives at the mailbox address.
4. Disconnect the mailbox → sends fail fast with "mailbox needs reconnect", no lead corruption.
