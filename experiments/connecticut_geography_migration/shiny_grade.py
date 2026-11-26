"""Exercise the Shiny application's county queries and CSV through its browser UI."""

import argparse
import csv
import json
import math
from pathlib import Path
import re
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from playwright.sync_api import TimeoutError as BrowserTimeout, sync_playwright


MEASURES = {"23": ("Unemployment", 100, 0.051), "51": ("Population", 1, 0)}
MISSING = re.compile(r"^(?:na|n/a|none|no (?:local )?data(?: available)?|[-–—])?$", re.I)


def pick(page, field, label, *, optional=False):
    select = page.locator(f"#{field}")
    select.locator("xpath=self::*[contains(@class, 'shiny-bound-input')]").wait_for(state="attached", timeout=12000)
    if "semantic-select-input" in (select.get_attribute("class") or ""):
        options = select.locator(".menu .item")
        choice = options.filter(has_text=re.compile(r"^" + re.escape(label) + r"$"))
        if optional and not choice.count():
            return False
        if select.locator(".text").first.inner_text().strip() != label:
            select.click()
            choice.click()
        return True
    control = select.locator("xpath=following-sibling::div[contains(@class, 'selectize-control')][1]")
    if not control.count():
        options = [element.inner_text().strip() for element in select.locator("option").all()]
        if label not in options and optional:
            return False
        select.select_option(label=label)
        return True
    selected = control.locator(".selectize-input > div[data-value]")
    if selected.count() and selected.inner_text().strip() == label:
        return True
    control.locator(".selectize-input").click()
    options = control.locator(".selectize-dropdown-content [data-selectable]")
    choices = [element.inner_text().strip() for element in options.all()]
    if label not in choices:
        # The original application uses Selectize's open dropdown for all filters.
        if not optional:
            raise RuntimeError(f"{field} option {label!r} unavailable: {choices}")
        page.keyboard.press("Escape")
        return False
    options.get_by_text(label, exact=True).click()
    return True


def read_view(page):
    tables = page.locator("#category_tables_ui")
    tables.locator("table").first.wait_for(state="attached", timeout=12000)
    for summary in tables.locator("details > summary").all():
        if summary.locator("xpath=..").get_attribute("open") is None:
            summary.click()
    values = {}
    for mid, (label, _, _) in MEASURES.items():
        row = tables.get_by_text(label.rstrip("*"), exact=False).filter(visible=True)
        name = re.compile(r"^" + re.escape(label) + r"\**$")
        measure = [
            node for node in row.all()
            if name.fullmatch(node.inner_text().strip())
            and node.locator("xpath=ancestor::tr[1]").count()
        ]
        # A linked measure name can appear on both the anchor and its strong element.
        if measure:
            cells = measure[0].locator("xpath=ancestor::tr[1]").locator('td[headers~="value_ci"]')
            if cells.count():
                values[mid] = cells.first.inner_text().strip()
    return values


def run_case(page, case, temp):
    pick(page, "state", case["state_label"])
    if not pick(page, "year", case["year_label"], optional=True):
        pick(page, "year", str(case["year"]))
    title = page.locator("#location_header_ui")
    title.filter(has_text=re.compile(re.escape(case["state_label"]) + r"\s+" + str(case["year"]))).wait_for(timeout=12000)
    names = [case["county_label"], case["county_label"].removesuffix(" County")]
    chosen = next((label for label in dict.fromkeys(names) if pick(page, "county", label, optional=True)), None)
    if chosen is None:
        choices = page.locator("#county .menu .item").all_text_contents()
        return {"county_available": False, "available_options": choices}
    title.filter(has_text=re.compile(re.escape(chosen) + r".*\b" + str(case["year"]))).wait_for(
        timeout=12000
    )
    page.locator("#download_data").filter(has_text=re.compile(re.escape(chosen) + r".*" + str(case["year"]))).wait_for(
        timeout=12000
    )
    with page.expect_download(timeout=12000) as download:
        page.locator("#download_data").click()
    path = temp / "observation.csv"
    download.value.save_as(path)
    with path.open(encoding="utf-8", newline="") as handle:
        records = [row for row in csv.DictReader(handle) if row["measure_id"] in MEASURES]
    return {"county_available": True, "display": read_view(page), "records": records}


def metric_check(observed, case, mid):
    expected = case["values"][mid]
    label, scale, tolerance = MEASURES[mid]
    if not observed.get("county_available"):
        return False, "county missing from selection"
    records = [record for record in observed["records"] if record["measure_id"] == mid]
    if len(records) != 1:
        return False, f"expected one exported {label} row, got {len(records)}"
    record = records[0]
    fips = record["state_fips"].zfill(2) + record["county_fips"].zfill(3)
    if fips != case["fips"]:
        return False, f"export returned FIPS {fips}"
    display = observed["display"].get(mid, "")
    value = record["raw_value"]
    if expected is None:
        return (value in ("", "NA") and bool(MISSING.fullmatch(display)), f"UI={display!r}, CSV={value!r}")
    if mid not in observed["display"]:
        return False, f"{label} missing from display"
    try:
        displayed_number = float(re.sub(r"[,\s%]", "", display))
        matching_raw = math.isclose(float(value), expected, rel_tol=0, abs_tol=1e-9)
    except ValueError:
        return False, f"UI={display!r}, CSV={value!r}"
    matching_display = math.isclose(displayed_number, expected * scale, rel_tol=0, abs_tol=tolerance)
    return (matching_raw and matching_display, f"UI={display!r}, CSV={value!r}")


def observe(workspace, rscript, cases):
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    url = f"http://127.0.0.1:{port}/"
    command = [rscript, "--vanilla", "-e", f'shiny::runApp(".", port={port}, host="127.0.0.1", launch.browser=FALSE)']
    with TemporaryDirectory() as folder, sync_playwright() as playwright:
        temp = Path(folder)
        with (temp / "r.log").open("w+") as log:
            server = subprocess.Popen(command, cwd=workspace, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        break
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                            break
                    except OSError:
                        time.sleep(0.25)
                else:
                    raise RuntimeError("Shiny did not open a local listener")
                if server.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(f"Shiny terminated before browser checks: {log.read()[-5000:]}")

                browser = playwright.chromium.launch(executable_path="/opt/google/chrome/chrome")
                context = browser.new_context(accept_downloads=True)
                observations = {}
                for case in cases:
                    page = context.new_page()
                    page.set_default_timeout(8000)
                    page.goto(url, timeout=12000)
                    try:
                        result = run_case(page, case, temp)
                    except (BrowserTimeout, RuntimeError, KeyError) as error:
                        result = {"query_error": str(error)}
                    observations[case["id"]] = result
                    page.close()
                browser.close()
                log.seek(0)
                server_tail = log.read()[-5000:]
            finally:
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()
    return {"observations": observations, "server_log_tail": server_tail}


def grade(observed, oracle):
    checks = {}
    for case in oracle:
        result = observed["observations"][case["id"]]
        for mid in case["values"]:
            passed, detail = metric_check(result, case, mid)
            checks[f"{case['id']}/{MEASURES[mid][0]}"] = {
                "passed": passed, "detail": result.get("query_error", detail),
            }
    return {"total": len(checks), "passed": sum(value["passed"] for value in checks.values()),
            "checks": checks, **observed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("rscript")
    args = parser.parse_args()
    cases = json.load(sys.stdin)
    print(json.dumps(observe(args.workspace, args.rscript, cases), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
