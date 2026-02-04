# Phase 3: Push Notifications - Manual Testing Guide

## Prerequisites

1. **Physical iOS Device** (iPhone/iPad) - Push notifications don't work in simulator
2. **Expo Go App** installed OR development build
3. **Device and computer on same network** (for local testing)
4. **Backend running** on your local machine

## Setup Steps

### 1. Start Backend Server

```bash
cd agent
python main.py
```

Verify it's running at `http://localhost:8080`

### 2. Configure Frontend Environment

Edit `frontend/.env`:

```bash
# Replace with your machine's local IP address
EXPO_PUBLIC_BACKEND_URL=http://192.168.X.X:8080
```

Find your IP:
- macOS: `ifconfig | grep "inet " | grep -v 127.0.0.1`
- Windows: `ipconfig`

### 3. Start Frontend Development Server

```bash
cd frontend
npx expo start
```

### 4. Run App on Physical Device

**Option A: Expo Go (Easier)**
- Open Expo Go app on your device
- Scan the QR code from terminal
- App will load

**Option B: Development Build (Better for testing)**
```bash
# Build and install on device
eas build --profile development --platform ios --local
```

### 5. Get Push Token

1. Open the app on your device
2. Grant notification permissions when prompted
3. Check backend logs for:
   ```
   [useNotifications] Got push token: ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]
   [useNotifications] Token saved to backend successfully
   ```
4. Copy the token for manual testing

### 6. Verify Token in Database

```bash
cd agent
sqlite3 data/agent.db
SELECT * FROM user_push_tokens;
```

You should see:
```
user_default|ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]
```

## Manual Testing

### Test 1: Send Test Notification via curl

```bash
curl -H "Content-Type: application/json" \
     -X POST "https://exp.host/--/api/v2/push/send" \
     -d '{
       "to": "ExponentPushToken[YOUR_TOKEN_HERE]",
       "title": "Test Notification",
       "body": "Testing push notifications!",
       "data": {
         "session_id": "session_user_default_2026-02-04",
         "type": "test"
       },
       "sound": "default",
       "priority": "high"
     }'
```

**Expected Result:**
- Notification appears on device within 5 seconds
- Shows "Test Notification" with body text
- Tapping opens app to assistant screen

### Test 2: Trigger Agent to Send Notification

1. **Create a test event manually:**

```bash
# In agent directory
python -c "
import asyncio
from database import SessionLocal
from repos import event_repo
from datetime import datetime, timedelta

async def create_test_event():
    async with SessionLocal() as db:
        event = await event_repo.create_event(
            db=db,
            user_id='user_default',
            event_type='test_checkin',
            scheduled_at=datetime.utcnow() + timedelta(seconds=30),
            payload={'message': 'Time to check in!'}
        )
        print(f'Created event: {event.event_id}')
        print(f'Will fire in 30 seconds')
        return event.event_id

event_id = asyncio.run(create_test_event())
"
```

2. **Manually trigger the event (don't wait 30 seconds):**

```bash
# Replace EVENT_ID with the ID from step 1
curl -X POST "http://localhost:8080/api/execute-event/EVENT_ID"
```

3. **Check backend logs for:**
```
🧠 [THINKING] Waking agent. Session: session_user_default_2026-02-04
[send_push_notification_tool] Agent sending notification: title='...', body='...', type='...'
🚀 [PUSH NOTIFICATION] Sending to user_default: title='...', body='...', data={...}
✅ [PUSH NOTIFICATION] Successfully sent to user_default
```

**Expected Result:**
- Agent wakes up and decides to send notification
- Notification appears on device
- Tapping notification opens assistant with session context

### Test 3: Deep Linking Verification

1. **Send notification with session_id:**
```bash
curl -H "Content-Type: application/json" \
     -X POST "https://exp.host/--/api/v2/push/send" \
     -d '{
       "to": "ExponentPushToken[YOUR_TOKEN_HERE]",
       "title": "Resume Session",
       "body": "Continue your conversation",
       "data": {
         "session_id": "session_user_default_2026-02-04",
         "type": "checkin"
       },
       "sound": "default",
       "priority": "high"
     }'
```

2. **Kill the app completely** (swipe up from app switcher)
3. **Tap the notification**

**Expected Result:**
- App opens to assistant screen
- Console logs show:
  ```
  [RootLayout] Notification tapped with data: {session_id: "...", type: "checkin"}
  [AssistantScreen] Resuming session from notification: session_user_default_2026-02-04 type: checkin
  ```
- WebSocket connects with correct session_id
- Agent can resume conversation with context

## Debugging Tips

### No notification received?

1. **Check permissions:**
   - Settings → Your App → Notifications → Enabled

2. **Verify token in database:**
   ```bash
   sqlite3 agent/data/agent.db
   SELECT * FROM user_push_tokens;
   ```

3. **Check backend logs for Expo API response:**
   - Look for error messages
   - Check for "DeviceNotRegistered" errors

4. **Test with Expo's push notification tool:**
   ```bash
   npx expo push:test ExponentPushToken[YOUR_TOKEN]
   ```

### Deep linking not working?

1. **Verify scheme in app.json:**
   ```json
   "scheme": "intentive"
   ```

2. **Check notification data payload:**
   - Must include `session_id`
   - Data format: `{"session_id": "...", "type": "..."}`

3. **Review iOS system logs:**
   - Open Console.app on Mac
   - Filter by your app name
   - Look for deep link errors

4. **Test with app in different states:**
   - Foreground (app open)
   - Background (app minimized)
   - Killed (app completely closed)

### App doesn't open to assistant?

1. **Check notification listener registration:**
   - Verify `Notifications.addNotificationResponseReceivedListener` in `_layout.tsx`

2. **Check route params:**
   - Add logging in `assistant/index.tsx`:
     ```typescript
     console.log('Route params:', params);
     ```

3. **Verify router navigation:**
   - Check for navigation errors in logs

## Testing the Full Flow

1. **Start fresh session:**
   - Open app
   - Connect to assistant
   - Have a brief conversation

2. **Set a timer (via agent):**
   ```
   "Set a timer for 1 minute to check in on me"
   ```

3. **Wait for timer to fire:**
   - Backend cron will trigger event
   - Agent wakes up and decides to notify
   - Notification sent to device

4. **Tap notification:**
   - App opens with session context
   - Agent resumes conversation
   - Full context from earlier is available

## Expected Behavior Checklist

- ✅ App requests notification permissions on launch
- ✅ Push token saved to backend successfully
- ✅ Manual notification received within 5 seconds
- ✅ Tapping notification opens app to assistant screen
- ✅ Deep linking works when app is killed
- ✅ Agent can send notifications via tool call
- ✅ Session context preserved across notification tap
- ✅ WebSocket connects with correct session_id
- ✅ Agent can resume conversation with full history

## Common Issues

### "Invalid token format" error

Token must match pattern: `ExponentPushToken[...]`

### "DeviceNotRegistered" error

- Token expired or device uninstalled app
- Backend automatically deletes invalid token
- Next app open will register new token

### Notification delivered but no sound

- Check device silent mode
- Verify `"sound": "default"` in payload
- iOS: Check Focus/Do Not Disturb settings

### Payload data not in notification

- Data must be in `"data"` field, not top-level
- Maximum payload size: 4KB
- Data values must be strings (not objects)

## Architecture Notes

### Key Principle: Agent Has Agency

- Timer fires → Agent wakes up in Thinking Mode
- Agent evaluates context and decides what to do
- If needed, agent calls `send_push_notification` tool
- Notification is an **invitation to conversation**, not pre-recorded message

### Flow Diagram

```
1. Timer fires → Cron calls /api/execute-event
   ↓
2. Agent wakes up in Thinking Mode (Standard API)
   ↓
3. Agent decides based on context
   ↓
4. Agent MAY call send_push_notification(title, body, data) tool
   ↓
5. Backend sends notification via Expo API
   ↓
6. User taps notification → App opens with session_id
   ↓
7. WebSocket connects → Voice conversation resumes
```

## v0 Limitations (By Design)

- Single user only (`user_default`)
- No retry logic for token registration
- No retry logic for notification delivery
- Permissions requested aggressively on launch
- No notification history/logging
- iOS only (Android not configured)

## Next Steps After Testing

Once basic flow works:

1. Test with different notification types (morning_wake, checkin)
2. Test session continuity across multiple notifications
3. Test error handling (deny permissions, invalid token, network errors)
4. Measure notification delivery time
5. Test with app in various states (foreground, background, killed)

---

**Note:** This is v0 testing. Focus on proving core functionality works end-to-end. Edge cases and polish can come later.
