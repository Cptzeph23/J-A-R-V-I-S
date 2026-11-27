# ADR-017: Social publishing (Facebook Page)

Status: proposed (Phase 13). Needs your confirmation of the platform choice.

## Context
The brief wants social media managed by the assistant. Publishing is the first action that
leaves the system and is visible to other people, so it is the riskiest tool in the product
(threat T05, T10, T12, T13).

## Platform choice (one platform in v1)
| Option | Finding | Verdict |
|---|---|---|
| Facebook Page, Graph API | Page posting needs a Meta app and a Page you administer. Posting to Pages you manage while the app is in development mode is reported to work without App Review (secondary sources; not verified against Meta's own current text) | chosen |
| X | Paid API, per-post cost, own account only | not chosen: recurring cost, no business Page concept |
| LinkedIn | Posting as an organization requires approval of a Community Management app by LinkedIn, which expects a registered organization | not chosen: approval dependency |

The choice assumes you have, or will create, a Facebook Page. If you want a personal profile, Meta does not offer a way to publish to it through the API; that would need another platform.

## Decisions
1. **Publishing is an exact-text approval.** A post is `draft`, then `approved` (owner taps with the hash of Page + text + link echoed back, valid 24 h), then `queued`, `publishing`, and finally `published`, `failed` or `uncertain`. Editing returns it to draft and clears the approval. A database CHECK refuses a queued, publishing or published row whose approved hash is not its content hash.
2. **The assistant gets one outbound tool,** `social_publish_post`, added to an explicit allow-list in the registry (`ALLOWED_OUTBOUND`) with extra invariants (mutating, sensitive so voice cannot start it, needs `social.write`, which is off by default). Each call needs its own approval showing the exact text, the Page and the word PUBLIC. The tool only queues; a background job sends.
3. **No duplicate posts.** The Graph API has no idempotency key for creating a post, so protection is ours: only `queued` is ever sent, and it leaves `queued` (taking a 90 s lease, attempt counter incremented) before the request is made; the assistant step key is unique per owner; repeating a request returns the existing post.
4. **An unknown outcome is never "failed".** A timeout, lost reply, 5xx, transient or unparseable error becomes `uncertain`: nothing retries it, publishing again is refused, and a worker that dies mid-send leaves `uncertain`. Resolution is by `reconcile` (read the Page's recent posts and match the text, never claiming a post already matched to another) or by the owner's own word. If the post is absent for 10 minutes it becomes `failed` ("not found on Page"), after which a new approval allows a retry. Risk: a post that appears after those 10 minutes would be duplicated if the owner retries.
5. **Only a parsed refusal is a definite failure** (4xx with a non-transient error object). Rate limits and token errors are definite and say so.
6. **Permissions requested:** `pages_show_list`, `pages_manage_posts`, `pages_read_engagement`, nothing else (a test pins the list). The login's Page token is sealed with the field key, never returned by the API, and deleted on disconnect. Only Pages the login can create content for are offered.
7. **Cap:** at most 10 posts per rolling 24 h per owner, so a bug cannot flood the Page.
8. **Analytics:** reactions, comments and shares per post, with the time fetched; missing is shown as missing. Reach and impressions are not collected (Meta's metric names have changed repeatedly and I have not verified the current ones).
9. **Text rules:** 1 to 2000 characters, no control characters; optional https link without credentials.

## Consequences
- Disconnecting deletes our copy of the token and cancels approved or queued posts. It cannot revoke the token at Meta (that needs the user token, which is not kept): the owner removes the app in Facebook's Business Integrations settings.
- The adapter is written to Meta's documented request shapes and has never run against the live service. First real run: connect a test Page, publish, reconcile (see `docs/runbooks/social-setup.md`).
- Comments, messages and scheduling are out of scope; no inbound social content is read, so T13 is limited to post text the assistant drafts from other untrusted input, which the approval marks as tainted.
