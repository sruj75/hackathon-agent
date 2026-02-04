# Speaker Mode Fix - Testing Instructions

## What Was Fixed
The audio was routing to earpiece because `react-native-live-audio-stream` was missing the `DefaultToSpeaker` option in its iOS audio session configuration. We patched the library to add this option.

## Testing Instructions

### Option 1: Test on iOS Simulator
```bash
cd frontend
npx expo run:ios
```

### Option 2: Build for Physical Device
```bash
cd frontend
npx expo run:ios --device
```

### Option 3: Build for TestFlight
```bash
cd frontend
eas build --platform ios --profile preview
```

## What to Test

1. **Start the assistant** - Open the app and start a voice session
2. **Speak to the agent** - Say something and verify:
   - ✅ Your audio is being streamed (check backend logs)
   - ✅ Agent responds with audio
   - ✅ **AUDIO PLAYS THROUGH SPEAKER** (not earpiece!)
3. **Check bidirectional streaming** - Verify you can interrupt the agent and it responds

## Expected Results

- ✅ Audio plays through **speaker** (loudspeaker)
- ✅ Recording continues to work
- ✅ Bidirectional streaming is NOT disrupted
- ✅ No WebSocket disconnections
- ✅ No Gemini API errors

## If It Doesn't Work

Check these things:

1. **Verify patch is applied:**
   ```bash
   grep DefaultToSpeaker node_modules/react-native-live-audio-stream/ios/RNLiveAudioStream.m
   ```
   Should show 2 lines with `DefaultToSpeaker`

2. **Reinstall dependencies:**
   ```bash
   rm -rf node_modules
   npm install
   npx expo prebuild --platform ios --clean
   ```

3. **Check if phone is in silent mode:**
   - The app respects `playsInSilentModeIOS: true`, so it should work even in silent mode
   - But test with ringer ON first

## Technical Details

### What Changed
**File:** `node_modules/react-native-live-audio-stream/ios/RNLiveAudioStream.m`

**Before:**
```objective-c
options: AVAudioSessionCategoryOptionDuckOthers |
         AVAudioSessionCategoryOptionAllowBluetooth |
         AVAudioSessionCategoryOptionAllowAirPlay
```

**After:**
```objective-c
options: AVAudioSessionCategoryOptionDuckOthers |
         AVAudioSessionCategoryOptionAllowBluetooth |
         AVAudioSessionCategoryOptionAllowAirPlay |
         AVAudioSessionCategoryOptionDefaultToSpeaker  // <-- ADDED
```

### How Patches Work
- The patch is stored in `patches/react-native-live-audio-stream+1.1.1.patch`
- Every time you run `npm install`, the patch is automatically applied (via `postinstall` script)
- When you build the iOS app, it uses the patched version
- **No need to maintain a fork or create custom native modules!**

## Sharing This Fix

If you want to share this codebase:
- The `patches/` folder is committed to git
- Anyone who clones and runs `npm install` will get the patched version
- No manual steps required for team members

## Future Updates

If `react-native-live-audio-stream` releases a new version:
- You'll need to recreate the patch for the new version
- Or wait for the library to add `DefaultToSpeaker` option (consider submitting a PR!)

## Troubleshooting

### "Audio still goes to earpiece"
1. Make sure you rebuilt the iOS app after applying the patch
2. Check that `postinstall` script ran (you should see "patch-package" output during `npm install`)
3. Verify the patch exists: `ls patches/`

### "Recording stopped working"
- This shouldn't happen with this patch
- The patch only changes the OUTPUT routing (speaker vs earpiece)
- Recording (input) is unaffected

### "WebSocket still disconnects"
- This fix only addresses speaker routing
- WebSocket issues are separate (likely network/firewall related for TestFlight)

---

## ✅ SUCCESS CRITERIA

You'll know the fix worked when:
1. You can hear the agent's voice through the **speaker** (loud and clear)
2. Bidirectional streaming continues to work
3. No new errors in logs
4. No need to hold phone to ear!

Good luck! 🎉
