"""Validate retained run 42017254130 stdout after a summary-only ANSI correction.

This script never builds or runs product code. It loads only the two pure
summary parsers from the published and corrected workflow documents.
"""
import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

import yaml


RUN_ID = 42017254130
RUN_SHA = "66a09bd2aab4f913435af85b8d4f3c1c1141e72f"
PUBLISHED_BLOB_SHA = "16eaf54b619ecc76102d4b64efacf29293cb563f"
SOURCE_SHA = "d0a415f76adcc0a2a42998121e0c8353ca5650ab"
BASE_SHA = "f92035975ac5ab6c0cdede8228952bcd8f618a2e"
HARNESS_SHA = "5b6504c697040b8ae76ff36e340dd9610a326e3b"
ARTIFACTS = (
    "perf399-host-controls-Linux-42017254130-attempt-1",
    "perf399-host-controls-macOS-42017254130-attempt-1",
)
CSI = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def input_hashes(root):
    return {
        path.relative_to(root).as_posix(): {
            "sha256": sha256(path.read_bytes()),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def load_parser(path, corrected):
    document = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    step = next(
        step for step in document["jobs"]["validate"]["steps"]
        if step.get("shell") == "python"
    )
    tree = ast.parse(step["run"])
    names = ("strip_ansi_csi_for_summary", "assess_one_test") if corrected else ("assess_one_test",)
    functions = {
        node.name: node for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    namespace = {"re": re}
    selected = ast.Module(body=[functions[name] for name in names], type_ignores=[])
    exec(compile(ast.fix_missing_locations(selected), str(path), "exec"), namespace)
    return document, tree, namespace


def assert_only_summary_correction(published_document, published_tree, corrected_document, corrected_tree):
    normalized = copy.deepcopy(corrected_tree)
    normalizers = [
        node for node in normalized.body
        if isinstance(node, ast.FunctionDef) and node.name == "strip_ansi_csi_for_summary"
    ]
    assert len(normalizers) == 1
    normalized.body.remove(normalizers[0])

    class UndoNormalizer(ast.NodeTransformer):
        calls = 0

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == "strip_ansi_csi_for_summary":
                assert len(node.args) == 1 and not node.keywords
                assert isinstance(node.args[0], ast.Name) and node.args[0].id == "text"
                self.calls += 1
                return node.args[0]
            return self.generic_visit(node)

    undo = UndoNormalizer()
    normalized = undo.visit(normalized)
    assert undo.calls == 1
    assert ast.dump(normalized) == ast.dump(published_tree), "Another orchestration change was introduced"
    old, new = copy.deepcopy(published_document), copy.deepcopy(corrected_document)
    for document in (old, new):
        step = next(s for s in document["jobs"]["validate"]["steps"] if s.get("shell") == "python")
        step["run"] = "<only summary normalizer differs>"
    assert old == new, "Another YAML setting was changed"


def fixtures(parser, normalizer):
    line = (
        "   Avalonia.Controls.DataGrid.UnitTests  Total: 1, Errors: 0, "
        "Failed: 0, Skipped: 0, Not Run: 0, Time: 8.716s"
    )
    esc = chr(27)
    osc = esc + "]0;title" + chr(7)
    cases = [
        ("plain_one_pass", 0, line, True),
        ("ansi_sgr_before_summary", 0, esc + "[37m" + line + esc + "[0m", True),
        ("standard_csi_and_sgr", 0, esc + "[2K" + esc + "[38;2;255;0;0m" + line, True),
        ("c1_csi", 0, chr(155) + "37m" + line, True),
        ("missing_summary", 0, "No matching summary", False),
        ("duplicate_after_normalization", 0, line + "\n" + esc + "[37m" + line, False),
        ("failed_test", 0, esc + "[37m" + line.replace("Failed: 0", "Failed: 1"), False),
        ("skipped_test", 0, line.replace("Skipped: 0", "Skipped: 1"), False),
        ("not_run_test", 0, line.replace("Not Run: 0", "Not Run: 1"), False),
        ("runner_error", 0, line.replace("Errors: 0", "Errors: 1"), False),
        ("nonzero_cli_with_passing_summary", 1, esc + "[37m" + line, False),
        ("osc_is_not_csi_and_is_not_removed", 0, osc + line, False),
    ]
    records = []
    for name, exit_code, text, accepted in cases:
        actual = parser(exit_code, text)
        records.append({
            "case": name,
            "status": "passed" if actual["accepted"] == accepted else "failed",
            "expected_accepted": accepted,
            "actual": actual,
        })
    assert normalizer(osc + line) == osc + line, "Only standard CSI may be removed"
    assert all(record["status"] == "passed" for record in records), records
    return records


def inspect_artifact(root, name, old_parser, new_parser):
    artifact = root / name
    plan = read_json(artifact / "timing-plan.json")
    statuses = read_json(artifact / "timing-process-status.json")
    invocation = read_json(artifact / "invocation.json")
    provenance = read_json(artifact / "provenance.json")
    phases = read_json(artifact / "phase-results.json")
    assert str(invocation["run_id"]) == str(RUN_ID) and str(invocation["run_attempt"]) == "1"
    assert invocation["workflow_sha"] == RUN_SHA
    assert provenance["workflow_sha"] == RUN_SHA
    assert provenance["source_sha"] == SOURCE_SHA and provenance["baseline_sha"] == BASE_SHA
    assert provenance["harness_commit"] == HARNESS_SHA
    assert len(plan) == len(statuses) == 64
    assert phases["provenance-prebuild-and-binary-proof"]["status"] == "passed"
    assert phases["supplementary-binary-factor-timings"]["status"] == "passed"
    assert phases["raw-evidence-and-input-validation"]["status"] == "failed"
    assert len({record["log_name"] for record in statuses}) == 64

    records = []
    for planned, status in zip(plan, statuses):
        assert all(status[key] == value for key, value in planned.items())
        relative = Path(status["log_name"] + ".log")
        path = (artifact / relative).resolve()
        assert path.is_relative_to(artifact.resolve())
        data = path.read_bytes()
        text = data.decode("utf-8", errors="strict")
        original = old_parser(status["exit_code"], text)
        corrected = new_parser(status["exit_code"], text)
        raw_summaries = [
            line for line in text.splitlines()
            if "Avalonia.Controls.DataGrid.UnitTests" in line and "Total:" in line
        ]
        records.append({
            "log": path.relative_to(root).as_posix(),
            "stdout_sha256": sha256(data),
            "stdout_bytes": len(data),
            "batch": status["batch"],
            "scenario": status["scenario"],
            "index": status["index"],
            "protocol_side": status["protocol_side"],
            "component": status["component"],
            "recorded_cli_exit": status["exit_code"],
            "standard_csi_sequences": len(CSI.findall(text)),
            "raw_summary_lines": raw_summaries,
            "original_published_assessment": original,
            "corrected_offline_assessment": corrected,
        })

    vectors = read_json(artifact / "vector-validation.json")
    after = read_json(artifact / "runtime-input-inventories-after.json")
    overlays = read_json(artifact / "binary-overlay-proofs.json")
    build = read_json(artifact / "original-build-inventories.json")
    assert vectors["expected_raw_records"] == vectors["observed_raw_records"] == 64
    assert len(vectors["checks"]) == 64 and all(check["accepted"] for check in vectors["checks"])
    original_summary_issues = vectors["issues"]
    assert len(original_summary_issues) == 64
    assert all(issue.startswith("Not exactly one passing test: ") for issue in original_summary_issues)
    assert {issue.removeprefix("Not exactly one passing test: ") for issue in original_summary_issues} == {
        record["log_name"] for record in statuses
    }
    assert set(after["unchanged_from_prebuild"]) == {"A", "C", "H", "P"}
    assert all(after["unchanged_from_prebuild"].values())
    assert all(proof["status"] == "passed" for proof in overlays.values())
    assert build["deps_runtimeconfig_equality"]["status"] == "passed"
    semantic_groups = []
    for summary_file in sorted((artifact / "timings").glob("*/summary.jsonl")):
        for line in summary_file.read_text(encoding="utf-8").splitlines():
            summary = json.loads(line)
            assert summary["semantic_metadata_identical"] is True
            semantic_groups.append({
                "batch": summary["batch"],
                "scenario": summary["scenario"],
                "semantic_metadata_identical": True,
            })
    assert len(semantic_groups) == 8
    assert all(not record["original_published_assessment"]["accepted"] for record in records)
    assert all(record["original_published_assessment"]["summary_count"] == 0 for record in records)
    assert all(record["standard_csi_sequences"] > 0 for record in records)
    assert all(record["corrected_offline_assessment"]["accepted"] for record in records)
    return {
        "artifact": name,
        "recorded_processes": len(records),
        "original_summary_rejections": len(records),
        "corrected_one_test_passes": len(records),
        "existing_vectors_accepted": 64,
        "existing_semantic_groups": semantic_groups,
        "existing_component_and_input_proofs": "passed and retained unchanged",
        "original_phase_results": phases,
        "processes": records,
    }


def main():
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, default=base / "ci-host-controls-42017254130")
    parser.add_argument("--workflow", type=Path, default=base / "ci-validation-host-controls.yml")
    parser.add_argument("--published-workflow", type=Path, default=base / "ci-validation-host-controls-published-66a09bd2.yml")
    parser.add_argument("--output", type=Path, default=base / "host-controls-42017254130-ansi-validation.json")
    args = parser.parse_args()
    assert not args.output.resolve().is_relative_to(args.evidence_root.resolve()), "Never write into retained CI inputs"
    assert args.output.resolve().is_relative_to(base), "Correction evidence must stay under this evidence root"
    published_bytes = args.published_workflow.read_bytes()
    blob = hashlib.sha1(b"blob " + str(len(published_bytes)).encode() + b"\0" + published_bytes).hexdigest()
    assert blob == PUBLISHED_BLOB_SHA, "Published snapshot must match the workflow that actually ran"
    before = input_hashes(args.evidence_root)
    published_document, published_tree, old = load_parser(args.published_workflow, False)
    corrected_document, corrected_tree, new = load_parser(args.workflow, True)
    assert_only_summary_correction(published_document, published_tree, corrected_document, corrected_tree)
    test_cases = fixtures(new["assess_one_test"], new["strip_ansi_csi_for_summary"])
    artifacts = [
        inspect_artifact(args.evidence_root, name, old["assess_one_test"], new["assess_one_test"])
        for name in ARTIFACTS
    ]
    after = input_hashes(args.evidence_root)
    assert before == after, "A retained input changed during read-only offline validation"
    assert sum(item["recorded_processes"] for item in artifacts) == 128
    report = {
        "schema_version": 1,
        "assessment": "passed: offline correction of the exact-one-test stdout parser",
        "original_ci": {
            "run_id": RUN_ID,
            "run_attempt": 1,
            "run_url": f"https://github.com/aleksandr-miheichev/ProDataGrid/actions/runs/{RUN_ID}",
            "head_sha": RUN_SHA,
            "status": "completed",
            "conclusion": "failure",
            "conclusion_preserved": True,
        },
        "cause": {
            "observed": "Standard ANSI ESC[37m occurs before the visible xUnit summary",
            "defect": "The published anchored regex sees ESC as a non-whitespace prefix and finds zero summaries",
            "correction": "Strip only standard CSI sequences, including SGR, from the in-memory summary-parsing copy",
            "unchanged_gates": "Exit0; exactly one summary; Total1/Errors0/Failed0/Skipped0/NotRun0",
            "original_logs_preserved": True,
        },
        "scope": {
            "actual_stdout_and_exit_records": 128,
            "actual_product_executions_by_this_script": 0,
            "benchmarks_rerun": False,
            "corrected_workflow_was_executed": False,
            "other_workflow_ast_or_yaml_changes": False,
            "interpretation": "All128 retained processes satisfy the corrected exact-one-test gate. Existing vector, semantic-metadata and binary-input proofs remain valid. This does not change the original failed CI conclusion or establish performance acceptance, root cause, additive decomposition, or equal actual realization.",
        },
        "provenance": {
            "published_workflow": str(args.published_workflow.resolve()),
            "published_workflow_sha256": sha256(published_bytes),
            "published_git_blob_sha": blob,
            "canonical_snapshot_note": "The parent's initial transport snapshot added one trailing LF. The separate canonical snapshot removes only that LF and matches the immutable Git blob; the transport file and all retained CI data remain unchanged.",
            "transport_snapshot_sha256": sha256((base / "ci-validation-host-controls-executed.yml").read_bytes()) if (base / "ci-validation-host-controls-executed.yml").exists() else None,
            "published_workflow_url": f"https://github.com/aleksandr-miheichev/ProDataGrid/blob/{RUN_SHA}/.github/workflows/ci-validation.yml",
            "corrected_workflow": str(args.workflow.resolve()),
            "corrected_workflow_sha256": sha256(args.workflow.read_bytes()),
            "offline_validator": str(Path(__file__).resolve()),
            "offline_validator_sha256": sha256(Path(__file__).read_bytes()),
            "python": sys.version,
            "pyyaml": yaml.__version__,
            "retained_input_file_count": len(before),
            "retained_input_hashes_unchanged": True,
        },
        "fixtures": test_cases,
        "artifacts": artifacts,
        "retained_input_files": before,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "result": "PASS",
        "actual_processes_validated": 128,
        "original_ci_conclusion": "failure",
        "fixtures": len(test_cases),
        "retained_input_files_unchanged": len(before),
        "product_executions": "Not run",
        "report": str(args.output.resolve()),
        "report_sha256": sha256(args.output.read_bytes()),
    }, indent=2))


if __name__ == "__main__":
    main()
