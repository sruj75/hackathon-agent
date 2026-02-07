# Quick Firebase Setup for Backend

## What You Need

The backend uses Firebase **Admin SDK** (server-side), which only needs:
1. **Service account credentials** - A JSON file with admin access to your Firebase project

## Setup Steps

### 1. Download Service Account JSON

1. Go to: https://console.firebase.google.com/project/hackathon-intentive/settings/serviceaccounts/adminsdk
2. Click **"Generate new private key"**
3. Save the downloaded JSON file as: `agent/firebase-service-account.json`

### 2. Configure Firestore Security Rules

Since you're not using Firebase CLI, set the rules directly in the console:

1. Go to: https://console.firebase.google.com/project/hackathon-intentive/firestore/rules
2. Replace the rules with:

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // v0: Public access for TestFlight testing (no auth)
    match /{document=**} {
      allow read, write: if true;
    }
  }
}
```

3. Click **Publish**

⚠️ **Note**: This allows public read/write for v0 testing. For v1 with authentication, you'll update these rules.

### 3. Update Local Environment

Add to `agent/.env`:
```bash
FIREBASE_SERVICE_ACCOUNT_PATH=firebase-service-account.json
```

### 4. Install Dependencies

```bash
cd agent
pip install -r requirements.txt
```

### 5. Test Locally

```bash
cd agent
python3 main.py
```

If Firestore is connected, you'll see: `Firestore client initialized successfully`

## For Render Deployment

1. Base64 encode the service account:
   ```bash
   base64 -i agent/firebase-service-account.json | tr -d '\n' > firebase-creds-base64.txt
   ```

2. Copy the contents of `firebase-creds-base64.txt`

3. In Render dashboard, add environment variable:
   - **Key**: `FIREBASE_CREDENTIALS`
   - **Value**: (paste the base64 string)

## Done!

Your backend will now use Firestore instead of SQLite. Collections will be created automatically when the app runs.

**View your data**: https://console.firebase.google.com/project/hackathon-intentive/firestore
