# Connecting Gmail (operator guide)

Do this once per deployment. Nothing here has been verified against the live Google console from this environment; check each screen against Google's current documentation.

## 1. Google Cloud project
1. Create a project, enable the **Gmail API**.
2. OAuth consent screen: user type External (a personal Gmail account), app name, your email as support contact.
3. Scopes: add only `https://www.googleapis.com/auth/gmail.readonly`. Google classes it as restricted. For one person's own mailbox the app can stay in **Testing** status with you as a test user; public use would need Google's verification and is out of scope.
4. Credentials: create an OAuth client, type Web application. Authorized redirect URI: `https://<your-api-host>/api/v1/integrations/oauth/callback` (it must match `GOOGLE_OAUTH_REDIRECT_URI` exactly).

## 2. Server settings (environment)
| Variable | Purpose |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | from the OAuth client |
| `GOOGLE_OAUTH_REDIRECT_URI` | the redirect URI above |
| `OAUTH_POST_CONNECT_URL` | optional; where the browser is sent after connecting (`?status=connected` or `?status=error&reason=...`) |
| `FIELD_ENCRYPTION_KEYS` | required in production: `id:key[,id:key]`, generate a key with `python -c "from apps.common.secretbox import generate_key; print(generate_key())"` |

Back the keys up separately from the database. Without them stored tokens and mail subjects cannot be read and the owner must reconnect.

## 3. Rotating the encryption key
1. Generate a new key and put it first: `FIELD_ENCRYPTION_KEYS=k2:<new>,k1:<old>`; deploy. New values use `k2`; old values still open.
2. Re-seal stored values (a management command for this is not written yet; until then reconnecting the integration re-seals the tokens and the next full sync re-seals subjects).
3. Remove `k1` only after nothing is sealed with it.

## 4. Testing-status caveat
While the consent screen is in Testing, Google has been reported to expire refresh tokens after 7 days. Google's own page says this limit applies to apps in Testing status; confirm in your console. When it happens the integration shows `reauth_required`, the dashboard card says "reconnect needed", and connecting again fixes it. Moving the consent screen to production removes the limit but triggers verification for the restricted scope.

## 5. Verify end to end (first time)
Connect from a client, confirm the integration shows `connected` and the dashboard email card gets counts after the first sync. The Gmail and OAuth HTTP clients have only been tested against a stub; this first real connection is their live test.

## 6. Connecting Google Calendar (Phase 7)
1. In the same Cloud project, enable the **Google Calendar API**.
2. On the consent screen add the scope `https://www.googleapis.com/auth/calendar.events` (this is the only calendar permission requested; it reads and changes events but not calendar settings or sharing).
3. Connect from a client with `POST /integrations/connect {provider: google, kind: calendar}`. Mail and calendar are separate connections, each asking only for its own permission.
4. Grant the agent permissions you want in agent settings: `calendar.read` for questions about your schedule, `calendar.write` only if you want it to create, move or cancel your own events (each one still waits for your approval).
The Calendar client has only been tested against a stub; the first real connection is its live test.
