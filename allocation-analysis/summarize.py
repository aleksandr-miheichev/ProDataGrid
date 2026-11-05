"""Summarize preserved allocation samples; never execute the product workload."""

from __future__ import annotations

import collections
import hashlib
import json
import pathlib
import sys


OPERATION_MARKERS = {
    "source": "ReviewPerformanceTests+<>c__DisplayClass0_1.<MatchedWorkload>b__13(int32)",
    "scroll": "ReviewPerformanceTests+<>c__DisplayClass0_0.<MatchedWorkload>b__15(int32)",
}


def classify(sample: dict, scenario: str) -> str:
    """Classify visible stack roots without treating missing frames as phase proof."""
    names = [frame["method"] for frame in sample["frames"]]
    if any(OPERATION_MARKERS[scenario] in name for name in names):
        return "operation-lambda-frame-present"
    if any("ReviewPerformanceTests+<>c.<MatchedWorkload>b__0_0" in name for name in names):
        return "workload-item-generation"
    if any("ReviewPerformanceTests.MatchedWorkload()" in name for name in names):
        if any("ReviewPerformanceTests.Pump(" in name for name in names):
            return "workload-pump-phase-unresolved"
        if any("Avalonia.Controls.Window.Show()" in name for name in names):
            return "workload-window-show"
        if any("Avalonia.Controls.Window.CloseCore(" in name for name in names):
            return "workload-window-close"
        return "workload-other-no-operation-lambda-frame"
    if any("discover" in name.lower() for name in names):
        return "discovery"
    if any("xunit" in name.lower() for name in names):
        return "test-runner-or-setup"
    return "other-or-unresolved"


def trimmed_stack(sample: dict) -> list[str]:
    names = [frame["method"] for frame in sample["frames"]]
    for index, name in enumerate(names):
        if "MatchedWorkload" in name:
            return names[: index + 1]
    return names[:12]


def count_types(samples: list[dict]) -> list[dict]:
    counts = collections.Counter(sample["typeName"] for sample in samples)
    return [{"type": name, "sampleCount": count} for name, count in counts.most_common()]


def count_frames(samples: list[dict]) -> list[dict]:
    counts = collections.Counter(
        name
        for sample in samples
        for name in set(trimmed_stack(sample))
    )
    return [{"frame": name, "sampleCount": count} for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def count_stacks(samples: list[dict]) -> list[dict]:
    counts = collections.Counter(tuple(trimmed_stack(sample)) for sample in samples)
    first_index = {}
    for sample in samples:
        first_index.setdefault(tuple(trimmed_stack(sample)), sample["index"])
    return [
        {"sampleCount": count, "exampleSampleIndex": first_index[frames], "frames": list(frames)}
        for frames, count in counts.most_common(25)
    ]


def summarize_trace(path: pathlib.Path) -> tuple[dict, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    scenario, variant, _ = path.name.split("-", 2)
    samples = data["allocations"]
    assert data["processingCompleted"] is True
    assert data["rawProcessingCompleted"] is True
    assert data["rawAllocationTickCount"] == len(samples)
    assert data["rawEventsLost"] == data["eventsLost"]
    assert len(samples) == data["allocationSampleCount"]
    assert all(sample["index"] == index for index, sample in enumerate(samples))
    assert data["eventCounts"]["Microsoft-Windows-DotNETRuntime/GC/AllocationTick"] == len(samples)
    for sample in samples:
        sample["finalCategory"] = classify(sample, scenario)
    categories = {}
    for category in sorted({sample["finalCategory"] for sample in samples}):
        group = [sample for sample in samples if sample["finalCategory"] == category]
        categories[category] = {
            "sampleCount": len(group),
            "intervalBytes": sum(sample["allocationAmount64"] for sample in group),
            "intervalByteAttributionWarning": "Each interval ends at this sampled stack; earlier allocations in the interval can belong to other stacks or types.",
            "topTypes": count_types(group)[:25],
            "topFrames": count_frames(group)[:25],
            "topStacks": count_stacks(group)[:8],
        }
    operation = [sample for sample in samples if sample["finalCategory"] == "operation-lambda-frame-present"]
    workload = [sample for sample in samples if sample["finalCategory"].startswith("workload-") or sample["finalCategory"] == "operation-lambda-frame-present"]
    summary = {
        "scenario": scenario,
        "variant": variant,
        "sourceSha": {"A": "f92035975ac5ab6c0cdede8228952bcd8f618a2e", "C": "d0a415f76adcc0a2a42998121e0c8353ca5650ab"}[variant],
        "inputTrace": data["tracePath"],
        "inputTraceSha256": data["traceSha256"],
        "traceBytes": data["traceBytes"],
        "rawSamples": str(path.resolve()),
        "rawSamplesSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "etlx": data["etlxPath"],
        "conversionLog": data["conversionLogPath"],
        "eventCount": data["EventCount"],
        "eventsLost": data["eventsLost"],
        "directEventPipeTickCount": data["rawAllocationTickCount"],
        "directEventPipeEventsLost": data["rawEventsLost"],
        "directEventPipeEventCount": data["rawEventCount"],
        "truncated": data["Truncated"],
        "lostNotifications": data["lostNotifications"],
        "allocationSampleCount": len(samples),
        "allocationSamplesWithStack": sum(sample["hasStack"] for sample in samples),
        "allocationSamplesWithNamedFrames": data["stacksWithNames"],
        "allocationSamplesWithSomeUnresolvedFrames": data["stacksWithUnknownFrames"],
        "allocationTickVersions": dict(collections.Counter(sample["Version"] for sample in samples)),
        "allocationKinds": dict(collections.Counter(sample["allocationKind"] for sample in samples)),
        "intervalBytesTotal": sum(sample["allocationAmount64"] for sample in samples),
        "intervalBytesMin": min(sample["allocationAmount64"] for sample in samples),
        "intervalBytesMax": max(sample["allocationAmount64"] for sample in samples),
        "gcStartCount": len(data["gcs"]),
        "gcStarts": [
            {"generation": key[0], "reason": key[1], "type": key[2], "count": count}
            for key, count in collections.Counter((gc["Depth"], gc["reason"], gc["type"]) for gc in data["gcs"]).most_common()
        ],
        "exceptionStartCount": len(data["exceptions"]),
        "categories": categories,
        "explicitLambdaTypes": count_types(operation),
        "explicitLambdaFrames": count_frames(operation),
        "explicitLambdaTopStacks": count_stacks(operation),
        "workloadStackTypes": count_types(workload),
        "workloadStackFrames": count_frames(workload),
        "workloadStackSampleCount": len(workload),
    }
    return summary, operation


def compare(baseline: dict, final: dict) -> dict:
    a = {row["type"]: row["sampleCount"] for row in baseline["explicitLambdaTypes"]}
    c = {row["type"]: row["sampleCount"] for row in final["explicitLambdaTypes"]}
    af = {row["frame"]: row["sampleCount"] for row in baseline["explicitLambdaFrames"]}
    cf = {row["frame"]: row["sampleCount"] for row in final["explicitLambdaFrames"]}
    wa = {row["type"]: row["sampleCount"] for row in baseline["workloadStackTypes"]}
    wc = {row["type"]: row["sampleCount"] for row in final["workloadStackTypes"]}
    return {
        "explicitLambdaSamplesBaseline": sum(a.values()),
        "explicitLambdaSamplesFinal": sum(c.values()),
        "workloadStackSamplesBaseline": sum(wa.values()),
        "workloadStackSamplesFinal": sum(wc.values()),
        "comparisonScope": "One profiled Windows process per source and scenario, including warmups; structural stack groups and qualitative evidence only. Pump without an explicit lambda frame may still be inside the operation.",
        "explicitLambdaTypeCounts": [
            {"type": name, "baselineSamples": a.get(name, 0), "finalSamples": c.get(name, 0)}
            for name in sorted(a.keys() | c.keys(), key=lambda name: (-max(a.get(name, 0), c.get(name, 0)), name))
        ],
        "explicitLambdaFrameCounts": [
            {"frame": name, "baselineSamples": af.get(name, 0), "finalSamples": cf.get(name, 0)}
            for name in sorted(af.keys() | cf.keys(), key=lambda name: (-max(af.get(name, 0), cf.get(name, 0)), name))
        ],
        "allWorkloadStackTypeCounts": [
            {"type": name, "baselineSamples": wa.get(name, 0), "finalSamples": wc.get(name, 0)}
            for name in sorted(wa.keys() | wc.keys(), key=lambda name: (-max(wa.get(name, 0), wc.get(name, 0)), name))
        ],
    }


def main() -> None:
    root = pathlib.Path(__file__).resolve().parent
    destination = pathlib.Path(sys.argv[1]).resolve()
    traces = {}
    for path in sorted((root / "parsed").glob("*-allocations.json")):
        summary, _ = summarize_trace(path)
        traces[f"{summary['scenario']}-{summary['variant']}"] = summary
    assert set(traces) == {"source-A", "source-C", "scroll-A", "scroll-C"}
    result = {
        "schemaVersion": 1,
        "analysisDate": "2026-11-05",
        "purpose": "Qualitative allocation diagnosis of separately captured Windows EventPipe traces. No product workload, benchmark or profiler is executed by this analysis.",
        "toolchain": {
            "dotnetSdk": "10.0.403",
            "profiledRuntime": ".NET 10.0.14",
            "dotnetTrace": "10.0.750501",
            "traceEventAssembly": "3.1.23.0, bundled with existing dotnet-trace tool",
            "analysisPython": "3.15.2",
            "packagesInstalledByAnalyzer": [],
            "maintainerHarnessCommit": "5b6504c697040b8ae76ff36e340dd9610a326e3b",
        },
        "method": {
            "conversion": "TraceLog.CreateFromEventPipeDataFile using KeepAllEvents=true, LocalSymbolsOnly=true, ContinueOnError=false; preserved ETLX and conversion logs.",
            "reading": "TraceLog.Events.GetSource().Clr.GCAllocationTick; GetCallStackForEvent; allocation leaf to root. Payloads are copied before the reused TraceEvent advances.",
            "operationMarkers": OPERATION_MARKERS,
            "markerEvidence": "Generated lambda names observed in rundown stacks. The source marker's stacks include ItemsSource/reset paths and the scroll marker's stacks include ScrollIntoView. Numeric closure IDs alone are not source-line mapping.",
            "phasePolicy": "Categories describe visible stack structure, not measured/setup phase. In the immutable harness Pump(window) occurs both initially and inside the operation delegate. A missing operation-lambda frame can result from inlining/tailcalls, and no source-line/native-offset mapping is applied here.",
            "rawCategoryWarning": "Raw parser category is a broad MatchedWorkload lambda heuristic and also includes b__0_0 item creation. Final summary categories use the exact scenario-specific operation lambda above.",
            "byteSemantics": "GCAllocationTick is emitted after approximately 100 KB of allocation. TypeName and stack describe the threshold-crossing object, not every object or type in the interval. ObjectSize is that trigger object's size. Neither interval sums nor trigger-object sums measure retained memory.",
            "lossSemantics": "EventsLost and truncation report transport/conversion loss. Zero lost events does not make sampling exhaustive.",
            "timingPolicy": "Profiled elapsed values are excluded from comparisons and primary timing conclusions.",
        },
        "limitations": [
            "Only one separately profiled Windows process exists per source/scenario; profiles are not primary A/B timing measurements.",
            "The same operation lambda is used for warmups and measured invocations; no exact per-iteration markers are present in these profiles.",
            "Whole-process profiles include discovery, setup, window show/close, pumping and teardown. Async work after the operation returns cannot be unambiguously assigned to a synchronous operation stack.",
            "Only 25 baseline and 19 final scroll ticks have an explicit operation-lambda frame. Pump has another 455/476 ticks with phase unresolved, so 25/19 is not the complete operation allocation count.",
            "Sampling cannot attribute a precise 578-byte difference or establish retained memory, and cannot confirm or refute the separate Linux scroll timing estimates.",
            "Method names resolve from runtime rundown. A small number of samples have some unresolved frames, even though every tick has a stack with named frames. No external native symbol resolution was requested.",
        ],
        "sources": [
            {"url": "https://raw.githubusercontent.com/microsoft/perfview/v3.1.23/src/TraceEvent/TraceLog.cs", "supports": "Matching TraceLog EventPipe conversion, lost events and reused event lifetime contract."},
            {"url": "https://raw.githubusercontent.com/microsoft/perfview/v3.1.23/src/TraceEvent/Parsers/ClrTraceEventParser.cs", "supports": "Matching GCAllocationTick fields and version guards including ObjectSize on v4."},
            {"url": "https://learn.microsoft.com/en-us/dotnet/fundamentals/diagnostics/runtime-garbage-collection-events#gcallocationtick_v3-event", "supports": "Approximately 100 KB event frequency and threshold-crossing type/object semantics."},
        ],
        "traces": traces,
        "comparisons": {scenario: compare(traces[scenario + "-A"], traces[scenario + "-C"]) for scenario in ["source", "scroll"]},
    }
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {destination}")
    for name, trace in traces.items():
        print(f"{name}: ticks={trace['allocationSampleCount']}; explicitLambda={trace['categories']['operation-lambda-frame-present']['sampleCount']}; lost={trace['eventsLost']}; truncated={trace['truncated']}")


if __name__ == "__main__":
    main()
