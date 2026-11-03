"""Where a record's attribute check ended: agree, different, unsettled or not checked.

A record seen in a photo is a record whose object is there. Whether the city's yes/no field for it still holds
([items.<id>.compare] in a city's file) is a second question that eval.record_check asks of the photo, and its answer
can be a no. This names the end of that second question, so what is left open is carried to the Asset Check instead of
being counted with the records that were settled.
"""

AGREE, DIFFERENT, UNSETTLED, NOT_CHECKED = "agree", "different", "unsettled", "not_checked"
STATES = (AGREE, DIFFERENT, UNSETTLED, NOT_CHECKED)
OPEN = (UNSETTLED, NOT_CHECKED)  # an attribute nobody has settled


def _said(answer: dict | None) -> str | None:
    """A model's yes or no about something it could see; anything else settles nothing."""
    if answer and answer.get("visible") is True and answer.get("answer") in ("yes", "no"):
        return answer["answer"]
    return None


def state_of(row: dict) -> dict:
    """The state of one asked record (a row of record_check_scan<N>.json), from the answers it holds, and the reason it
    stayed unsettled: unclear, not_visible, error, not_asked (the cost cap), models_disagree or second_model_error."""
    spark, other = row.get("spark") or {}, row.get("open")
    if row.get("differs"):
        return {"state": DIFFERENT, "reason": ""}
    said = _said(spark)
    if said is not None and said == row.get("city_value"):
        return {"state": AGREE, "reason": ""}
    if spark.get("answer") == "not_asked":
        reason = "not_asked"
    elif spark.get("answer") == "error" or not spark:
        reason = "error"
    elif said is None:
        reason = "not_visible" if spark.get("visible") is False else "unclear"
    elif other is None or other.get("answer") == "error":
        reason = "second_model_error"
    else:
        reason = "models_disagree"
    return {"state": UNSETTLED, "reason": reason}
