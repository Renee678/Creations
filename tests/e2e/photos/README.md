# Photos for live runs

Offline runs make their own test images. A run against the deployed site (`LOOKMATE_URL=...`) sends
photos to the real AI, so it needs real ones. Put these here (git ignores them, so they stay on your computer):

- `outfit.jpg`: an outfit screenshot, for Find dupes
- `selfie.jpg`: a selfie in daylight
- `fullbody.jpg`: a full-body photo, standing, facing the camera

Tests that need a missing photo are skipped, not failed.
