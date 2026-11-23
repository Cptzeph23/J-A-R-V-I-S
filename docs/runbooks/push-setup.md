# Push notifications setup (FCM and Gmail push)

## 1. Firebase Cloud Messaging
1. Create a Firebase project and enable Cloud Messaging (HTTP v1).
2. Create a service account with the "Firebase Cloud Messaging API Admin" role and download its JSON key.
3. Set `FCM_PROJECT_ID` and `FCM_SERVICE_ACCOUNT_JSON` (the JSON as one environment value or a mounted secret). Without them the provider reports not configured and deliveries wait.
4. The app registers its token with `POST /devices/{id}/push-token`.

## 2. Gmail push (optional, after hosting is chosen)
1. Create a Pub/Sub topic and grant `gmail-api-push@system.gserviceaccount.com` publish rights.
2. Create a push subscription to `https://<host>/api/v1/webhooks/google-pubsub?token=<WEBHOOK_SHARED_SECRET>`.
3. Set `WEBHOOK_SHARED_SECRET` to a long random value. Registering `users.watch` is not implemented yet; polling and the reconcile job cover mail until then.

## 3. Verify
Send yourself an important email, wait one sync interval, and check `GET /notifications` shows it with delivery status.
