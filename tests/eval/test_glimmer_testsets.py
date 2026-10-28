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
