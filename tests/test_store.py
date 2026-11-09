import json

import store


def test_interrupted_task_requires_review(tmp_path, monkeypatch):
    path = tmp_path / "plans.json"
    monkeypatch.setattr(store, "PLANS_PATH", path)

    plans = [
        {"id": "task_1", "status": store.STATUS_PUBLISHING},
        {"id": "task_2", "status": store.STATUS_SENT},
    ]
    path.write_text(json.dumps(plans), encoding="utf-8")

    assert store.reset_interrupted() == 1
    result = json.loads(path.read_text(encoding="utf-8"))

    assert result[0]["status"] == store.STATUS_NEEDS_REVIEW
    assert result[1]["status"] == store.STATUS_SENT


def test_claim_only_once(tmp_path, monkeypatch):
    path = tmp_path / "plans.json"
    monkeypatch.setattr(store, "PLANS_PATH", path)
    path.write_text(
        json.dumps([{"id": "task_1", "status": store.STATUS_PLANNED}]),
        encoding="utf-8",
    )

    first = store.claim_plan("task_1")
    second = store.claim_plan("task_1")

    assert first is not None
    assert first["status"] == store.STATUS_PUBLISHING
    assert second is None
