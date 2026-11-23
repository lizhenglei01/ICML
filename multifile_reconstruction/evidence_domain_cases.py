"""Prepare two count-based evidence cases from original Doc2DB-Bench tables."""

from collections import Counter
from pathlib import Path

from medical_cf.facts import intervene, validate as validate_medical
from schema_cf.input import load, validate


DATASET = Path(__file__).resolve().parents[1] / "三方依赖代码" / "Doc2DB-Bench" / "dataset"


def _declared_links(dataset, links):
    for source, target in links.items():
        table, column = source.split(".")
        schema = dataset.schemas[table]
        if schema["key_origin"] != "declared_schema" or schema["foreign_keys"].get(column) != target:
            raise ValueError(f"Missing declared relationship {source} → {target}")


def _provenance(dataset, tables, links, routes, metric_sources):
    names = {"schema.json", *(f"{table}.json" for table in tables)}
    return {
        "repository": "https://github.com/SetonLiang/Doc2DB-Bench",
        "case": dataset.provenance["input"],
        "files": [item for item in dataset.provenance["files"] if Path(item["path"]).name in names],
        "loader": "schema_cf.input.load → medical_cf.upstream.load → Doc2DB load_database_from_directory",
        "declared_links": links,
        "metric_sources": metric_sources,
        "event_routing": routes,
    }


def _labelled_counts(labels, gross, excluded, net):
    state = {
        metric: {label: counts[key] for key, label in sorted(labels.items())}
        for metric, counts in (("gross", gross), ("excluded", excluded), ("net", net))
    }
    if any(state["gross"][label] != state["excluded"][label] + state["net"][label]
           for label in labels.values()):
        raise ValueError("The independently counted categories do not partition the full count")
    return state


def _medical_state(rows, departments):
    affiliations = rows["Affiliated_With"]
    gross = Counter(row["Department"] for row in affiliations)
    excluded = Counter(row["Department"] for row in affiliations if row["PrimaryAffiliation"] == 0)
    net = Counter(row["Department"] for row in affiliations if row["PrimaryAffiliation"] == 1)
    return _labelled_counts(departments, gross, excluded, net)


def _medical_case():
    dataset = load(DATASET / "medical" / "case1")
    links = {
        "Affiliated_With.Physician": "Physician.EmployeeID",
        "Affiliated_With.Department": "Department.DepartmentID",
    }
    _declared_links(dataset, links)
    rows = dataset.rows
    validate_medical(rows, dataset.schemas)
    departments = {row["DepartmentID"]: row["Name"] for row in rows["Department"]}
    physicians = {row["EmployeeID"]: row["Name"] for row in rows["Physician"]}
    chinese = {1: "普通内科", 2: "外科", 3: "精神科"}
    states, events, routes = [_medical_state(rows, departments)], [], []
    for physician, source, destination in ((1, 1, 2), (2, 1, 3)):
        if not any(row["Physician"] == physician and row["Department"] == source and row["PrimaryAffiliation"] == 1
                   for row in rows["Affiliated_With"]):
            raise ValueError(f"Prior primary affiliation differs for employee {physician}")
        rows = intervene(rows, dataset.schemas, physician, destination)
        name = physicians[physician]
        events.append(
            f"将 {name} 医生的主要任职科室从{chinese[source]}调整为{chinese[destination]}。"
            f" Transfer Dr. {name}'s primary appointment from {departments[source]} to {departments[destination]}."
        )
        routes.append({"table": "Affiliated_With", "column": "Department",
                       "row_key_before": {"Physician": physician, "Department": source},
                       "row_key_after": {"Physician": physician, "Department": destination},
                       "predicate": {"PrimaryAffiliation": 1}, "old_value": source, "new_value": destination})
        states.append(_medical_state(rows, departments))
    return {
        "title": "科室医生任职复核 / Department physician affiliations",
        "labels": {"gross": "任职记录总数 / Total affiliation records",
                   "excluded": "非主要任职记录数 / Secondary affiliation records",
                   "net": "主要任职医生数 / Physicians with a primary appointment"},
        "unit": "条 / records",
        "states": states,
        "events": events,
        "provenance": _provenance(
            dataset, ("Affiliated_With", "Department", "Physician"), links, routes,
            {"group": "Affiliated_With.Department, represented by Department.Name",
             "gross": "Count all Affiliated_With rows",
             "excluded": "Count rows with PrimaryAffiliation = 0",
             "net": "Independently count rows with PrimaryAffiliation = 1",
             "constraint": "A physician has exactly one primary affiliation; the composite affiliation key is unique."},
        ),
    }


def _sports_state(rows, venues):
    venue_by_event = {row["ID"]: row["Stadium_ID"] for row in rows["event"]}
    joined = [(venue_by_event[row["Event_ID"]], row["Result"]) for row in rows["record"]]
    gross = Counter(venue for venue, _ in joined)
    excluded = Counter(venue for venue, result in joined if result == "NC")
    net = Counter(venue for venue, result in joined if result in {"Win", "Loss"})
    return _labelled_counts(venues, gross, excluded, net)


def _sports_case():
    dataset = load(DATASET / "sports" / "case1")
    links = {"record.Event_ID": "event.ID", "event.Stadium_ID": "stadium.ID"}
    _declared_links(dataset, links)
    rows = dataset.rows
    validate(rows, dataset.schemas)
    venues = {row["ID"]: row["name"] for row in rows["stadium"]}
    states, events, routes = [_sports_state(rows, venues)], [], []
    for event, source, destination in ((3, 2, 1), (1, 1, 3)):
        original = next(row for row in rows["event"] if row["ID"] == event)
        if original["Stadium_ID"] != source or source == destination:
            raise ValueError(f"Prior stadium differs or the move is a no-op for event {event}")
        rows = {**rows, "event": [
            {**row, "Stadium_ID": destination} if row["ID"] == event else row for row in rows["event"]
        ]}
        validate(rows, dataset.schemas)
        name = original["Name"]
        events.append(
            f"将 {name}（赛事 {event}）的举办场馆从 {venues[source]} 改为 {venues[destination]}。"
            f" Move {name} (event {event}) from {venues[source]} to {venues[destination]}."
        )
        routes.append({"table": "event", "column": "Stadium_ID", "row_key": {"ID": event},
                       "old_value": source, "new_value": destination})
        states.append(_sports_state(rows, venues))
    return {
        "title": "游泳赛事场馆成绩复核 / Swimming results by host venue",
        "labels": {"gross": "成绩记录总数 / Total result records",
                   "excluded": "NC 标记记录数 / NC-coded result records",
                   "net": "胜负成绩记录数 / Win/Loss result records"},
        "unit": "条 / records",
        "states": states,
        "events": events,
        "provenance": _provenance(
            dataset, ("record", "event", "stadium"), links, routes,
            {"group": "record.Event_ID → event.Stadium_ID, represented by stadium.name",
             "gross": "Count all joined result records",
             "excluded": "Count joined records with Result = NC",
             "net": "Independently count joined records with Result in {Win, Loss}"},
        ),
    }


def domain_cases():
    """Return independent medical and sports cases with three chronological states."""
    return {"medical": _medical_case(), "sports": _sports_case()}
