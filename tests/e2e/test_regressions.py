"""Renee's manual checks, automated. Each test names the feedback it guards (feedback/lookmate-feedback.md)."""

import base64
import json
import re
import struct
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, unquote_plus, urlparse, parse_qs

from .conftest import LIVE_URL, photo

from lookmate.catalog.service import BRANDS
from lookmate.services.colours import NEUTRALS, family
from lookmate.services.lookbook import LOUD
from lookmate.services.match import Target

RED, GREEN, BLUE, YELLOW = (200, 40, 40), (40, 160, 60), (40, 70, 200), (230, 200, 40)


# ---------- helpers ----------

def tab(page, name):
    page.locator(f"#tabs button[data-tab={name}]").click()
    page.locator(f"#{name}").wait_for(state="visible")


def responses(page, pattern):
    """Collect JSON responses whose URL matches, in arrival order."""
    seen = []
    page.on("response", lambda r: seen.append(r) if re.search(pattern, r.url) and r.ok else None)
    return seen


def upload_me(page):
    """Selfie + full-body photo in the Lookbook, then wait until they have been read."""
    # The summary of an earlier analysis may already be on screen, so wait for this upload's own answer.
    with page.expect_response(lambda r: r.url.endswith("/analyses") and r.request.method == "POST"):
        page.locator("#me-files").set_input_files([photo("selfie", RED), photo("fullbody", BLUE)])
    page.wait_for_function("document.querySelector('#me-status').hidden && !!document.querySelector('#me-analysis .me-summary')")


def create_looks(page):
    with page.expect_response(lambda r: "/lookbook?" in r.url and r.ok) as resp:
        page.locator("#lb-create-btn").click()
    page.locator("#lb-sections .outfit").first.wait_for()
    return resp.value.json()


def bright_pieces(outfit):
    loud = []
    for p in outfit["pieces"]:
        denim = re.search(r"\b(denim|jeans?)\b", p["name"], re.I)
        if LOUD.search(f"{p['name']} {p['colour']}") or (family(p["colour"]) not in NEUTRALS | {None} and not denim):
            loud.append(f"{p['colour']} {p['name']}")
    return loud


# ---------- the whole site ----------

def test_every_tab_opens_without_errors(page):
    """Feedback #20: Trends must never be empty. No tab may throw a script error."""
    for name in ("lookbook", "style", "trends", "profile", "find"):
        tab(page, name)
    tab(page, "trends")
    page.locator("#trend-list .trend").first.wait_for()
    assert not page.errors, f"script errors: {page.errors}"


# ---------- Find dupes ----------

def find_dupes(page, outfit_photo):
    looks = responses(page, r"/api/looks/\d+")
    page.locator("#file").set_input_files(outfit_photo)
    # Stop at the page's own error message (AI failed, access code, too slow) instead of waiting out the timeout.
    page.locator("#results .item, #status.error").first.wait_for()
    error = page.locator("#status.error")
    assert not error.is_visible(), f"Find dupes showed an error instead of results: {error.text_content()}"
    done = [r.json() for r in looks]
    return next(d for d in reversed(done) if d.get("status") == "done")["result"]


def outfit_photos():
    shots = [photo("outfit", c) for c in (RED, GREEN, BLUE, YELLOW)]
    return list({s["buffer"]: s for s in shots}.values())  # live runs use the one real photo


def test_find_dupes_match_type_colour_and_length(page):
    """Feedback #21/#25: same garment type, colour and length, plain for plain. Zero picks is fine."""
    wrong = []
    for shot in outfit_photos():
        result = find_dupes(page, shot)
        for s in result["sections"]:
            it = s["item"]
            target = Target(it["category"], it["name"], it["colour"], it.get("details"), it.get("fit", ""))
            for p in s["picks"]:
                if not target.check(SimpleNamespace(**p)):
                    wrong.append(f"{it['colour']} {it['name']} -> {p['colour']} {p['name']}")
            if not s["picks"]:
                assert page.locator("#results .item", has_text=it["name"]).locator(".muted").count(), \
                    f"no picks for {it['name']} but no note saying so"
    assert not wrong, "dupes that don't match:\n" + "\n".join(wrong)
    assert not page.errors, f"script errors: {page.errors}"


def test_shop_links_search_without_brand(page):
    """Feedback #2: SHEIN / ASOS buttons search for the piece and its colour, not the brand."""
    bad, checked = [], 0
    for shot in outfit_photos():
        find_dupes(page, shot)
        for a in page.locator("#results .shop a").all():
            href = a.get_attribute("href")
            url = urlparse(href)
            if "shein" in url.netloc:
                query = unquote(url.path.split("/pdsearch/", 1)[1].strip("/"))
            else:
                query = unquote_plus(parse_qs(url.query)["q"][0])
            checked += 1
            q = query.lower()
            if not q or any(q == b or q.startswith(b + " ") for b in BRANDS) or "asos" in q.split():
                bad.append(f"{href} -> '{query}'")
    assert not bad, "shop links still carry a brand:\n" + "\n".join(bad)
    if not checked:
        print("no picks had shop links in this run")


# ---------- Lookbook ----------

def test_lookbook_shows_nothing_until_photo_and_create(page):
    """Feedback #23: photo first, then options, then "Create my looks"; no outfits before that."""
    tab(page, "lookbook")
    page.locator("#lb-create-btn").wait_for()
    page.wait_for_timeout(800)
    assert page.locator("#lb-sections .outfit").count() == 0, "outfits shown before any photo"
    upload_me(page)
    page.wait_for_timeout(800)
    assert page.locator("#lb-sections .outfit").count() == 0, "outfits shown before Create my looks"
    lb = create_looks(page)
    assert sum(len(s["outfits"]) for s in lb["sections"]) > 0
    assert not page.errors, f"script errors: {page.errors}"


def test_lookbook_has_at_most_one_bright_per_outfit(page):
    """Feedback #16: no bright green with bright blue; one accent colour per outfit at most."""
    tab(page, "lookbook")
    upload_me(page)
    pages = [create_looks(page)]
    with page.expect_response(lambda r: "/lookbook?" in r.url and "occasions" in r.url and r.ok) as resp:
        page.locator("#lb-mode [data-mode=occasions]").click()
    pages.append(resp.value.json())
    clashes = [f"{s['title']} / {o['title']}: {', '.join(bright_pieces(o))}"
               for lb in pages for s in lb["sections"] for o in s["outfits"] if len(bright_pieces(o)) > 1]
    assert not clashes, "outfits with more than one bright piece:\n" + "\n".join(clashes)


def test_fitting_room_starts_empty(page):
    """Feedback #13: pieces from an earlier round never stay in the fitting room."""
    tab(page, "lookbook")
    upload_me(page)
    create_looks(page)
    page.locator("[data-room-all]").first.click()
    page.locator("#room .room-piece").first.wait_for()

    # A new visit: before photos and outfits there is no fitting room on screen.
    page.reload()
    page.locator("#find").wait_for(state="visible")
    tab(page, "lookbook")
    page.wait_for_timeout(800)
    assert page.locator("#room").is_hidden(), "fitting room shown before any outfits"

    # New photos start a new lookbook with an empty fitting room.
    upload_me(page)
    assert page.evaluate("localStorage.getItem('room')") in ("{}", None)
    create_looks(page)
    assert page.locator("#room").is_hidden(), "old pieces still in the fitting room"
    assert page.locator("button[data-room].on").count() == 0, "pieces still marked as in the fitting room"


def test_tryon_uses_the_uploaded_full_body_photo(page):
    """Feedback #6/#11: try-on reuses the full-body photo from the Lookbook upload, no second photo picker.

    The try-on call itself is stubbed, so this never spends Replicate credit, even on the live site.
    """
    sent = []
    page.route("**/api/users/*/tryons", lambda route: (sent.append(route.request.post_data_buffer),
               route.fulfill(status=202, content_type="application/json", body=json.dumps({"id": "e2e-stub"}))))
    page.route("**/api/tryons/e2e-stub", lambda route: route.fulfill(
        content_type="application/json", body=json.dumps({"id": "e2e-stub", "status": "failed", "error": "stubbed by e2e"})))
    pickers = []
    page.on("filechooser", lambda fc: pickers.append(fc))

    tab(page, "lookbook")
    upload_me(page)
    page.wait_for_function("(localStorage.getItem('tryonPhoto') || '').startsWith('data:image/')")
    create_looks(page)
    page.locator("[data-room-all]").first.click()
    page.locator("#room [data-room-try]").click()
    page.wait_for_function("document.querySelector('#tryon-room') && document.querySelector('#tryon-room').textContent.includes('stubbed')")

    assert not pickers, "try-on asked for a photo again although a full-body photo was uploaded"
    assert len(sent) == 1, "try-on request was not sent"
    stored = page.evaluate("localStorage.getItem('tryonPhoto')")
    assert base64.b64decode(stored.split(",", 1)[1]) in sent[0], "try-on did not send the stored photo"
    if not LIVE_URL:
        # Offline, the selfie is red and the full-body photo blue: the blue one must be the try-on photo.
        r, g, b = page.evaluate("""async (url) => {
            const img = new Image(); img.src = url; await img.decode();
            const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
            const ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0);
            return Array.from(ctx.getImageData(img.width >> 1, img.height >> 1, 1, 1).data.slice(0, 3));
        }""", stored)
        assert b > r, f"try-on photo is the selfie (rgb {r},{g},{b}), not the full-body photo"


def test_a_saved_outfit_becomes_a_look_book_page(page):
    """2026-10-05: My Style is a Look Book. Save a Lookbook outfit, find its page, flip through, delete it."""
    tab(page, "lookbook")
    upload_me(page)
    lb = create_looks(page)
    first = next(o for s in lb["sections"] for o in s["outfits"])
    save = page.locator("#lb-sections .save-outfit").first
    assert save.inner_text().strip() == "♡ Save to My Style"
    with page.expect_response(lambda r: r.url.endswith("/outfits") and r.request.method == "POST"):
        save.click()
    page.wait_for_function("document.querySelector('#lb-sections .save-outfit').textContent.includes('Saved')")

    tab(page, "style")
    look = page.locator("#book-contents [data-book-open='2']")
    look.wait_for()
    assert page.locator("#book-contents .book-thumb-cover").count() == 1, "page 1 is the cover"
    assert page.locator("#book-contents .book-thumb-about").count() == 1, "page 2 is About me"
    look.click()
    page.locator("#book-page .bk-look").wait_for()
    assert first["title"] in page.locator("#book-page").inner_text(), "the saved outfit's own page"
    assert page.locator("#book-page .bk-it img, #book-page .bk-it .bp-none").count() == len(first["pieces"])

    page.locator("[data-book-step='-1']").click()
    page.locator("#book-page .bk-about #style-report").wait_for()
    page.locator("[data-book-step='-1']").click()
    page.locator("#book-page .bk-cover").wait_for()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.locator("#book-page .bk-look").wait_for()

    # Coordinator, 2026-10-05: a page goes out as a 3:4 image for Xiaohongshu.
    with page.expect_download() as dl:
        page.locator("[data-book-image]").click()
    png = Path(dl.value.path()).read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and dl.value.suggested_filename.endswith(".png")
    assert struct.unpack(">II", png[16:24]) == (1080, 1440), "a 3:4 portrait, the Xiaohongshu post shape"
    assert page.locator("[data-book-image]").inner_text().strip() == "⤓ Save as image"

    with page.expect_response(lambda r: "/outfits/" in r.url and r.request.method == "DELETE"):
        page.locator("[data-book-delete]").click()
    page.wait_for_function("!document.querySelector('#book-contents [data-book-open=\"2\"]')")
    assert not page.errors, page.errors
