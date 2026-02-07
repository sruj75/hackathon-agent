# Dynamic Cron Timer Implementation - Setup Guide

## Overview

The timer system now uses **dynamic cron job creation** via cron-jobs.org REST API instead of polling. This eliminates wasted API calls and provides exact timing precision.

### Architecture

```
Agent sets timer → Create one-time cron job → Fires at exact time → Execute & cleanup
```

**Benefits:**
- Zero wasted API calls (only fires when needed)
- Exact timing precision (fires at the exact minute)
- No database polling overhead
- Auto-cleanup with `expiresAt` field

## Setup Instructions

### 1. Get cron-jobs.org API Key

1. Sign up at [https://console.cron-job.org](https://console.cron-job.org)
2. Go to **Settings** → **API**
3. Generate an API key
4. Copy the API key

### 2. Configure Environment Variables

Edit your `.env` file:

```bash
# For cron-jobs.org REST API (dynamic timer creation)
CRONJOB_ORG_API_KEY=your_api_key_here

# Backend URL for cron job callbacks
BACKEND_URL=https://your-app.onrender.com  # Production URL
# BACKEND_URL=http://localhost:8080        # Local development
```

**Important:** 
- For local testing, use `http://localhost:8080`
- For production on Render, use your actual deployment URL (e.g., `https://intentive-backend.onrender.com`)
- cron-jobs.org needs to be able to reach this URL via HTTP/HTTPS

### 3. Testing

Run the test suite to verify everything works:

```bash
cd agent
python3 test_dynamic_cron.py
```

**Expected Output:**
- ✅ Repository functions work correctly
- If API key is set: Full integration tests with cron-jobs.org API

## How It Works

### Timer Creation Flow

1. **Agent calls `set_checkin_timer(60, "How's it going?")`**
   - Calculates target time (now + 60 minutes)
   - Creates ScheduledEvent in database
   
2. **Create dynamic cron job via API**
   - Calls `cron_service.create_one_time_job()`
   - Sends PUT request to cron-jobs.org
   - Job configured to fire at exact target time
   - Sets `expiresAt` to 5 minutes after (prevents recurrence)
   
3. **Update event with cron job ID**
   - Stores `cron_job_id` in database for cleanup

4. **Execution (when time arrives)**
   - cron-jobs.org fires HTTP POST to `/api/execute-event/{event_id}`
   - Backend executes agent logic
   - Sends push notification to user
   - Marks event as executed
   - Deletes cron job from cron-jobs.org (cleanup)

### Morning Wake Scheduling

On server startup, the system:
1. Checks all users for tomorrow's morning wake events
2. Creates events + cron jobs if they don't exist
3. Recovers cron jobs for events that exist but have no `cron_job_id` (migration support)

## API Endpoints

### Removed (No Longer Needed)
- ~~`GET /api/check-pending`~~ - Polling endpoint removed

### Updated
- `POST /api/execute-event/{event_id}` - Now called directly by cron-jobs.org
  - No longer requires `x-cron-secret` header
  - Security: Event IDs are UUIDs (unguessable) + idempotent execution

## Rate Limits

cron-jobs.org free tier:
- **100 API requests per day** (default)
- **5,000 API requests per day** (sustaining members)
- 1 job creation per second
- 5 job deletions per second

For v0 usage:
- ~10-20 timers per user per day
- 1-10 users = well within free tier limits

## Troubleshooting

### Test Fails: "CRONJOB_ORG_API_KEY not set"
- Add API key to `.env` file
- Restart any running processes

### Cron job created but not firing
- Check `BACKEND_URL` is publicly accessible
- For local testing, use ngrok or similar tunnel
- Verify cron-jobs.org can reach your URL

### Event executes but cron job not deleted
- Check logs for cleanup errors
- Manually delete via cron-jobs.org console
- Non-critical: Job will expire automatically via `expiresAt`

## Development vs Production

### Local Development
```bash
BACKEND_URL=http://localhost:8080
```
- Use ngrok for testing: `ngrok http 8080`
- Update BACKEND_URL to ngrok URL
- Remember: cron-jobs.org must reach your backend

### Production (Render)
```bash
BACKEND_URL=https://your-app.onrender.com
```
- Set in Render environment variables
- No changes needed after deployment
- cron-jobs.org will call production URL directly

## Migration from Polling System

If you have existing events from the old polling system:

1. The startup function automatically recovers them
2. Events without `cron_job_id` get jobs created
3. No manual intervention needed

To clean up old polling config:
1. Remove any external cron job hitting `/api/check-pending`
2. The endpoint is removed and will return 404

## Next Steps

1. ✅ Set `CRONJOB_ORG_API_KEY` in `.env`
2. ✅ Update `BACKEND_URL` to your deployment URL
3. ✅ Run tests: `python3 test_dynamic_cron.py`
4. ✅ Start backend: `python3 main.py`
5. ✅ Test via agent: Set a timer and watch logs
6. ✅ Deploy to production with environment variables

## Support

For issues or questions:
- Check logs: Backend logs show cron job creation/deletion
- Test API key: Run test suite
- Verify URL accessibility: Use curl or browser
- Check cron-jobs.org console for job status
