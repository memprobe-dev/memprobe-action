import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import report


ANALYSIS = {
    "filename": "firmware.elf",
    "total_flash": 412048,
    "total_ram": 86320,
    "build_id": 27,
}

CHECK_FAIL = {
    "passed": False,
    "violations": [
        {"kind": "flash", "label": "Flash", "budget": 393216,
         "actual": 412048, "overage": 18832},
    ],
}

DIFF = {
    "flash_delta": 2048,
    "ram_delta": -512,
    "passed": True,
    "symbol_diffs": [
        {"name": "parse_json", "old_size": 1200, "new_size": 1712, "delta": 512},
        {"name": "unchanged", "old_size": 64, "new_size": 64, "delta": 0},
    ],
    "regressions": [],
}


def test_render_sizes_only():
    body = report.render(ANALYSIS, {}, None, None, report.marker_for("firmware.elf"))
    assert report.marker_for("firmware.elf") in body
    assert "`firmware.elf`" in body
    assert "402.4 KB" in body
    assert "84.3 KB" in body
    assert "Change" not in body
    assert "build 27" in body


def test_render_regions_show_percentages():
    body = report.render(ANALYSIS, {"flash": 1048576}, None, None, report.marker_for("firmware.elf"))
    assert "39.3% of 1.00 MB" in body


def test_render_budget_violation():
    body = report.render(ANALYSIS, {}, CHECK_FAIL, None, report.marker_for("firmware.elf"))
    assert "**Over budget:** Flash by 18.4 KB" in body


def test_render_budget_pass():
    body = report.render(ANALYSIS, {}, {"passed": True, "violations": []}, None, report.marker_for("firmware.elf"))
    assert "All budgets passed." in body


def test_render_diff_table():
    body = report.render(ANALYSIS, {}, None, DIFF, report.marker_for("firmware.elf"))
    assert "| Flash | 402.4 KB | +2.0 KB |" in body
    assert "| RAM | 84.3 KB | -512 B |" in body
    assert "`parse_json`" in body
    assert "unchanged" not in body


def test_human_sizes():
    assert report.human(512) == "512 B"
    assert report.human(2048) == "2.0 KB"
    assert report.human(-2048) == "-2.0 KB"
    assert report.human(3 * 1024 * 1024) == "3.00 MB"


def test_outputs_written(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    report.write_outputs(ANALYSIS, DIFF, True)
    text = out.read_text()
    assert "flash=412048" in text
    assert "flash-delta=2048" in text
    assert "ram-delta=-512" in text
    assert "passed=true" in text


def test_sticky_comment_updates_existing(monkeypatch, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7}}))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.delenv("GITHUB_API_URL", raising=False)

    marker = report.marker_for("firmware.elf")
    calls = []

    def fake_request(method, url, token, payload=None):
        calls.append((method, url, payload))
        if method == "GET":
            return [{"id": 99, "body": marker + "\nold"}]
        return {}

    monkeypatch.setattr(report, "gh_request", fake_request)
    report.post_comment(marker + "\nnew", marker)

    methods = [c[0] for c in calls]
    assert methods == ["GET", "PATCH"]
    assert "/issues/comments/99" in calls[1][1]
    assert calls[1][2] == {"body": marker + "\nnew"}


def test_render_file_diff_table():
    diff = dict(DIFF, file_diffs=[{"file": "app/src/ui/render.c", "delta": 512, "symbols": 1}])
    body = report.render(ANALYSIS, {}, None, diff, report.marker_for("firmware.elf"))
    assert "| Source file | Change |" in body
    assert "`src/ui/render.c`" in body
    assert "+512" in body


def test_render_footer_commit(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "abc1234def5678")
    body = report.render(ANALYSIS, {}, None, None, report.marker_for("firmware.elf"))
    assert "· abc1234" in body


def test_load_toml_tables_returns_three(tmp_path):
    budgets, regions, watch = report.load_toml_tables(tmp_path)
    assert budgets == {} and regions == {} and watch == {}
