"""Recheck the Connecticut geography migration on published release assets."""

import argparse
import json
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from openpyxl import load_workbook
from playwright.sync_api import sync_playwright


CASES = (
    (2024, "fairfield", "09001", "4.1%", "959,768"),
    (2025, "fairfield", "09001", "3.9%", None),
    (2025, "greater-bridgeport", "09120", None, "327,651"),
)
HOSTS = re.compile(r"^https://(?:www|api)\.countyhealthrankings\.org/")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def check_browser(evidence, chromium, capture):
    archive = evidence / "official_pages.har"
    if capture and archive.exists():
        raise FileExistsError(archive)
    options = {"service_workers": "block", "offline": not capture}
    if capture:
        options.update(record_har_path=archive, record_har_url_filter=HOSTS, record_har_mode="full")

    observations = {}
    with TemporaryDirectory() as temp, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium)
        context = browser.new_context(**options)

        def route_public_requests(route):
            if HOSTS.match(route.request.url) and route.request.resource_type not in {"image", "font", "media"}:
                route.continue_()
            else:
                route.abort()

        if capture:
            context.route("**/*", route_public_requests)
        else:
            recorded = read_json(archive)
            entries = recorded["log"]["entries"]
            # Playwright leaves requests with status -1 permanently pending during HAR replay.
            recorded["log"]["entries"] = [entry for entry in entries if entry["response"]["status"] > 0]
            replay_har = Path(temp) / "replay.har"
            replay_har.write_text(json.dumps(recorded), encoding="utf-8")
            context.route_from_har(replay_har, not_found="abort")

        for year, slug, fips, unemployment, population in CASES:
            page = context.new_page()
            url = f"https://www.countyhealthrankings.org/health-data/connecticut/{slug}?year={year}"
            endpoint = f"https://api.countyhealthrankings.org/county/{year}/{fips[:2]}/{slug}"
            hosted_path = f"/measures/{year}/{fips[:2]}/{fips[2:]}"
            with page.expect_response(endpoint, timeout=20000) as county_response, page.expect_response(
                lambda response: urlsplit(response.url).path == hosted_path, timeout=20000
            ) as measures_response:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            raw = county_response.value.json()["county"]
            assert county_response.value.ok and measures_response.value.ok

            snapshot = page.locator("#county-snapshot")
            assert snapshot.get_attribute("data-state-fips") == fips[:2]
            assert snapshot.get_attribute("data-county-fips") == fips[2:]
            unemployment_label = snapshot.get_by_text("Unemployment", exact=True)
            unemployment_label.wait_for(timeout=15000)
            unemployment_row = unemployment_label.locator("xpath=ancestor::tr[1]")
            unemployment_cells = [cell.strip() for cell in unemployment_row.locator("td").all_inner_texts()]
            county_unemployment = unemployment_cells[-3]
            assert county_unemployment == (unemployment or ""), unemployment_cells
            raw_population = next(item for item in raw["demographics"] if item["id"] == 51)["county_value"]
            assert raw_population == (population or "&nbsp;")
            if population:
                toggle = snapshot.get_by_role("button", name="Show demographic data", exact=True)
                if toggle.count():
                    toggle.click()
                population_row = snapshot.locator("tr").filter(has_text=re.compile(r"^Population\s+\*\*"))
                assert population in [cell.strip() for cell in population_row.locator("td").all_inner_texts()]
            else:
                assert not snapshot.get_by_role("button", name="Show demographic data", exact=True).count()

            observations[f"{year}/{fips}"] = {
                "url": url,
                "county_endpoint": endpoint,
                "measures_path": hosted_path,
                "unemployment_display": county_unemployment or None,
                "population_display": population,
            }
            page.wait_for_load_state("networkidle", timeout=15000)
            page.close()
        context.close()
        browser.close()
    return observations


def check_releases(evidence):
    observations = {}
    for year, slug, fips, unemployment, population in CASES:
        row = 4 if fips == "09001" else 13
        unemployment_column, population_column = ("FZ", "LK") if year == 2024 else ("FX", "OE")
        select = "Select Measure Data"
        additional = "Additional Measure Data"
        workbook = load_workbook(evidence / f"{year}.xlsx", read_only=True)
        assert workbook[select][f"A{row}"].value == workbook[additional][f"A{row}"].value == fips
        address = f"{unemployment_column}{row}"
        unemployment_value = workbook[select][address].value
        population_address = f"{population_column}{row}"
        population_value = workbook[additional][population_address].value
        assert unemployment == (f"{unemployment_value:.1f}%" if unemployment_value is not None else None)
        assert population == (f"{population_value:,}" if population_value is not None else None)
        workbook.close()

        county = read_json(evidence / f"official_api_county_{year}_{slug}.json")["county"]
        population_api = next(item for item in county["demographicData"].values() if item["id"] == 51)
        unemployment_api = next(
            item
            for category in county["categoryArray"].values()
            for component in category["components"]
            for item in (component.get("measures") or {}).values()
            if item["id"] == 23
        )
        assert (population_api["raw_value"], unemployment_api["raw_value"]) == (population_value, unemployment_value)
        if unemployment_value is not None:
            computed_rate = 100 * unemployment_api["numerator"] / unemployment_api["denominator"]
            assert math.isclose(unemployment_value, computed_rate, abs_tol=1e-9)
        observations[f"{year}/{fips}"] = {
            "unemployment_cell": f"{select}!{address}",
            "unemployment_percent": unemployment_value,
            "population_cell": f"{additional}!{population_address}",
            "population": population_value,
        }
    assert read_json(evidence / "official_api_county_2024_greater-bridgeport.json")["county"] == {}
    return observations


def check_maps(evidence, releases):
    observations = {}
    for year, expected_size in ((2024, 8), (2025, 9)):
        original = read_json(evidence / f"arcgis_{year}_orig_ct.json")
        replacement = read_json(evidence / f"arcgis_{year}_ct.json")
        attributes = [feature["attributes"] for feature in original["features"]]
        rows = {row["fipscode"]: row for row in attributes}
        assert len(rows) == expected_size == len(original["features"])
        assert original["features"] == replacement["features"]
        item_data = read_json(evidence / f"arcgis_{year}_orig_itemdata.json")
        county_layer = next(layer for layer in item_data["layers"] if layer["id"] == 2)
        fields = {field["fieldName"]: field for field in county_layer["popupInfo"]["fieldInfos"]}
        assert fields["v023_rawvalue"]["format"]["places"] == 1
        assert fields["v051_rawvalue"]["format"]["places"] == 0

        fips = "09001" if year == 2024 else "09120"
        expected = releases[f"{year}/{fips}"]
        assert rows[fips]["v051_rawvalue"] == expected["population"]
        if year == 2024:
            assert "09120" not in rows
            assert math.isclose(rows[fips]["v023_rawvalue"], expected["unemployment_percent"], abs_tol=1e-9)
        else:
            assert "09001" not in rows
            assert all(row["v023_rawvalue"] is None for row in rows.values())
        observations[str(year)] = {"feature_count": len(rows), "rows": rows}
    return observations


def check_shiny_lookup(evidence):
    import duckdb
    import pyreadr

    choices = pyreadr.read_r(str(evidence / "county_fips.rds"))[None]
    choices = choices[choices["state"] == "CT"]
    choices = dict(zip(choices["county"], choices["fipscode"]))
    assert choices.get("Greater Bridgeport Planning Region") == "09120"
    assert "Fairfield County" not in choices

    source = (evidence / "app.R").read_text(encoding="utf-8")
    county = source.split("  county_df <- reactive({", 1)[1].split("  state_df <- reactive({", 1)[0]
    query = re.search(r'query <- sprintf\("([^"]+)"', county).group(1).replace("'%s'", "?")
    connection = duckdb.connect()
    observations = {}
    for year in (2024, 2025):
        for fips in ("09001", "09120"):
            records = connection.execute(query, [str(evidence / f"snapshot_{year}.parquet"), fips[:2], fips[2:]]).fetchall()
            raw = {row[0]: row[3] for row in records}
            observations[f"{year}/{fips}"] = {
                "in_dropdown": fips in choices.values(),
                "sql_row_count": len(records),
                "sql_unemployment_raw": raw.get(23),
                "sql_population": raw.get(51),
            }
    connection.close()
    assert observations["2024/09001"]["sql_row_count"] == 85
    assert observations["2024/09120"]["sql_row_count"] == 0
    assert math.isclose(observations["2025/09001"]["sql_unemployment_raw"], 0.038658799335, abs_tol=1e-10)
    assert observations["2025/09120"]["sql_population"] == 327651
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--chromium", default="/usr/bin/google-chrome")
    parser.add_argument("--capture", action="store_true", help="Record the publisher's current public pages and assets.")
    args = parser.parse_args()
    releases = check_releases(args.evidence)
    result = {
        "browser_mode": "capture" if args.capture else "offline",
        "release_workbook_api_comparison": releases,
        "gis_feature_replay": check_maps(args.evidence, releases),
        "shiny_sql_replay": check_shiny_lookup(args.evidence),
        "official_snapshot_browser": check_browser(args.evidence, args.chromium, args.capture),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
