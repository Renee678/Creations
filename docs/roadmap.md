# Roadmap and ideas

Ideas captured during development, with a feasibility note for each. Items move to "Done" when shipped.

## Done
- Look-alike search from an outfit photo, with per-pick reasons
- Profile (height, weight, age, body shape, styles, budget) and rule-based fit advice
- Style memory from uploads and saves
- Weekly trend radar
- Logo and favicon

## Next (proposed)
- **Search modes** (Renee, 2026-10-04): not only cheaper "平替". Modes for *cheaper look-alike*, *closest
  match at any price* and *premium upgrade (贵替)*. Ranking already separates the price term, so a mode
  changes that weight and the price filter. A premium mode needs a higher price band, because the H&M
  catalog is budget-only.
- **Image evaluation set**: about 20 labelled outfit photos, measuring item recall and category hit rate
  for look-alikes.

## Later: virtual try-on (Renee, 2026-10-04)
Idea: from the user's photo or measurements, show how a recommended item, hairstyle, makeup, shoes or bag
would look on them, like the dress-up games 奇迹暖暖 / 无限暖暖. Recommendations are only useful if you
can see they suit you.

Feasibility:
- **Claude cannot generate images**, so try-on needs a separate image model.
- **2D photo try-on** works today with virtual try-on models (e.g. open-source IDM-VTON, or hosted
  try-on APIs priced per image). It needs a full-body user photo and clean product images. It fits the
  existing async worker as another job type, but it brings privacy (body photos), cost and latency
  questions.
- **3D avatars** (body model from measurements, physics-based clothing) are a multi-month project.
- **Lightweight version that fits the current scope:** an "outfit board" that puts the chosen pieces
  together on one card, with fit notes for the user's body shape and no generated images.
