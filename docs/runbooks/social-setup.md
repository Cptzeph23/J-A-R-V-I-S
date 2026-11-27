# Runbook: Facebook Page publishing

STATUS: written from Meta's documentation; the adapter has not been run against the live service.

## 1. Create the Meta app
1. At developers.facebook.com create an app (type Business). Keep it in **Development** mode; add yourself as an administrator.
2. Add the **Facebook Login** product. Valid OAuth redirect URI: `https://<your-domain>/api/v1/social/oauth/callback`.
3. Request only: `pages_show_list`, `pages_manage_posts`, `pages_read_engagement`. In development mode, these work for people with a role on the app and for Pages they administer. Going live for other people would need App Review and business verification, which this product does not need.
4. Confirm the Graph API version in the app dashboard and set `META_GRAPH_VERSION` if it differs from the default.

## 2. Configure the server
```
META_APP_ID=...
META_APP_SECRET=...            # secret; never commit
META_REDIRECT_URI=https://<your-domain>/api/v1/social/oauth/callback
SOCIAL_POST_CONNECT_URL=https://<your-domain>/social
```

## 3. First real run (this is the live test of the adapter)
1. Create a throwaway test Page you administer.
2. In the web client open Social, connect the Page, create a draft, approve it, publish.
3. Confirm exactly one post appears on the Page and the post shows `published` with an id.
4. Press "Refresh now" under Engagement; figures appear with a fetch time.
5. Unknown-outcome drill: stop the network or block `graph.facebook.com` while publishing; the post must show `uncertain`, and "Check my Page" must resolve it either way. Never publish again while it is `uncertain`.
6. Disconnect, then remove the app under Facebook Settings, Business Integrations.

## 4. Operating notes
- `uncertain` posts raise a high priority notification. Look at the Page first, then use "Check my Page" or say what you saw.
- A rejected Page token (password change, app removed) marks the account `reauth_required` and notifies; reconnect from the Social screen.
- Jobs: `social.recover` (every minute), `social.reconcile` (5 minutes), `social.refresh_metrics` (6 hours).
- Audit actions: `social.post_created|approved|publish_requested|publish_started|published|failed|uncertain|resolved`, never containing post text.
