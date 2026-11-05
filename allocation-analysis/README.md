# Allocation profile analysis

This is a task-local, read-only analysis of the four separately captured Windows `gc-verbose` traces. It does not execute the product workload, a benchmark or a profiler. The original `.nettrace` files and all primary timing datasets remain unchanged.

The final structured report is `../allocation-profile-analysis.json`. `parsed/*-allocations.json` preserves every allocation tick's payload and full leaf-to-root stack, while the report contains counts and trimmed representative stacks. ETLX conversions, conversion logs, analyzer build logs, and both parser/summary attempts are retained.

## Findings

All four files parse without reported transport loss or truncation. A second direct `EventPipeEventSource` pass independently reports the same allocation tick count as the ETLX pass: source baseline/final 11,288/11,295, scroll baseline/final 1,450/1,469. Every allocation tick has a stack with managed method names. Some frames remain unresolved in 0/2 source samples and 5/6 scroll samples.

The source stacks with an explicit operation lambda contain 10,269/10,275 ticks. Both versions are dominated by the same visual/property/delegate work: `RefreshRows` occurs in 7,020/7,019 of these samples; `System.Object[]` is the threshold-crossing type in 1,125/1,117, and server composition visuals in 520/505. These are sample counts, not complete object counts or proof of a timing result.

The scroll stacks with an explicit operation lambda contain 25/19 ticks. All include `ScrollIntoView` and `ScrollSlotIntoView`; both versions have four ticks in `DataGridScrollHeightIndex.Rebuild`, with arrays and ordinary property/layout notifications. The broader workload stacks contain font lookup/text formatting/visual allocation in both versions. This establishes that the workload includes these activities; it does not establish that an allocation caused any timing difference.

Another 455/476 scroll ticks and 48/48 source ticks contain `MatchedWorkload -> Pump -> Dispatcher.RunJobs` without an explicit operation-lambda frame. Their phase is unresolved. The immutable harness calls `Pump(window)` both initially and inside the operation delegate. Inlining or tailcalls can remove the lambda frame. These samples must not be labelled untimed or setup merely because the frame is missing. The same delegate serves warmups and measured invocations, which cannot be separated in these profiles.

No new causal allocation mechanism is demonstrated. The visible samples show common work in both revisions, with different sampled type counts; the profiles neither establish no regression nor explain the repeated positive Linux scroll estimates. They contain Windows data only. The allocation mix and phase ambiguity are supported mechanisms by which whole-process or sampled allocation comparisons can differ from a narrowly timed block; whether either explains the observed timings remains unproved.

## Limits

`GCAllocationTick` is emitted approximately every 100 KB. The type and stack identify the object crossing the threshold, and the interval can contain other types and stacks. `ObjectSize` is the triggering object's size, not a complete allocation or retained-memory measurement. A zero transport-loss count does not make sampling exhaustive. These files cannot precisely attribute 578 bytes, establish retained memory, or rule out smaller allocations at a frame that never crosses a sample threshold.

One profiled process per source and scenario is insufficient for statistical timing conclusions. Profiled elapsed values are excluded from the final comparison. Existing primary A/B, A/A and cross-platform results remain the relevant timing evidence.

## Reproduce the analysis

The helper pins SDK 10.0.403 and references existing TraceEvent 3.1.23.0 and its dependencies directly from the bundled `dotnet-trace` 10.0.750501 directory. It installs no packages or global tools. The profiled runtime and the analyzer runtime are .NET 10.0.14. The summarizer uses the existing Python 3.15.2 and its standard library.

Run from this directory in PowerShell:

```powershell
& 'D:\claude-projects\tools\rtk\rtk.exe' proxy dotnet build .\AllocationTraceAnalyzer.csproj -c Release --nologo
& 'D:\claude-projects\tools\rtk\rtk.exe' proxy dotnet .\bin\Release\net10.0\AllocationTraceAnalyzer.dll ..\allocation-traces .\parsed
& 'D:\Python 15\python.exe' -X utf8 -B .\summarize.py ..\allocation-profile-analysis.json
```

Existing ETLX files are reused, while derived JSON files are regenerated. For first-time conversion in a separate owned output directory, pass that directory as the analyzer's second argument; do not overwrite or delete the input traces.

The task directory has its own CodeGraph index. CodeGraph exposes the Python analyzer symbols; its C# parser does not expose the top-level statements as method bodies. No product repository, worktree, benchmark or CI file was edited by this analysis.

## API and event semantics

Installed assembly metadata was inspected before coding. Context7 resolution did not provide matching TraceEvent documentation; the exact matching upstream source and official event reference were used instead:

- [TraceLog v3.1.23: EventPipe conversion, loss and event lifetime](https://raw.githubusercontent.com/microsoft/perfview/v3.1.23/src/TraceEvent/TraceLog.cs)
- [CLR parser v3.1.23: allocation tick payloads and version guards](https://raw.githubusercontent.com/microsoft/perfview/v3.1.23/src/TraceEvent/Parsers/ClrTraceEventParser.cs)
- [.NET allocation tick semantics](https://learn.microsoft.com/en-us/dotnet/fundamentals/diagnostics/runtime-garbage-collection-events#gcallocationtick_v3-event)
