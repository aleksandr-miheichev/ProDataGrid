# PR399 performance follow-up

The confirmed per-cell switch-read cost has been removed from null/string-to-string conversion. The strict no-regression acceptance gate is **not established**: repeated positive measurements remain and are retained below. Functional and NativeAOT success do not waive that gate.

## Revisions and method

- Baseline: `f92035975ac5ab6c0cdede8228952bcd8f618a2e`.
- Originally reviewed candidate: `596db0a6e401eb9c26b98e67de3ff6f247f0bed9`.
- Revised candidate: `d0a415f76adcc0a2a42998121e0c8353ca5650ab`.
- Immutable maintainer artifact: `5b6504c697040b8ae76ff36e340dd9610a326e3b`; the primary C# harness is unchanged, SHA256 `755668ad8404f9184cb99a4a48567e9b148ef22459cba91916972180918973cb`.
- Both balanced orders, `ABBABAAB` and `BAABABBA`; eight fresh processes per scenario/order, four per revision, six warmups and 24 operations per process. `DOTNET_TieredCompilation=0`; 10,000 rows and eight columns. No parallel local product builds, tests or profiling during primary measurements.
- .NET SDK 10.0.403/runtime 10.0.14 and Python 3.15.2. Local Windows: i7-4790K, Windows 11 build 28000. Hosted Windows 2025 x64, Ubuntu 26.04 x64 and macOS 26 ARM64 are separate environments; absolute timings are not compared across hosts.
- Local setup uses externally indexed worktrees and a setup-only prepared-runner extension; timed commands and C# code are unchanged. Reverse/local A/A repeats reuse recorded identical binaries. Hosted CI uses the immutable original runner and separately builds each comparison's worktrees; hashes are retained.
- All process samples, orders, unsuccessful attempts and block/parity directions are retained. Twenty-four operations in a process are correlated samples, not independent experimental replicates. No percentage/byte tolerance or automatic performance pass is applied.

## Confirmed mechanism and narrow correction

An isolated instrumented copy counted 480 converter calls and 488/490 switch reads per source operation; scroll had 160 converter calls and 160 switch reads. Direct switch-read allocation was zero. A separate four-mode diagnostic measured 16 million warmed API reads with zero allocated bytes; lookup time was measurable (for example absent lookup 77.45 ms/million versus 2.57 ms/million for its control). These diagnostic timings are not primary workload results.

`Convert` now returns a null/string value immediately when the target is exactly string. Avalonia 12.1.0 already returns the same value in these cases, before culture/parameter conversion: see its [DefaultValueConverter](https://github.com/AvaloniaUI/Avalonia/blob/12.1.0/src/Avalonia.Base/Data/Converters/DefaultValueConverter.cs#L29) and [TypeUtilities identity conversion](https://github.com/AvaloniaUI/Avalonia/blob/12.1.0/src/Avalonia.Base/Utilities/TypeUtilities.cs#L150). `ConvertBack`, other conversion routes and the dynamic reflection switch remain unchanged. Both compatibility modes are covered by two new behavior cases. Diagnostic lookup gates failed before the change and passed afterward.

The final diagnostic copy retains 480 source converter calls but only 8/10 metadata-related switch reads; scroll retains 160 converter calls and zero switch reads. Displayed rows remain 20 and layout is Nested. Instrumentation is excluded from the product patch and primary binaries.

## Revised candidate versus baseline: hosted primary observations

Entries are first order / reverse order. Allocation is candidate minus baseline, current-thread managed bytes per operation.

| Host | source time | source allocation | scroll time | scroll allocation |
| --- | ---: | ---: | ---: | ---: |
| Linux | -0.13% / -0.71% | +164 / -33 B | +1.64% / +2.62% | +9,299 / +4,689 B |
| Windows | +0.23% / +2.37% | -246 / -770 B | +1.24% / +3.73% | -2,100 / +6,934 B |
| macOS | -7.67% / -4.61% | -613 / -746 B | +1.15% / +12.35% | +6,746 / +4,639 B |

These are descriptive measurements, not accepted tolerances or a claim of speedup. Linux scroll allocation is positive in all four balanced blocks; macOS scroll remains positive in both overall orders. Favorable source results do not offset scroll. Local source-to-array allocation is also positive in both orders, even where its aggregate source result changes sign.

Same-source/same-binary controls exhibit variability, but do not consistently share every repeated A/B direction. They therefore do not prove that the A/B positives are noise. The initial local baseline source process mean of 688.619983 ms remains in the dataset without exclusion or a claimed cause.

The primary harness records common count/selection/virtual-surface metadata, but does not record exact realized work, actual offset state or render/drain counts for every sample. Exploratory allocation traces and supplementary host/product controls are kept separate from the primary comparisons. Allocation-tick sampling cannot resolve a 578-byte cause precisely. Managed allocations are not retained memory, peak working set, native/GPU memory or a universal non-regression guarantee.

## Supplementary binary controls and allocation stacks

The candidate also adds two unit-test cases, so the primary comparison changes its test-host assembly. Supplementary controls copy complete runtime directories with identical directory depth and working-directory placement, then swap only the test assembly and PDB. A is baseline product/baseline host, H is baseline product/candidate host, P is the candidate runtime set/baseline host, and C is candidate product/candidate host. Every copied runtime file is inventoried; A/C dependency and runtime-configuration manifests are byte-identical. P includes all changed non-host runtime assets, not just one DataGrid DLL. This experiment changes the primary runner's directory context and is a separate diagnostic, not a replacement acceptance test or an additive decomposition of A/C.

All four comparisons (A/H and A/P, each in both orders) ran on local Windows and on separate Linux/macOS CI hosts. The test-host-only source-to-array allocation estimate repeats positive on Windows (+1,374/+691 B) and macOS (+5,456/+2,486 B), but is negative on Linux (-378/-233 B). With the baseline host fixed, product-only source-to-array allocations are mixed or near zero. This identifies test-host context as a confound, not the universal cause of the primary increases.

The Linux product-only scroll allocation estimates are -4.58/0 B in the copied-runtime context, while its time remains positive (+1.14%/+0.71%, all four balanced blocks positive). macOS product-only source time also remains positive (+0.58%/+11.66%, all four blocks positive); its scroll allocation is mixed (-2,309/+20,578 B). These residual observations remain unresolved. They do not clear the strict gate or erase the original comparisons.

The [supplementary hosted run](https://github.com/aleksandr-miheichev/ProDataGrid/actions/runs/42017254130) remains failed: its final summary parser did not account for xUnit's ANSI color codes. All 128 measured processes exited zero and retained vectors; a separate offline correction checks their actual summaries and raw samples without repeating measurements or changing the original status. The executed and corrected validator definitions and their contract checks are supplied separately.

Four separate `gc-verbose` traces retain all sampled allocation stacks, with zero lost events and no truncated trace. Source samples mostly follow existing row-refresh/property/visual churn; scroll contains both explicit ScrollIntoView and dispatcher/layout paths. No new allocation site was established. A missing operation-lambda frame does not identify startup: Pump is also the last call in the operation delegate, so inlining/tail calls leave its exact phase unresolved. The traces mix setup, warmups and measured operations because the immutable harness has no phase markers. They are exploratory Windows samples, not an explanation of Linux estimates or evidence of retained/peak memory. Original traces are supplied as lossless gzip; profiled timings are excluded from primary comparisons.

## Functional and NativeAOT evidence

After publishing the revised source head, the five new upstream workflows are `action_required` and have not executed. The fork source/CI provenance below is separate from those upstream checks; previous-head upstream green results are not attributed to the new head.

- Original hosted source: 6,017/6,017 passed on each OS.
- Revised hosted source: 6,019/6,019 passed on Windows and Linux. The first macOS full run reports 6,018 passed and one failure: `DataGrid_ColumnDefinitionsSource_DoesNotLeak`, a `NullReferenceException` in `Avalonia.Headless.HeadlessUnitTestSession.Dispose` before the retention assertion. An [unchanged-source recheck](https://github.com/aleksandr-miheichev/ProDataGrid/actions/runs/42011256034) passed the target on baseline and candidate (1/1 each), then the unfiltered full candidate suite (6,019/6,019). The first failure is retained; the retry does not identify or fix its teardown cause.
- Local revised DataGrid suite: 3,267/3,267 passed; actual Windows NativeAOT executable passed, and enabled-reflection publishing failed with genuine native IL errors.
- The first hosted validation incorrectly checked pre-native compiler/analyzer IL warnings as native warnings. Those runs remain recorded as failed gates with native execution Not run. They published successfully with 75 pre-native diagnostics and zero native-phase IL diagnostics; full tests, semantic validation and timing phases are separately recorded.
- Corrected native-only runs execute actual native binaries successfully on all three OSes for both original and revised sources. Native diagnostics remain zero after the standalone native-generation marker, and each enabled-reflection control fails with genuine post-marker IL errors. Earlier phases are explicitly Not run in the native-only follow-up and reference their original executions.

Original full/timing run: https://github.com/aleksandr-miheichev/ProDataGrid/actions/runs/41999728294

Revised full/timing run: https://github.com/aleksandr-miheichev/ProDataGrid/actions/runs/42001896241

Original native execution: https://github.com/aleksandr-miheichev/ProDataGrid/actions/runs/42006138785

Revised native execution: https://github.com/aleksandr-miheichev/ProDataGrid/actions/runs/42006151729

## Raw evidence and remaining interpretation

`performance-analysis.json` retains individual processes, both blocks and alternating operation directions, input hashes, actual test outcomes and the interpretation limits. Original JSONL vectors remain unchanged. Allocation diagnostics, setup-only runner changes and all CI definitions are provided separately; they are not silently substituted for the maintainer's primary method.

`MANIFEST.json` records both original and published SHA256 for every evidence file; large logs and the four traces use lossless gzip. Live Git/CodeGraph indexes, worktrees and production build trees are excluded. Derived ETLX databases remain local and can be regenerated from the supplied traces/analyzer. Every original file, including unsuccessful diagnostic/tool attempts, is retained in the local task evidence directory. CI-only workflows and evidence are kept off the actual PR source branch.

There are 592 audited process records: 400 primary-method comparisons/controls, 64 local binary controls and 128 hosted binary controls. The public layout's offline correction was itself executed successfully against all 128 retained outputs and 12 parser fixtures, without running product code. From this evidence checkout, use Python with the recorded PyYAML version:

```sh
python supplementary/validate-host-controls-offline.py --evidence-root ci/ci-host-controls-42017254130 --workflow workflows/ci-validation-host-controls-corrected.yml --published-workflow workflows/ci-validation-host-controls-executed.yml --output supplementary/revalidated.json
```

The allocation-analysis helper preserves the exact local project and commands, including recorded absolute tool paths. To use it elsewhere, decompress the four traces, substitute the location of the same task-local dotnet-trace/TraceEvent bundle in the project, and choose a separate output directory. That helper has not been tested as a portable package; the original traces and stack report remain independently inspectable.

To repeat the primary comparison, use the immutable `harness/run-comparison.py` with a fresh output directory, the revisions above, `--scenario source --scenario scroll`, and a second fresh directory with `--order BAABABBA`. The script builds both revisions before timing and invokes `dotnet <DataGrid.UnitTests.dll> -class '*ReviewPerformanceTests' -parallel none`. Set up the Git objects from upstream and this fork first. `harness/run-prepared.py` is the local setup adaptation, not the immutable runner. For functional validation, CI ran `dotnet test Avalonia.Controls.DataGrid.slnx -c Release -m:2 -p:CollectCoverage=false --settings build/ci.runsettings`; exact commands, environments and phase outcomes are retained with each run. Native-only and functional-only follow-ups explicitly mark other phases Not run, with references to their actual prior execution.

The patch removes demonstrated redundant work while preserving the tested functional/AOT contracts. Repeated positive workload estimates remain unresolved; this report does not request that they be averaged away, accepted as small, or treated as a demonstrated zero-regression result.
