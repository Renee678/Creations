"""Keep the user's face when the fitting room dresses "My model".

In a full-body 3:4 picture the face is only a few dozen pixels, and the image model tends to redraw it as
someone else (Renee's chin-length bob came back long). Two deterministic helpers, no ML:

- face_crop: a close-up of the head, cut from the saved model and upscaled, sent as an extra reference image.
- paste_head: after the render, the head from the model is blended back onto the result with a feathered
  oval, aligned to where the render put the head, when the frames match and the outfit has nothing on the head.
"""

import io

from PIL import Image, ImageDraw, ImageFilter

FACE_SIZE = 768        # the close-up's longest side, upscaled so the model sees the face in detail
BG_DIFF = 40           # how far (0-255, any channel) a pixel must be from the backdrop to count as the person
HEAD_H = 0.17          # head height, top of the hair to the chin, as a share of a standing full-body frame
ASPECT_TOLERANCE = 0.06  # covers the image models' own aspect buckets (3:4 comes back 864x1184), not a new crop
MAX_SHIFT_X = 0.08      # how far the render may move the head (share of the frame) and still get it pasted back
MAX_SHIFT_Y = 0.05


def _open(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img.convert("RGB")


def head_box(img: Image.Image) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) around the head of one person standing on a plain backdrop.

    The top of the hair is the first row that differs from the backdrop; the head's centre is the middle of
    what differs just below it. Without a clear backdrop, the top-centre of the frame is used.
    """
    w, h = img.size
    small_w = 120
    small_h = max(1, round(h * small_w / w))
    px = img.resize((small_w, small_h)).load()
    corners = [px[x, y] for x in (0, 1, small_w - 2, small_w - 1) for y in (0, 1, 2)]
    bg = tuple(sorted(c[i] for c in corners)[len(corners) // 2] for i in range(3))

    def differs(x, y):
        return max(abs(px[x, y][i] - bg[i]) for i in range(3)) > BG_DIFF

    top = next((y for y in range(small_h) if sum(differs(x, y) for x in range(small_w)) >= 3), None)
    if top is None or top > small_h * 0.4:
        return round(w * 0.3), 0, round(w * 0.7), round(h * 0.22)
    band = range(top, min(small_h, top + max(1, round(small_h * HEAD_H * 0.6))))
    cols = sorted(x for y in band for x in range(small_w) if differs(x, y))
    cx = cols[len(cols) // 2] * w / small_w
    head_h = h * HEAD_H
    t = max(0, top * h / small_h - head_h * 0.08)
    b = min(h, t + head_h * 1.12)
    half = (b - t) * 0.45
    return round(max(0, cx - half)), round(t), round(min(w, cx + half)), round(b)


def face_crop(data: bytes) -> tuple[bytes, str] | None:
    """A close-up of the head and hair, upscaled, as a JPEG; None if the picture can't be read."""
    try:
        img = _open(data)
    except (OSError, ValueError):
        return None
    crop = img.crop(head_box(img))
    scale = FACE_SIZE / max(crop.size)
    crop = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))), Image.LANCZOS)
    out = io.BytesIO()
    crop.save(out, "JPEG", quality=92)
    return out.getvalue(), "image/jpeg"


def paste_head(model: bytes, result: bytes) -> tuple[tuple[bytes, str] | None, str]:
    """((image, media type), "") with the model's head blended back in, or (None, why it was skipped).

    The render rarely comes back in exactly the model's frame: Nano Banana answers a 3:4 picture with its own
    3:4 bucket (864x1184, a 2.7% narrower frame), and the person can shift a little. So the model is scaled to
    the render's height and its head is moved onto the render's head, within limits. Reasons: "unreadable",
    "frame" (a really different crop), "moved" (the person is somewhere else in the picture).
    """
    try:
        src, dst = _open(model), _open(result)
    except (OSError, ValueError):
        return None, "unreadable"
    src_ratio = src.width / src.height
    if abs(src_ratio - dst.width / dst.height) > ASPECT_TOLERANCE * src_ratio:
        return None, "frame"  # a different crop: the head would come out the wrong size
    if src.height != dst.height:
        src = src.resize((max(1, round(src.width * dst.height / src.height)), dst.height), Image.LANCZOS)
    l, t, r, b = head_box(src)
    rl, rt_, rr, _ = head_box(dst)
    dx, dy = round((rl + rr - l - r) / 2), rt_ - t
    if abs(dx) > dst.width * MAX_SHIFT_X or abs(dy) > dst.height * MAX_SHIFT_Y:
        return None, "moved"  # a pasted head would float beside theirs
    # The model, moved so its head sits on the render's head, then a feathered oval a little larger than the
    # head, so the hairline and the neck blend in.
    moved = dst.copy()
    moved.paste(src, (dx, dy))
    l, t, r, b = l + dx, t + dy, r + dx, b + dy
    pad_x, pad_y = (r - l) * 0.08, (b - t) * 0.04
    box = (round(l - pad_x), round(t - pad_y), round(r + pad_x), round(b + pad_y))
    mask = Image.new("L", dst.size, 0)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(max(2, (r - l) * 0.06)))
    dst.paste(moved, (0, 0), mask)
    out = io.BytesIO()
    dst.save(out, "JPEG", quality=92)
    return (out.getvalue(), "image/jpeg"), ""
