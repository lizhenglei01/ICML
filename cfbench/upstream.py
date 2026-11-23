"""Load actual upstream code without modifying or copying its implementation."""
import hashlib
import importlib.util
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]


def repository_path(name):
    """Accept the original checkouts and the user's relocated dependency folder."""
    for path in [WORKSPACE / "ICM2027/三方依赖代码" / name, WORKSPACE / name]:
        if (path / ".git").exists():
            return path
    raise FileNotFoundError(f"Upstream checkout not found: {name}")


OFFICE = repository_path("OfficeEditBench")
WTM = repository_path("WTM-Bench")
COHORT = OFFICE / "medium/cohorts/cross_media"
TASK = "OBM-036"
FILES = ("unit_review.xlsx", "management_discussion.docx")
PINS = {
    "OfficeEditBench": "3b4f567970e25d5340571fb1e41b3a6a28e93ecb",
    "WTM-Bench": "83d45ae601f3241ac7246613c67b765e48f93c06",
    "EditCoherenceBench": "4cfd20c8cd94324cca48bead9362806c764269f7",
    "SWE-Milestone": "17a8f1593e172e26b36cea15e2b30fb9536c93f5",
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


@lru_cache
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def patcher():
    return module("cfbench_office_patch", OFFICE / "hard/patch_office.py")


def verifier():
    return module("cfbench_office_verify", COHORT / "verify_task.py")


def wtm_scorer():
    return module("cfbench_wtm_scorer", WTM / "grader/scorer_v2.py")


def provenance():
    """Fail if an asset/code file differs from its pinned committed blob."""
    records = []
    source_paths = [
        *(f"medium/cohorts/cross_media/tasks/{TASK}/input/{f}" for f in FILES),
        *(f"medium/cohorts/cross_media/private/reference/{TASK}/{f}" for f in FILES),
        f"medium/cohorts/cross_media/tasks/{TASK}/instruction.md",
        f"medium/cohorts/cross_media/private/gold/{TASK}.json",
        "medium/cohorts/cross_media/verify_task.py", "hard/patch_office.py",
        "medium/cohorts/cross_media/SOURCES.md",
        "medium/cohorts/retail_ops/source/manifest.json", "LICENSE.txt",
    ]
    for repo_name, paths in [
        ("OfficeEditBench", source_paths),
        ("WTM-Bench", ["grader/scorer_v2.py", "LICENSE"]),
    ]:
        repo = repository_path(repo_name)
        commit = git(repo, "rev-parse", "HEAD").decode().strip()
        if commit != PINS[repo_name]:
            raise ValueError(f"Unexpected {repo_name} commit: {commit}")
        for relative in paths:
            data = (repo / relative).read_bytes()
            tracked = git(repo, "show", f"{commit}:{relative}")
            if data != tracked:
                raise ValueError(f"Modified upstream file: {repo_name}/{relative}")
            records.append({"repository": repo_name, "checkout": str(repo), "commit": commit,
                            "path": relative, "sha256": sha256(data),
                            "matches_git_blob": True})
    return {"repositories": PINS, "files": records,
            "asset_type": "Upstream authored benchmark fixtures, not enterprise documents",
            "baseline": "Unmodified upstream private/reference/OBM-036; already unit-corrected",
            "license": "OfficeEditBench CC BY 4.0; WTM-Bench MIT; SEC attribution retained",
            "edits": "Only copies are edited; see generated public/input and private/reference",
            "mvp_archive": "Not found in supplied workspace; prior MVP tests not reproduced"}
