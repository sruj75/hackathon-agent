# Firebase Firestore Setup Guide

## Overview

This guide covers setting up Firebase Firestore for the Intentive backend.

## 1. Firebase Project Setup

1. Go to [Firebase Console](https://console.firebase.google.com/)
2. Select your project: `hackathon-intentive` (already exists)
3. Navigate to **Firestore Database** in the left sidebar

## 2. Create Firestore Database

1. Click "Create database"
2. Select **Start in production mode** (we'll configure rules next)
3. Choose a location (recommend: `us-central` for lowest latency to Render)
4. Click "Enable"

## 3. Configure Security Rules

Since we're not using authentication in v0 (single hardcoded user for TestFlight), we need to allow public read/write access:

1. Go to **Firestore Database** → **Rules**
2. Replace the default rules with:

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /{document=**} {
      allow read, write: if true;  // Public access for v0 testing
    }
  }
}
```

3. Click **Publish**

**Warning:** Firebase will show "Your security rules are not secure" - this is expected for v0. In v1, we'll add authentication and proper rules.

## 4. Create Service Account

For the backend to access Firestore, we need a service account:

1. Go to **Project Settings** (gear icon) → **Service Accounts**
2. Click **Generate new private key**
3. Download the JSON file
4. **For local development:**
   - Save as `agent/firebase-service-account.json`
   - This file is gitignored for security
   - Add to `.env`:
     ```
     FIREBASE_SERVICE_ACCOUNT_PATH=firebase-service-account.json
     ```

5. **For production (Render):**
   - Base64 encode the file:
     ```bash
     base64 -i firebase-service-account.json | tr -d '\n' > firebase-creds-base64.txt
     ```
   - Copy the contents of `firebase-creds-base64.txt`
   - Add to Render environment variables:
     ```
     FIREBASE_CREDENTIALS=<paste-base64-here>
     ```

## 5. Verify Setup

Test the connection locally:

```bash
cd agent
python3 -c "from firestore import initialize_firestore; db = initialize_firestore(); print('✅ Firestore connected!')"
```

Expected output: `✅ Firestore connected!`

## Collections Structure

The backend will automatically create these collections:

- **users/{user_id}** - User profiles and preferences
  - `wake_time`: string (HH:MM format)
  - `bedtime`: string (HH:MM format)
  - `timezone`: string
  - `health_anchors`: array of strings
  - `created_at`, `updated_at`: timestamps

- **push_tokens/{user_id}** - Expo push notification tokens
  - `expo_push_token`: string
  - `created_at`, `updated_at`: timestamps

- **sessions/{session_id}** - Agent conversation sessions
  - `user_id`: string
  - `date`: string (YYYY-MM-DD)
  - `state`: object (conversation history, context)
  - `created_at`, `updated_at`: timestamps

- **events/{event_id}** - Scheduled events (timers, morning wake)
  - `user_id`: string
  - `scheduled_time`: timestamp
  - `event_type`: string ("morning_wake", "checkin", etc.)
  - `payload`: object (notification data)
  - `executed`: boolean
  - `cron_job_id`: number (for cleanup)
  - `created_at`: timestamp

## Troubleshooting

### "Missing or insufficient permissions"
- Check that security rules allow public access
- Verify service account has "Cloud Datastore User" role

### "Could not load the default credentials"
- Ensure `FIREBASE_SERVICE_ACCOUNT_PATH` or `FIREBASE_CREDENTIALS` is set
- For Render: Verify base64 encoding has no line breaks (`tr -d '\n'`)

### "Project not found"
- Verify the service account is from the correct Firebase project
- Check project ID in service account JSON matches your Firebase project

## Next Steps

Once Firestore is set up:
1. Update `.env` with Firebase credentials
2. Deploy indexes from `firestore.indexes.json` (for cron reconciliation queries):
   ```bash
   firebase deploy --only firestore:indexes
   ```
   Or create the suggested index directly from backend startup logs.
3. Run `pip install -r requirements.txt` to install `firebase-admin`
4. Start the backend: `python3 main.py`
5. Deploy to Render with `FIREBASE_CREDENTIALS` environment variable
