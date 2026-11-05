using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Diagnostics.Tracing;
using Microsoft.Diagnostics.Tracing.Etlx;
using Microsoft.Diagnostics.Tracing.Parsers.Clr;

if (args.Length != 2)
    throw new ArgumentException("Usage: AllocationTraceAnalyzer <trace-directory> <output-directory>");

var inputDirectory = Path.GetFullPath(args[0]);
var outputDirectory = Path.GetFullPath(args[1]);
Directory.CreateDirectory(outputDirectory);
var jsonOptions = new JsonSerializerOptions { WriteIndented = true };
foreach (var tracePath in Directory.EnumerateFiles(inputDirectory, "*.nettrace").Order())
{
    var name = Path.GetFileNameWithoutExtension(tracePath);
    var etlxPath = Path.Combine(outputDirectory, name + ".etlx");
    var conversionLogPath = Path.Combine(outputDirectory, name + "-conversion.log");
    var lostNotifications = new List<object>();
    if (!File.Exists(etlxPath))
    {
        using var conversionLog = File.CreateText(conversionLogPath);
        TraceLog.CreateFromEventPipeDataFile(tracePath, etlxPath, new TraceLogOptions
        {
            ConversionLog = conversionLog,
            KeepAllEvents = true,
            LocalSymbolsOnly = true,
            ContinueOnError = false,
            OnLostEvents = (truncated, lost, eventCount) =>
                lostNotifications.Add(new { truncated, lost, eventCount })
        });
    }
    using var log = new TraceLog(etlxPath);
    using var rawSource = new EventPipeEventSource(tracePath);
    var rawEventCount = 0;
    var rawAllocationTickCount = 0;
    rawSource.AllEvents += _ => rawEventCount++;
    rawSource.Clr.GCAllocationTick += _ => rawAllocationTickCount++;
    var rawProcessingCompleted = rawSource.Process();
    var rawEventsLost = rawSource.EventsLost;
    using var source = log.Events.GetSource();
    var allocations = new List<object>();
    var eventCounts = new Dictionary<string, int>();
    var gcs = new List<object>();
    var exceptions = new List<object>();
    var stacksWithNames = 0;
    var stacksWithUnknownFrames = 0;
    source.AllEvents += data =>
    {
        var eventName = data.ProviderName + "/" + data.EventName;
        eventCounts[eventName] = eventCounts.GetValueOrDefault(eventName) + 1;
    };
    source.Clr.GCStart += data => gcs.Add(new
    {
        timeMSec = data.TimeStampRelativeMSec,
        data.ProcessID,
        data.ThreadID,
        data.Count,
        data.Depth,
        reason = data.Reason.ToString(),
        type = data.Type.ToString()
    });
    source.Clr.ExceptionStart += data => exceptions.Add(new
    {
        timeMSec = data.TimeStampRelativeMSec,
        data.ProcessID,
        data.ThreadID,
        data.ExceptionType,
        data.ExceptionMessage
    });
    source.Clr.GCAllocationTick += data =>
    {
        var frames = new List<object>();
        var names = new List<string>();
        var stack = log.GetCallStackForEvent(data);
        var hasStack = stack is not null;
        var unknownFrames = 0;
        while (stack is not null)
        {
            var address = stack.CodeAddress;
            var method = address.FullMethodName;
            if (string.IsNullOrEmpty(method))
            {
                unknownFrames++;
                method = "[unresolved:" + address.ModuleName + "]";
            }
            names.Add(method);
            frames.Add(new { method, module = address.ModuleName, address = "0x" + address.Address.ToString("x") });
            stack = stack.Caller;
        }
        if (names.Count > unknownFrames) stacksWithNames++;
        if (unknownFrames > 0) stacksWithUnknownFrames++;
        var category = names.Any(f => f.Contains("MatchedWorkload>") && f.Contains("b__"))
            ? "matched-workload-lambda-frame-unclassified"
            : names.Any(f => f.Contains("MatchedWorkload")) ? "workload-other"
            : names.Any(f => f.Contains("Discover", StringComparison.OrdinalIgnoreCase)) ? "discovery"
            : names.Any(f => f.Contains("Xunit", StringComparison.OrdinalIgnoreCase)) ? "test-runner-or-setup"
            : "other-or-unresolved";
        allocations.Add(new
        {
            index = allocations.Count,
            eventIndex = (int)data.EventIndex,
            timeMSec = data.TimeStampRelativeMSec,
            data.ProcessID,
            data.ProcessName,
            data.ThreadID,
            data.Version,
            typeName = data.TypeName,
            typeId = "0x" + data.TypeID.ToString("x"),
            allocationKind = data.AllocationKind.ToString(),
            allocationAmount64 = data.Version >= 2 ? data.AllocationAmount64 : data.AllocationAmount,
            objectSize = data.Version >= 4 ? data.ObjectSize : (long?)null,
            category,
            hasStack,
            unknownFrames,
            frames
        });
    };
    var processingCompleted = source.Process();
    if (!rawProcessingCompleted || rawAllocationTickCount != allocations.Count)
        throw new InvalidDataException($"Raw/ETLX allocation tick mismatch for {name}: {rawAllocationTickCount}/{allocations.Count}");
    var result = new
    {
        schemaVersion = 1,
        tracePath,
        traceBytes = new FileInfo(tracePath).Length,
        traceSha256 = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(tracePath))).ToLowerInvariant(),
        traceEventAssembly = typeof(TraceLog).Assembly.GetName().FullName,
        etlxPath,
        conversionLogPath,
        processingCompleted,
        rawProcessingCompleted,
        rawEventCount,
        rawAllocationTickCount,
        rawEventsLost,
        eventsLost = log.EventsLost,
        log.Truncated,
        lostNotifications,
        log.EventCount,
        log.HasCallStacks,
        startMSec = log.Events.StartTimeRelativeMSec,
        endMSec = log.Events.EndTimeRelativeMSec,
        stackOrder = "allocation-leaf-to-root",
        stacksWithNames,
        stacksWithUnknownFrames,
        allocationSampleCount = allocations.Count,
        eventCounts = eventCounts.OrderByDescending(p => p.Value).ToDictionary(),
        gcs,
        exceptions,
        allocations
    };
    var outputPath = Path.Combine(outputDirectory, name + "-allocations.json");
    File.WriteAllText(outputPath, JsonSerializer.Serialize(result, jsonOptions));
    Console.WriteLine($"{name}: samples={allocations.Count}; eventsLost={log.EventsLost}; hasStacks={log.HasCallStacks}; output={outputPath}");
}
