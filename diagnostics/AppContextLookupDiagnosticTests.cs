using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.CompilerServices;
using System.Text.Json;
using Xunit;

namespace Avalonia.Controls.DataGridTests;

public class AppContextLookupDiagnosticTests
{
    private const string SwitchName = "ProDataGrid.Reflection.IsEnabled";
    private static volatile int _sink;

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static bool Control() => true;

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static bool Lookup() => !AppContext.TryGetSwitch(SwitchName, out bool enabled) || enabled;

    [Fact]
    public void MeasureWarmedDirectLookup()
    {
        string mode = Environment.GetEnvironmentVariable("PR399_SWITCH_MODE") ?? "absent";
        if (mode == "true") AppContext.SetSwitch(SwitchName, true);
        else if (mode == "false") AppContext.SetSwitch(SwitchName, false);
        else if (mode == "configured") AppContext.SetData(SwitchName, "true");
        else Assert.Equal("absent", mode);

        bool found = AppContext.TryGetSwitch(SwitchName, out bool configuredValue);
        Assert.Equal(mode != "absent", found);
        _ = Stopwatch.GetElapsedTime(Stopwatch.GetTimestamp());
        Func<bool> control = Control, lookup = Lookup;
        for (int i = 0; i < 10000; i++) { _sink = control() ? 1 : 0; _sink = lookup() ? 1 : 0; }
        const int iterations = 1000000;
        var records = new List<object>(8);
        foreach (char side in "ABBABAAB")
        {
            Func<bool> read = side == 'A' ? control : lookup;
            int sum = 0;
            long before = GC.GetAllocatedBytesForCurrentThread();
            long start = Stopwatch.GetTimestamp();
            for (int i = 0; i < iterations; i++) sum += read() ? 1 : 0;
            double elapsedMs = Stopwatch.GetElapsedTime(start).TotalMilliseconds;
            long allocated = GC.GetAllocatedBytesForCurrentThread() - before;
            _sink = sum;
            records.Add(new { side, elapsedMs, allocated, iterations, sum });
        }
        string output = Environment.GetEnvironmentVariable("REVIEW_OUTPUT")!;
        File.AppendAllText(output, JsonSerializer.Serialize(new { mode, found, configuredValue, runtime = Environment.Version.ToString(), records }) + Environment.NewLine);
    }
}
