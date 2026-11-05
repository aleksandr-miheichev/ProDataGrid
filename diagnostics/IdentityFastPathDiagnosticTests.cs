using System;
using System.Globalization;
using Xunit;

namespace Avalonia.Controls.DataGridTests;

public class IdentityFastPathDiagnosticTests
{
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void IdentityStringDoesNotReadTheCompatibilitySwitch(bool enabled)
    {
        AppContext.SetSwitch("ProDataGrid.Reflection.IsEnabled", enabled);
        foreach (object? input in new object?[] { new string('x', 32), null })
        {
            long before = DataGridReflectionOptions.DiagnosticCalls;
            object? result = DataGridValueConverter.Instance.Convert(input, typeof(string), null, CultureInfo.InvariantCulture);
            Assert.Same(input, result);
            Assert.Equal(0, DataGridReflectionOptions.DiagnosticCalls - before);
        }
    }
}
