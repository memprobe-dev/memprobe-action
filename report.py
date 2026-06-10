import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOP_CHANGES = 15


def marker_for(file):
    # one sticky comment per analyzed file, so matrix and multi-image
    # builds don't overwrite each other
    return f"<!-- memprobe-action:{file} -->"


def human(n):
    neg = "-" if n < 0 else ""
    n = abs(int(n))
    if n < 1024:
        return f"{neg}{n} B"
    if n < 1024 * 1024:
        return f"{neg}{n / 1024:.1f} KB"
    return f"{neg}{n / 1024 / 1024:.2f} MB"


def signed(n):
    return f"+{human(n)}" if n >= 0 else human(n)


def signed_bytes(n):
    return f"+{n:,}" if n >= 0 else f"{n:,}"


def usage(used, capacity):
    if not capacity:
        return human(used)
    pct = used / capacity * 100
    return f"{human(used)} ({pct:.1f}% of {human(capacity)})"


def fail(message):
    print(f"::error::{message}")
    sys.exit(1)


def run_cli(args, retries=1):
    for attempt in range(retries + 1):
        proc = subprocess.run(["memprobe", *args, "--json"],
                              capture_output=True, text=True)
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            data = None
        # a gate failure still produces JSON; no JSON means the call itself
        # failed, which is worth one retry for transient network errors
        if data is not None or attempt == retries:
            return proc.returncode, data, proc.stderr.strip()
        time.sleep(3)


def load_toml_tables(start):
    try:
        from memprobe_cli.budgets import load_budgets, load_regions
        return load_budgets(start), load_regions(start)
    except Exception:
        return {}, {}


def render(analysis, regions, check, diff, marker):
    name = analysis.get("filename") or "firmware.elf"
    flash = analysis.get("total_flash", 0)
    ram = analysis.get("total_ram", 0)
    lines = [marker, f"### Firmware size: `{name}`", ""]

    if diff is not None:
        lines += [
            "| | Size | Change |",
            "|---|---:|---:|",
            f"| Flash | {usage(flash, regions.get('flash'))} | {signed(diff.get('flash_delta', 0))} |",
            f"| RAM | {usage(ram, regions.get('ram'))} | {signed(diff.get('ram_delta', 0))} |",
        ]
    else:
        lines += [
            "| | Size |",
            "|---|---:|",
            f"| Flash | {usage(flash, regions.get('flash'))} |",
            f"| RAM | {usage(ram, regions.get('ram'))} |",
        ]

    if check is not None:
        if check.get("passed"):
            lines += ["", "✅ All budgets passed."]
        else:
            for v in check.get("violations", []):
                lines += ["", f"❌ {v.get('label', v.get('kind'))} over budget by "
                              f"{human(v.get('overage', 0))} "
                              f"({human(v.get('actual', 0))} > {human(v.get('budget', 0))})."]

    if diff is not None:
        for r in diff.get("regressions", []):
            lines += ["", f"❌ {str(r.get('metric', '')).upper()} grew "
                          f"{human(r.get('delta', 0))} (limit {signed(r.get('limit', 0))})."]
        changed = [s for s in diff.get("symbol_diffs", []) if s.get("delta")]
        if changed:
            lines += ["", "<details><summary>Largest symbol changes</summary>", "",
                      "| Symbol | Old | New | Change |", "|---|---:|---:|---:|"]
            for s in changed[:TOP_CHANGES]:
                lines.append(f"| `{s.get('name', '')}` | {s.get('old_size', 0):,} | "
                             f"{s.get('new_size', 0):,} | {signed_bytes(s.get('delta', 0))} |")
            lines += ["", "</details>"]

    tail = "[memprobe](https://memprobe.dev)"
    if analysis.get("build_id"):
        tail += f" · build {analysis['build_id']}"
    if diff is not None and diff.get("base_build_id"):
        tail += f" · compared against build {diff['base_build_id']}"
    lines += ["", f"<sub>{tail}</sub>"]
    return "\n".join(lines)


def gh_request(method, url, token, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "memprobe-action",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        print(f"::warning::GitHub API {method} {url} returned {exc.code}")
        return None


def post_comment(body, marker):
    if os.environ.get("GITHUB_EVENT_NAME") not in ("pull_request", "pull_request_target"):
        return
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not (token and repo and event_path):
        print("::warning::Missing token or event payload, skipping the PR comment.")
        return
    with open(event_path) as fh:
        event = json.load(fh)
    number = (event.get("pull_request") or {}).get("number")
    if not number:
        return
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    comments_url = f"{api}/repos/{repo}/issues/{number}/comments"
    mine = None
    for page in range(1, 11):
        batch = gh_request("GET", f"{comments_url}?per_page=100&page={page}", token) or []
        mine = next((c for c in batch if marker in (c.get("body") or "")), None)
        if mine or len(batch) < 100:
            break
    if mine:
        gh_request("PATCH", f"{api}/repos/{repo}/issues/comments/{mine['id']}",
                   token, {"body": body})
    else:
        gh_request("POST", comments_url, token, {"body": body})


def write_summary(body):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as fh:
            fh.write(body + "\n")


def write_outputs(analysis, diff, passed):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a") as fh:
        fh.write(f"flash={analysis.get('total_flash', 0)}\n")
        fh.write(f"ram={analysis.get('total_ram', 0)}\n")
        fh.write(f"flash-delta={diff.get('flash_delta', '') if diff else ''}\n")
        fh.write(f"ram-delta={diff.get('ram_delta', '') if diff else ''}\n")
        fh.write(f"passed={'true' if passed else 'false'}\n")


def main():
    file = os.environ.get("MP_FILE", "")
    base = os.environ.get("MP_BASE", "")
    project = os.environ.get("MP_PROJECT", "")
    fail_on = os.environ.get("MP_FAIL_ON", "")
    budget_flash = os.environ.get("MP_BUDGET_FLASH", "")
    budget_ram = os.environ.get("MP_BUDGET_RAM", "")
    want_comment = os.environ.get("MP_COMMENT", "true").strip().lower() != "false"

    if not file or not os.path.isfile(file):
        fail(f"Firmware file not found: {file or '(no file input)'}")

    # diff runs first: in project mode the server compares against the latest
    # saved build, which must not be the one analyze saves below
    diff = None
    diff_args = None
    if base:
        if not os.path.isfile(base):
            fail(f"Base file not found: {base}")
        diff_args = ["diff", base, file]
    elif project:
        diff_args = ["diff", file, "--project", project]
    if diff_args:
        if fail_on:
            diff_args += ["--fail-on", fail_on]
        code, diff, err = run_cli(diff_args)
        if diff is None:
            if not base and "No builds found" in err:
                print("No saved builds in the project yet, skipping the size diff.")
            else:
                fail(err or "memprobe diff failed.")

    args = ["analyze", file]
    if project:
        args += ["--project", project]
    code, analysis, err = run_cli(args)
    if code != 0 or analysis is None:
        fail(err or "memprobe analyze failed.")

    start = Path(file).resolve().parent
    budgets, regions = load_toml_tables(start)

    check = None
    if budget_flash or budget_ram or budgets:
        args = ["check", file]
        if budget_flash:
            args += ["--budget-flash", budget_flash]
        if budget_ram:
            args += ["--budget-ram", budget_ram]
        code, check, err = run_cli(args)
        if check is None:
            fail(err or "memprobe check failed.")

    passed = ((check is None or check.get("passed", True)) and
              (diff is None or diff.get("passed", True)))

    marker = marker_for(file)
    body = render(analysis, regions, check, diff, marker)
    write_summary(body)
    if want_comment:
        post_comment(body, marker)
    write_outputs(analysis, diff, passed)

    if not passed:
        for v in (check or {}).get("violations", []):
            print(f"::error::{v.get('label', v.get('kind'))} over budget by "
                  f"{human(v.get('overage', 0))}")
        for r in (diff or {}).get("regressions", []):
            print(f"::error::{str(r.get('metric', '')).upper()} grew "
                  f"{human(r.get('delta', 0))}, limit {signed(r.get('limit', 0))}")
        sys.exit(1)


if __name__ == "__main__":
    main()
