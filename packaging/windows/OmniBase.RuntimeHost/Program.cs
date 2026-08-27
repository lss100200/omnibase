using System;
using System.IO;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading.Tasks;

namespace OmniBase.RuntimeHost;

internal static class Program
{
  internal const int Ready = 0;
  internal const int SecurityFailure = 30;
  internal const int ChildStartFailure = 31;
  internal const int ChildExited = 32;
  internal const int OutputLimitExceeded = 33;
  internal const int ShutdownDidNotConverge = 34;
  internal const int UnsupportedPlatform = 35;

  public static async Task<int> Main(string[] args)
  {
    if (args.Length != 0)
      return Fail(SecurityFailure, "runtime_host_arguments_forbidden");
    if (!OperatingSystem.IsWindows())
      return Fail(UnsupportedPlatform, "runtime_host_requires_windows");

    try
    {
      var applicationRoot = PathSecurity.ValidateApplicationRoot(AppContext.BaseDirectory);
      var configPath = Path.Combine(applicationRoot, "runtime-host.json");
      var config = RuntimeHostConfig.Load(configPath);
      var environment = InstanceEnvironment.Load();
      var artifacts = VerifiedArtifacts.Load(applicationRoot, config);

      using var supervisor = new ChildProcessSupervisor(
          applicationRoot,
          config,
          environment,
          artifacts);
      return await supervisor.RunAsync().ConfigureAwait(false);
    }
    catch (HostFailureException exception)
    {
      return Fail(exception.ExitCode, exception.Code);
    }
    catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or
        JsonException or CryptographicException or ArgumentException or InvalidOperationException or
        OverflowException or FormatException)
    {
      return Fail(SecurityFailure, "runtime_host_operation_failed");
    }
  }

  internal static int Fail(int exitCode, string code)
  {
    Console.Error.WriteLine($"runtime_host_error={code}");
    return exitCode;
  }
}

internal sealed class HostFailureException : Exception
{
  internal HostFailureException(string code, int exitCode = Program.SecurityFailure)
      : base(code)
  {
    Code = code;
    ExitCode = exitCode;
  }

  internal string Code { get; }

  internal int ExitCode { get; }
}
