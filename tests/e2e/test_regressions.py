"""Renee's manual checks, automated. Each test names the feedback it guards (feedback/lookmate-feedback.md)."""

import base64
import json
import re
import struct
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, unquote_plus, urlparse, parse_qs

import pytest

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
    page.locator("#find-btn").click()  # picking a photo only pins it (feedback batch 14)
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
    # Without a model, the first try-on offers to make one; skipped, it uses the uploaded photo.
    page.locator("#tryon-room [data-model-skip]").click()
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
    # Feedback item 31: page tabs instead of "← Contents" and arrows. The book opens on its cover.
    page.locator("#book-page .bk-cover").wait_for()
    tabs = page.locator("#book-tabs button")
    assert [t.inner_text().strip() for t in tabs.all()] == ["Cover", "About me", "Favourites", "Outfits · 1"]
    assert page.locator("#book-tabs button.on").inner_text().strip() == "Cover"
    assert page.locator("[data-book-step], [data-book-contents]").count() == 0, "no arrows or contents link"
    for name, sel in (("About me", ".bk-about #style-report"), ("Favourites", ".fav-closet"), ("Cover", ".bk-cover")):
        page.locator("#book-tabs button", has_text=name).click()
        page.locator(f"#book-page {sel}").wait_for()
        assert page.locator("#book-tabs button.on").inner_text().strip() == name

    page.locator("#book-tabs button", has_text="Outfits").click()
    look = page.locator("#book-contents .book-thumb-look")
    look.wait_for()
    assert look.count() == 1 and page.locator("#book-contents .book-thumb-cover").count() == 0, "the Outfits tab lists outfits"
    look.click()
    page.locator("#book-page .bk-look").wait_for()
    assert page.locator("#book-tabs button.on").inner_text().strip().startswith("Outfits")
    assert first["title"] in page.locator("#book-page").inner_text(), "the saved outfit's own page"
    assert page.locator("#book-page .bk-it img, #book-page .bk-it .bp-none").count() == len(first["pieces"])

    page.keyboard.press("ArrowLeft")  # arrow keys (and swipes) still turn the pages
    page.locator("#book-page .fav-closet").wait_for()
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
    page.wait_for_function("!document.querySelector('#book-contents .book-thumb-look')")
    assert page.locator("#book-tabs button.on").inner_text().strip() == "Outfits · 0"
    assert not page.errors, page.errors


def test_find_dupes_waits_for_the_button_and_clears_old_results(page):
    """Feedback batch 14 #1: picking a photo doesn't start the search; "Find my dupes" does. A new photo clears
    the old results at once, and moving the price range after results waits for the button too."""
    posts, fetches = [], []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and r.url.endswith("/api/looks") else None)
    page.on("request", lambda r: fetches.append(r.url) if re.search(r"/api/looks/\d+", r.url) else None)
    first, second = outfit_photos()[:2]

    page.locator("#file").set_input_files(first)
    page.locator("#find-btn").wait_for()
    assert page.locator("#price-find").is_visible(), "the price range is set before searching"
    page.wait_for_timeout(800)
    assert not posts and page.locator("#results").inner_html().strip() == "", "a photo alone must not start the search"

    page.locator("#find-btn").click()
    page.locator("#results .item").first.wait_for()
    assert len(posts) == 1

    page.locator("#file").set_input_files(second)
    assert page.locator("#results .item").count() == 0, "old results go as soon as a new photo is picked"
    page.wait_for_timeout(800)
    assert len(posts) == 1, "the new photo waits for the button too"
    page.locator("#find-btn").click()
    page.locator("#results .item").first.wait_for()
    assert len(posts) == 2

    seen = len(fetches)
    page.locator("#price-find .pr-max").fill("100")
    page.locator("#price-find .pr-max").dispatch_event("change")
    page.wait_for_timeout(500)
    assert len(fetches) == seen, "moving the price doesn't re-run the search by itself"
    with page.expect_request(lambda r: "price_max=100" in r.url):
        page.locator("#find-btn").click()
    page.locator("#results .item").first.wait_for()
    assert not page.errors, page.errors


def test_price_range_is_one_slider_filled_only_between_the_thumbs(page):
    """Feedback batch 14 #2: at $0 the "from" slider showed a blue stretch left of its thumb. One track with two
    thumbs now, filled only between them, on Find dupes and Lookbook alike."""
    page.locator("#file").set_input_files(outfit_photos()[0])
    for box in ("#price-find", "#price-lookbook"):
        if box == "#price-lookbook":
            tab(page, "lookbook")
        b = page.locator(box)
        assert b.locator(".pr-slider").count() == 1 and b.locator("input[type=range]").count() == 2
        assert b.locator(".pr-min").evaluate("e => getComputedStyle(e).appearance") == "none", \
            "the browser's own fill is off: it drew the stretch left of the low thumb"
        for low, high in ((0, 60), (90, 210)):
            b.locator(".pr-min").fill(str(low))
            b.locator(".pr-min").dispatch_event("input")
            b.locator(".pr-max").fill(str(high))
            b.locator(".pr-max").dispatch_event("input")
            # Where the browser draws each thumb's centre: half a thumb in from each end of the input.
            box_ = b.locator(".pr-min").bounding_box()
            thumb = 20  # style.css: .pr-slider thumbs are 20px wide
            centre = lambda v: box_["x"] + thumb / 2 + (box_["width"] - thumb) * v / 300  # noqa: E731
            fill = b.locator(".pr-fill").bounding_box()
            assert abs(fill["x"] - centre(low)) < 2, f"nothing filled left of the low thumb (at ${low})"
            assert abs(fill["x"] + fill["width"] - centre(high)) < 2, "and the fill ends at the high thumb"
    assert not page.errors, page.errors


def test_a_piece_saved_in_find_dupes_appears_in_its_my_style_folder(page):
    """Feedback batch 14 #3/#4: "♡ Save to My Style" on a dupe puts it in My Style → Favourites, in its folder."""
    from lookmate.api.style import FOLDERS, folder_of

    pick = None
    for shot in outfit_photos():
        result = find_dupes(page, shot)
        pick = next((p for s in result["sections"] for p in s["picks"] if p["category"] == "top"), None) \
            or next((p for s in result["sections"] for p in s["picks"]), None)
        if pick:
            break
    assert pick, "no dupes at all to save"
    button = page.locator(f"#results button[data-save='{pick['id']}']").first
    assert button.inner_text().strip() == "♡ Save to My Style"
    with page.expect_response(lambda r: r.url.endswith("/saved") and r.request.method == "POST"):
        button.click()
    assert button.inner_text().strip() == "Saved ♥"

    folder = folder_of(SimpleNamespace(**pick))
    label = dict(FOLDERS)[folder]
    tab(page, "style")
    page.locator("#book-tabs button", has_text="Favourites").click()
    section = page.locator(f"#book-page .fav-folder[data-folder='{folder}']")
    section.wait_for()
    assert label in section.locator(".rail-h").inner_text()
    # Feedback item 31: the lilac closet. One silver rail per folder with pieces, each piece on a hook.
    rails = page.locator("#book-page .fav-closet .rail")
    counts = {r.get_attribute("data-folder"): r.locator(".hang").count() for r in rails.all()}
    assert counts and all(counts.values()), f"a rail for every non-empty folder only: {counts}"
    assert page.locator("#book-page .fav-closet .seg button.off").count() == 9 - 1 - len(counts), "empty folders dimmed"
    assert section.locator(".hang .hook").count() == section.locator(".hang").count()
    assert section.locator(".hang .pricetag").first.inner_text().startswith("$")
    hang = section.locator(".hang").first
    assert hang.evaluate("e => getComputedStyle(e).animationName") == "sway", "hangers sway"
    page.emulate_media(reduced_motion="reduce")
    assert hang.evaluate("e => getComputedStyle(e).animationName") == "none", "but not with reduced motion"
    assert section.locator(f"[data-fav-remove='{pick['id']}']").count() == 1, f"saved piece is not under {label}"
    assert section.locator("[data-room]").count() == 0, "no fitting-room button in Favourites (Renee)"
    remove = section.locator(f"[data-fav-remove='{pick['id']}']")
    assert remove.inner_text().strip() == "✕"
    with page.expect_response(lambda r: "/saved/" in r.url and r.request.method == "DELETE"):
        remove.click()
    page.wait_for_function(f"!document.querySelector(\"[data-fav-remove='{pick['id']}']\")")
    page.locator("#book-page .fav-closet .empty").wait_for()  # the only piece: the dashed "empty closet" hint
    assert not page.errors, page.errors


FLIP = {"spring": "autumn", "summer": "winter", "autumn": "spring", "winter": "summer"}


def _season_chips(page, box):
    chips = page.locator(f"{box} button[data-season]")
    return [c.inner_text().strip() for c in chips.all()], page.locator(f"{box} button[data-season].on").get_attribute("data-season")


def _check_four_seasons(page, expected):
    tab(page, "trends")
    page.locator("#trend-list .trend").first.wait_for()
    labels, on = _season_chips(page, "#trend-seasons")
    assert labels == ["Spring", "Summer", "Autumn", "Winter"], labels
    assert on == expected
    assert str(__import__("datetime").date.today().year) in page.locator("#trends-meta").inner_text()
    for season in ("spring", "summer", "autumn", "winter"):
        page.locator(f"#trend-seasons [data-season='{season}']").click()
        page.locator("#trend-list .trend").first.wait_for()  # every season has trends, seed or researched

    tab(page, "lookbook")
    page.locator("#lb-seasons button[data-season]").first.wait_for()
    labels, on = _season_chips(page, "#lb-seasons")
    assert labels == ["Spring", "Summer", "Autumn", "Winter"] and on == expected
    assert not page.errors, page.errors


def test_trends_and_lookbook_offer_all_four_seasons(page):
    """Feedback (Trends): Spring to Winter for this year, no "now"/"next"; the northern season preselected."""
    from datetime import datetime, timezone

    from lookmate.services.trends import season_of

    _check_four_seasons(page, season_of(datetime.now(timezone.utc).month))


@pytest.mark.timezone("Australia/Sydney")
def test_a_southern_hemisphere_shopper_starts_on_their_own_season(page):
    """Feedback (Trends): it's spring in Sydney when it's autumn in New York."""
    from datetime import datetime, timezone

    from lookmate.services.trends import season_of

    _check_four_seasons(page, FLIP[season_of(datetime.now(timezone.utc).month)])


def test_my_model_is_made_in_profile_and_dresses_every_try_on(page):
    """Feedback #33: Profile -> My model: one full-body photo -> Create my model -> Save; try-ons then dress it.

    Offline there is no Replicate token, so the photo is saved as it is, with a note (the fallback). The try-on
    call is stubbed, so this never spends Replicate credit on the live site.
    """
    if LIVE_URL:
        pytest.skip("creates and deletes a model on the server; offline only")
    tab(page, "profile")
    box = page.locator("#my-model")
    assert box.is_visible() and box.locator("[data-model-create]").count() == 0, "nothing to create before a photo"
    # Feedback #37: an optional face close-up makes the face more like her; it goes with the full-body photo.
    face = box.locator(".model-face")
    assert "A clear photo of your face" in face.inner_text() and "optional" in face.inner_text()
    with page.expect_file_chooser() as fc:
        face.locator("[data-model-face-pick]").click()
    fc.value.set_files(photo("selfie", RED))
    face.locator("img").wait_for()
    with page.expect_file_chooser() as fc:
        box.locator("[data-model-pick]").click()
    fc.value.set_files(photo("fullbody", BLUE))
    sent = []
    page.route("**/api/users/*/model", lambda route: (sent.append(route.request.post_data_buffer or b""),
                                                      route.continue_()))
    box.locator("[data-model-create]").click()
    box.locator(".model-shot img").wait_for()
    page.unroute("**/api/users/*/model")
    assert b'name="face"' in sent[0] and b'name="photo"' in sent[0], "both photos are sent"
    assert "REPLICATE_API_TOKEN" in box.locator(".model-note").inner_text(), "the fallback says why"
    assert box.locator("[data-model-original]").count() == 0, "a saved-as-is photo has no 'use original'"
    with page.expect_response(lambda r: r.url.endswith("/save") and r.ok):
        box.locator("[data-model-save]").click()
    box.locator("[data-model-delete]").wait_for()

    # Saved on the server: a new visit on another device finds it.
    page.reload()
    tab(page, "profile")
    box.locator("[data-model-delete]").wait_for()
    image_url = box.locator(".model-shot img").get_attribute("src")
    assert image_url.startswith("/api/body-models/")

    # The fitting room dresses the model: no photo is asked for or sent.
    sent = []
    page.route("**/api/users/*/tryons", lambda route: (sent.append(route.request.post_data_buffer),
               route.fulfill(status=202, content_type="application/json", body=json.dumps({"id": "e2e-stub"}))))
    page.route("**/api/tryons/e2e-stub", lambda route: route.fulfill(
        content_type="application/json", body=json.dumps({"id": "e2e-stub", "status": "failed", "error": "stubbed by e2e"})))
    pickers = []
    page.on("filechooser", lambda fc: pickers.append(fc))
    tab(page, "lookbook")
    page.locator("[data-lb-browse]").click()
    page.locator("[data-room-all]").first.click()
    assert page.locator("#room .room-me img").get_attribute("src") == image_url, "the tray shows My model"
    page.locator("#room [data-room-try]").click()
    page.wait_for_function("document.querySelector('#tryon-room') && document.querySelector('#tryon-room').textContent.includes('stubbed')")
    assert not pickers and len(sent) == 1
    assert b'name="photo"' not in sent[0], "with a saved model the server dresses it; no photo is uploaded"

    # Delete my model: gone from the server too.
    tab(page, "profile")
    box.locator("[data-model-delete]").click()
    box.locator("[data-model-pick]").wait_for()
    assert page.request.get(image_url).status == 404
    assert page.errors == []


def test_skipped_at_sign_up_my_model_is_offered_in_the_fitting_room(page):
    """Feedback #33: skipped at sign-up, the first try-on shows the same step; what's made is saved to Profile."""
    if LIVE_URL:
        pytest.skip("creates a model on the server; offline only")
    page.route("**/api/users/*/tryons", lambda route: route.fulfill(
        status=202, content_type="application/json", body=json.dumps({"id": "e2e-stub"})))
    page.route("**/api/tryons/e2e-stub", lambda route: route.fulfill(
        content_type="application/json", body=json.dumps({"id": "e2e-stub", "status": "failed", "error": "stubbed by e2e"})))
    tab(page, "lookbook")
    page.locator("[data-lb-browse]").click()
    page.locator("[data-room-all]").first.click()
    page.locator("#room [data-room-try]").click()
    step = page.locator("#tryon-room .model-step")
    with page.expect_file_chooser() as fc:
        step.locator("[data-model-pick]").click()
    fc.value.set_files(photo("fullbody", BLUE))
    step.locator("[data-model-create]").click()
    step.locator("[data-model-save]").click()
    page.wait_for_function("document.querySelector('#tryon-room').textContent.includes('stubbed')")

    tab(page, "profile")
    page.locator("#my-model [data-model-delete]").wait_for()
    assert page.locator("#my-model .model-shot img").get_attribute("src").startswith("/api/body-models/")


def test_lookbook_pieces_link_to_shops_and_outfits_shelve_by_season(page):
    """Feedback #34/#36: a Lookbook outfit is one row of pieces, outer layer first, each with a "$54.50 · ASOS ↗"
    shop link (no board); My Look Book → Outfits files saved looks under season shelves, empty seasons left out."""
    tab(page, "lookbook")
    with page.expect_response(lambda r: "/lookbook?" in r.url and r.ok) as resp:
        page.locator("[data-lb-browse]").click()
    first = next(o for s in resp.value.json()["sections"] for o in s["outfits"])
    card = page.locator("#lb-sections .outfit").first
    card.wait_for()
    assert card.locator(".board, .flatlay").count() == 0, "no board or collage on the Lookbook tab"
    cells = card.locator(".piece-row .piece-cell")
    assert cells.count() == len(first["pieces"])
    boxes = [cells.nth(i).bounding_box() for i in range(cells.count())]
    assert len({round(b["y"]) for b in boxes}) == 1, "one row"
    assert len({round(b["width"]) for b in boxes}) == 1, "equal-size cards"
    assert [b["x"] for b in boxes] == sorted(b["x"] for b in boxes)
    order = ["outerwear", "top", "dress", "bottom", "shoes", "bag", "accessory"]
    shown = sorted(first["pieces"], key=lambda p: order.index(p["category"]) if p["category"] in order else 99)
    for i, p in enumerate(shown):
        link = cells.nth(i).locator("a.shop-link")
        assert re.fullmatch(rf"\${p['price']:.2f} · (ASOS|SHEIN|Amazon) ↗", link.inner_text().strip()), link.inner_text()
        assert link.get_attribute("href").startswith("https://") and link.get_attribute("target") == "_blank"

    saved = []
    for _ in range(2):
        saved.append(page.locator("#lb-seasons .chip.on").get_attribute("data-season"))
        with page.expect_response(lambda r: r.url.endswith("/outfits") and r.request.method == "POST"):
            page.locator("#lb-sections .save-outfit").first.click()
        other = next(c for c in page.locator("#lb-seasons .chip").all() if c.get_attribute("data-season") not in saved)
        with page.expect_response(lambda r: "/lookbook?" in r.url and r.ok):
            other.click()
        page.locator("#lb-sections .outfit").first.wait_for()

    tab(page, "style")
    page.locator("#book-tabs button", has_text="Outfits").click()
    page.locator("#book-contents .book-shelf").first.wait_for()
    order = ["spring", "summer", "autumn", "winter"]
    shelves = page.locator("#book-contents .book-shelf")
    assert [s.get_attribute("data-shelf") for s in shelves.all()] == sorted(saved, key=order.index), \
        "one shelf per season with outfits, in season order"
    for s in shelves.all():
        assert s.locator(".book-thumb-look").count() == 1
    assert page.locator("#book-contents .book-add").count() == 1

    # The saved look's flat-lay page has the same shop links.
    shelves.first.locator(".book-thumb-look").click()
    page.locator("#book-page .bk-flat .bk-lab a.shop-link").first.wait_for()
    assert not page.errors, page.errors


def test_try_on_and_my_model_pictures_open_full_size(page):
    """Feedback #35: the try-on polaroid, the Daily Look hero and My model open full size; ✕, Esc or a click
    outside closes it."""
    if LIVE_URL:
        pytest.skip("creates a model on the server; offline only")
    tab(page, "profile")
    box = page.locator("#my-model")
    with page.expect_file_chooser() as fc:
        box.locator("[data-model-pick]").click()
    fc.value.set_files(photo("fullbody", BLUE))
    box.locator("[data-model-create]").click()
    with page.expect_response(lambda r: r.url.endswith("/save") and r.ok):
        box.locator("[data-model-save]").click()
    shot = box.locator(".model-shot img")
    shot.wait_for()
    src = shot.get_attribute("src")

    lightbox = page.locator("#lightbox")
    for close in ("Escape", "button", "outside"):
        shot.click()
        lightbox.wait_for()
        assert lightbox.locator("img").get_attribute("src") == src
        lightbox.locator("img").click()  # a click on the picture itself keeps it open
        assert lightbox.is_visible()
        if close == "Escape":
            page.keyboard.press("Escape")
        elif close == "button":
            lightbox.locator(".lightbox-close").click()
        else:
            page.mouse.click(5, 450)
        lightbox.wait_for(state="detached")

    # The try-on result ("you, in this look") opens the same way.
    page.route("**/api/users/*/tryons", lambda route: route.fulfill(
        status=202, content_type="application/json", body=json.dumps({"id": "e2e-stub"})))
    page.route("**/api/tryons/e2e-stub", lambda route: route.fulfill(content_type="application/json", body=json.dumps(
        {"id": "e2e-stub", "status": "done", "image_url": src,
         "result": {"rendered": True, "rendered_ids": [], "described_ids": []}})))
    tab(page, "lookbook")
    page.locator("[data-lb-browse]").click()
    page.locator("[data-room-all]").first.click()
    page.locator("#room [data-room-try]").click()
    result = page.locator("#tryon-room .tryon-shot:not(.rendering) img")
    result.wait_for()
    result.click()
    lightbox.wait_for()
    assert lightbox.locator("img").get_attribute("src") == src
    page.keyboard.press("Escape")
    lightbox.wait_for(state="detached")
    assert page.errors == []



def test_the_fitting_room_is_a_side_panel_on_wide_screens_and_a_bottom_bar_on_phones(page):
    """Feedback #36: at 1100px and wider the fitting room docks beside the content without covering it."""
    tab(page, "lookbook")
    page.locator("[data-lb-browse]").click()
    page.locator("[data-room-all]").first.click()
    room = page.locator("#room")
    room.wait_for()
    tray, content = room.bounding_box(), page.locator("#lb-sections").bounding_box()
    assert tray["height"] > tray["width"], "a vertical panel"
    assert tray["x"] >= content["x"] + content["width"], "beside the content, not over it"
    kids = [room.locator(sel).bounding_box()["y"] for sel in (".room-total", ".room-pieces", "[data-room-try]", "[data-room-clear]")]
    assert kids == sorted(kids), "total, pieces, Try it on me, clear: top to bottom"
    thumbs = room.locator(".room-piece")
    assert thumbs.count() > 1 and thumbs.nth(1).bounding_box()["y"] > thumbs.nth(0).bounding_box()["y"], "stacked"

    page.set_viewport_size({"width": 390, "height": 844})
    tray = room.bounding_box()
    assert tray["width"] > tray["height"] and tray["y"] + tray["height"] > 844 - 40, "the bottom bar on a phone"
