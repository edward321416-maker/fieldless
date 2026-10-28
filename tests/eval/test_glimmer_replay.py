from eval import glimmer_replay as replay


def scan():
    frames = [{"idx": i, "image_id": f"img{i}", "lat": 47.6, "lng": -122.3, "compass": 0.0,
               "captured_at": "2022-08-30T00:00:00Z"} for i in range(6)]
    rows = [{"id": 1, "issue_type": "pole", "frame_idx": 0, "object_label": "yes", "review_status": "auto",
             "corrections": [], "model_outputs": {"check": {"answer": "yes", "box": [400, 0, 600, 900]}}},
            {"id": 2, "issue_type": "pole", "frame_idx": 2, "object_label": "unclear", "review_status": "needs_review",
             "corrections": [], "model_outputs": {"check": {"answer": "unclear", "box": []}}},
            {"id": 3, "issue_type": "tree", "frame_idx": 4, "object_label": "yes", "review_status": "auto",
             "corrections": [], "model_outputs": {"check": {"answer": "yes", "box": []}}}]
    return {"frames": frames, "issues": rows}


def test_picks_are_the_rows_and_the_dropped_photos_with_sparks_answer():
    got = replay.picks(scan(), {"pole": {"not_target": [5, 1]}}, "pole")
    assert [(p["idx"], p["spark"], p["row"]["id"] if p["row"] else None) for p in got] == \
        [(0, "yes", 1), (1, "no", None), (2, "unclear", 2), (5, "no", None)]


def test_the_flow_keeps_glimmers_decision_and_falls_back_to_spark_when_glimmer_is_unclear():
    assert replay.cascade("yes", "no") == "yes" and replay.cascade("no", "yes") == "no"
    assert replay.cascade("unclear", "yes") == "yes" and replay.cascade("unclear", "unclear") == "unclear"


def test_agreement_counts_only_photos_both_decided():
    out = replay.agreement([("yes", "yes"), ("no", "yes"), ("unclear", "yes"), ("yes", "unclear"), ("no", "no")])
    assert out["both_decided"] == 3 and out["agree"] == 2 and out["share"] == round(2 / 3, 3)
    assert out["glimmer_unclear_share"] == 0.2 and out["table"][("no", "yes")] == 1


def test_replaced_rows_take_the_flows_answer_and_glimmers_box_and_add_rows_for_dropped_photos():
    p = replay.picks(scan(), {"pole": {"not_target": [5]}}, "pole")
    answers = {0: {"answer": "no", "visible": True, "box": []},
               2: {"answer": "unclear", "visible": True, "box": []},
               5: {"answer": "yes", "visible": True, "box": [100, 0, 200, 800]}}
    rows = replay.replaced_rows(p, answers, "pole")
    by_frame = {r["frame_idx"]: r for r in rows}
    assert by_frame[0]["object_label"] == "no"
    assert by_frame[2]["object_label"] == "unclear" and by_frame[2]["id"] == 2  # Glimmer unclear: Spark's answer kept
    assert by_frame[5]["object_label"] == "yes" and by_frame[5]["id"] < 0  # a new row; Spark had dropped this photo
    assert by_frame[5]["model_outputs"]["check"]["box"] == [100, 0, 200, 800]
    assert all(r["issue_type"] == "pole" and r["review_status"] != "rejected" for r in rows)


def test_the_gate_names_every_failed_rule():
    ok = replay.gate(spark_found=13, flow_found=12, spark_false=0, flow_false=1, agree_share=0.9, unclear_share=0.1)
    assert ok == {"pass": True, "reasons": []}
    bad = replay.gate(spark_found=13, flow_found=11, spark_false=0, flow_false=2, agree_share=0.8, unclear_share=0.4)
    assert not bad["pass"] and len(bad["reasons"]) == 4
    assert not replay.gate(13, 13, 0, 0, None, None)["pass"]  # nothing both decided: no evidence, no switch


def test_a_higher_reasoning_setting_changes_the_system_line_and_leaves_room_for_the_reasoning(monkeypatch, tmp_path):
    monkeypatch.setenv("GLIMMER_URL", "https://example.modal.run")
    monkeypatch.setenv("MODEL_API_KEY", "k")
    low, medium = replay.judge_for(tmp_path, "low"), replay.judge_for(tmp_path, "medium")
    assert low.system == "Reasoning strength: low" and low.max_tokens == 2000
    assert medium.system == "Reasoning strength: medium" and medium.max_tokens == 4000
    monkeypatch.delenv("GLIMMER_URL")
    import pytest
    with pytest.raises(SystemExit, match="GLIMMER_URL"):
        replay.judge_for(tmp_path)
