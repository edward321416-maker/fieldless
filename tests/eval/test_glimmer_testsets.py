from eval import glimmer_testsets as ts


def test_flow_false_counts_negatives_the_flow_keeps():
    spark = {"n1": "no", "n2": "yes", "n3": "unclear", "p1": "yes"}
    glimmer = {"n1": "yes", "n2": "unclear", "n3": "no", "p1": "yes"}
    # n1: Glimmer keeps it (false); n2: Glimmer unclear -> Spark yes (false); n3: Glimmer no; p1 is not a negative
    assert ts.flow_false(spark, glimmer, {"n1", "n2", "n3"}) == 2


def test_every_inventory_item_has_a_test_set_on_disk():
    from assist.testset import FOLDER
    from pipeline.survey_items import load
    standard = load("us")
    assert all(standard.check(item).inventory for item in ts.SETS)
    assert all((FOLDER / f"{name}.json").is_file() for name in ts.SETS.values())


def test_rescore_asks_glimmer_only_on_the_photos_sam_found_last_time():
    from types import SimpleNamespace
    prev = {"spark": {"p1": "yes", "p2": "no", "n1": "yes"}, "spark_kept": 1, "spark_false": 1}
    test_set = SimpleNamespace(visible=lambda: [SimpleNamespace(id="p1"), SimpleNamespace(id="p2"),
                                                SimpleNamespace(id="p3")],
                               negatives=lambda: [SimpleNamespace(id="n1")])
    said = {"p1": "yes", "p2": "yes", "n1": "unclear"}
    asked = []

    class Judge:
        def answer(self, check, photo):
            asked.append(photo.stem)
            return {"answer": said[photo.stem], "visible": True, "box": [], "note": ""}

    out = ts.rescore(prev, test_set, Judge(), check=None, photo=lambda i: __import__("pathlib").Path(f"{i}.jpg"))
    assert sorted(asked) == ["n1", "p1", "p2"]  # p3: SAM did not find it last time, so it is not asked
    assert out["glimmer_kept"] == 2 and out["flow_false"] == 1  # n1: Glimmer unclear -> Spark's yes
    assert out["glimmer"] == {"p1": "yes", "p2": "yes", "n1": "unclear"}
