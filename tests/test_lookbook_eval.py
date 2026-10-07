"""The Lookbook evaluation harness: season agreement and outfit checks. Runs offline with FakeVision."""

from types import SimpleNamespace

import pytest

from lookmate.llm.client import FakeVision
from lookmate.lookbook_eval import PhotoResult, check_outfits, evaluate_photo, run, summarise, to_markdown
from lookmate.services.lookbook import Palette

from .e2e.conftest import png


def piece(name, colour):
    return {"name": name, "colour": colour}


def outfit(title, *pieces, reviewed=True):
    return {"title": title, "pieces": list(pieces), "reviewed": reviewed}


def test_avoided_colours_and_two_bright_pieces_are_flagged():
    palette = Palette.from_analysis({"best_colours": [{"name": "dusty pink"}, {"name": "navy"}],
                                     "avoid_colours": [{"name": "orange"}]})
    calm = outfit("calm", piece("blouse", "pink"), piece("jeans", "blue"), piece("loafers", "black"))
    loud = outfit("loud", piece("blazer", "green"), piece("trousers", "blue"), piece("top", "orange"))
    off, two = check_outfits([calm, loud], palette)
    assert off == ["orange top"]
    assert two == ["loud"]  # denim jeans in "calm" are a neutral, not a second bright


def test_agreement_is_the_share_of_photos_with_the_most_common_season():
    results = [PhotoResult("a", "summer", "soft summer", "cool"), PhotoResult("b", "summer", "soft summer", "cool"),
               PhotoResult("c", "autumn", "soft autumn", "warm"), PhotoResult("d", error="no face found")]
    s = summarise(results, season="summer", undertone="warm")
    assert s["season"] == ("summer", 2) and s["ok"] == 3 and s["errors"] == 1
    assert s["season_accuracy"] == 2 / 3 and s["undertone_accuracy"] == 1 / 3
    md = to_markdown(results, s, "fake", "summer", "warm")
    assert "| Same colour season every time (most common: summer) | 67% | 2 of 3 |" in md
    assert "| d | error |" in md and "Would you wear them?" in md


def test_a_photo_is_analysed_then_built_into_a_lookbook_offline():
    lb = {"sections": [{"outfits": [outfit("a", piece("cardigan", "cream"), piece("skirt", "red")),
                                    outfit("b", piece("tee", "white"), reviewed=False)]}]}
    res = evaluate_photo("me.png", png((200, 170, 150)), "image/png", FakeVision(), lambda analysis: (lb, 3))
    assert not res.error and res.season and res.undertone
    assert (res.approved, res.reviewed, len(res.outfits)) == (1, 3, 2)


def test_a_failing_photo_is_reported_and_the_run_goes_on(tmp_path):
    (tmp_path / "a.png").write_bytes(png((10, 10, 10)))
    (tmp_path / "b.png").write_bytes(png((200, 200, 200)))
    (tmp_path / "notes.txt").write_text("not a photo")
    broken = SimpleNamespace(analyze_person=lambda photos: (_ for _ in ()).throw(RuntimeError("timeout")))
    results = run(tmp_path, broken, lambda a: ({"sections": []}, 0))
    assert [r.image for r in results] == ["a.png", "b.png"]
    assert all(r.error == "analysis failed: timeout" for r in results)


def test_labels_score_the_photo_checks_and_the_clear_face_photos(tmp_path):
    from lookmate.lookbook_eval import load_labels

    cases = tmp_path / "cases.csv"
    cases.write_text("image,framing,good_for_colour,good_for_tryon,lighting,notes\n# comment,,,,,\n"
                     "a.png,Full body,yes,yes,daylight,\nb.png,full_body,no,,night,sunglasses\n", encoding="utf-8")
    labels = load_labels(cases)
    assert labels["a.png"] == {"framing": "full_body", "good_for_colour": True, "good_for_tryon": True,
                               "notes": "daylight"}
    assert labels["b.png"]["good_for_tryon"] is None and labels["b.png"]["notes"] == "night; sunglasses"
    (tmp_path / "a.png").write_bytes(png((200, 170, 150)))
    (tmp_path / "b.png").write_bytes(png((20, 20, 20)))
    results = run(tmp_path, FakeVision(), lambda a: ({"sections": []}, 0), labels)
    # Offline, FakeVision calls every lone photo full body, good for colour and for try-on.
    assert results[1].wrong_checks() == ["good_for_colour"]
    s = summarise(results)
    assert s["checks"] == {"framing": (1.0, 2), "good_for_colour": (0.5, 2), "good_for_tryon": (1.0, 1)}
    assert s["clear"] == 1 and s["clear_season"][1] == 1
    md = to_markdown(results, s, "fake")
    assert "| Photo check right: \"good for colour\" | 50% | 2 labelled |" in md
    assert "wrong photo check: good_for_colour" in md and "| night; sunglasses |" in md


def test_a_bad_framing_label_is_refused(tmp_path):
    from lookmate.lookbook_eval import load_labels

    cases = tmp_path / "cases.csv"
    cases.write_text("image,framing\na.png,legs\n", encoding="utf-8")
    with pytest.raises(ValueError, match="framing"):
        load_labels(cases)


def test_a_long_run_reports_each_photo_as_it_goes(tmp_path):
    (tmp_path / "a.png").write_bytes(png((200, 170, 150)))
    lines = []
    run(tmp_path, FakeVision(), lambda a: ({"sections": [{"outfits": [outfit("x", piece("tee", "white"))]}]}, 1),
        progress=lines.append)
    assert lines[0] == "[1/1] a.png: analysing..."
    assert lines[1].startswith("[1/1] a.png: ") and "1 outfits" in lines[1]
