# Push notifications (FCM) — setup

The server can push every new in-app notification to the Digital NBTE app through
**Firebase Cloud Messaging, HTTP v1 API** (`notifications/push.py`). It is **off** until a
Firebase service account is configured; until then nothing is sent and nothing external is
contacted. The app's background polling (`GET api/v1/notifications/feed/`) works with or without it.

## What is sent

* Trigger: every notification created through `notifications.services.notify` /
  `notify_many` (the only creation paths), after the database transaction commits, via the
  Celery task `notifications.tasks.send_push_task` (runs inline in development,
  `CELERY_TASK_ALWAYS_EAGER`).
* To: every **active app device** of the recipient — `accounts.UserSession` rows of kind
  `app`, not revoked, with a `push_token` (the app sends it with `POST api/v1/me/push-token/`
  or `device.push_token` at sign-in).
* Respecting the recipient's notification preferences (`me/preferences/`, website
  "Notification settings"): the kind's switch, quiet hours (urgent = level `error` breaks
  through when "allow urgent" is on), and `show_previews = never` (generic body).
* Payload: `notification {title, body}`, `data {notification_id, category, kind, target}`
  (`target` = the JSON string of the notification's app target, see `api/docs/notifications.md`),
  Android high priority.
* Tokens FCM reports as `UNREGISTERED` (or an invalid registration token) are removed from
  the device row automatically.

No new Python packages: `requests`, `PyJWT` and `cryptography` (already installed) sign the
service-account JWT and call Google's OAuth and FCM endpoints. The OAuth access token is
cached (Django cache) for just under an hour.

## Server setup

1. **Create a Firebase project** at <https://console.firebase.google.com> (Add project →
   e.g. "Digital NBTE"). Google Analytics is not needed.
2. **Turn on Cloud Messaging (V1).** Project settings → *Cloud Messaging* → make sure
   "Firebase Cloud Messaging API (V1)" is *Enabled* (enable it in Google Cloud console if it
   says disabled). The legacy server key is not used.
3. **Download a service account key.** Project settings → *Service accounts* →
   *Firebase Admin SDK* → **Generate new private key**. This downloads a JSON file
   (`<project>-firebase-adminsdk-xxxx.json`) containing `project_id`, `client_email` and
   `private_key`. Treat it like a password.
4. **Put it on the server** outside the web root and the repository, readable only by the
   app user, e.g.
   ```bash
   sudo install -m 600 -o nbte -g nbte digital-nbte-fcm.json /etc/nbte/fcm-service-account.json
   ```
5. **Set the environment variable** for the web process **and** the Celery worker:
   ```bash
   FCM_SERVICE_ACCOUNT_FILE=/etc/nbte/fcm-service-account.json
   ```
   (systemd: `Environment=` in both units, or the shared env file; then restart both.)
   In development you can export it before `python manage.py runserver`.
6. **Check:** sign in on the app with a device that registered a token, then create a
   notification (e.g. assign a task to that user). `python manage.py shell`:
   ```python
   from notifications import push; push.enabled()      # True
   from notifications.services import notify
   from accounts.models import User
   notify(User.objects.get(email="you@nbte.gov.ng"), title="Test push", body="Hello from NBTE")
   ```
   Failures are logged by the `notifications.push` logger (auth errors, refused sends).

To switch push off again, unset `FCM_SERVICE_ACCOUNT_FILE` and restart.

## App side (a later step)

Not done yet in the Flutter app; needed before phones receive pushes:

1. In the same Firebase project, **add an Android app** with the app's package name (and an
   iOS app with its bundle id, plus an APNs key under Cloud Messaging for iOS).
2. Download **`google-services.json`** into `mobile/android/app/` (and
   `GoogleService-Info.plist` into `mobile/ios/Runner/` for iOS); add the Firebase
   Gradle plugin as the FlutterFire docs describe (`flutterfire configure` does all of this).
3. Add `firebase_core` + `firebase_messaging`, request notification permission (Android 13+),
   get the token with `FirebaseMessaging.instance.getToken()` and send it to
   `POST api/v1/me/push-token/`; send it again on `onTokenRefresh`; send `null` when the user
   turns notifications off or signs out.
4. On tap, read `data.target` (JSON) and route exactly like a notification in the list;
   use `data.notification_id` to avoid showing the same item from the background feed twice.
