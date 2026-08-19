using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace OmniBase.RuntimeHost;

internal sealed class ChildProcessSupervisor : IDisposable
{
  private readonly string applicationRoot;
  private readonly RuntimeHostConfig config;
  private readonly InstanceEnvironment instanceEnvironment;
  private readonly VerifiedArtifacts artifacts;
  private readonly string authorizationToken;
  private readonly HttpClient readinessClient;
  private readonly CancellationTokenSource lifetime = new();
  private readonly TaskCompletionSource<string> outputFailure =
      new(TaskCreationOptions.RunContinuationsAsynchronously);
  private readonly List<Process> processes = new();
  private WindowsJobObject? job;

  internal ChildProcessSupervisor(
      string applicationRootValue,
      RuntimeHostConfig configValue,
      InstanceEnvironment instanceEnvironmentValue,
      VerifiedArtifacts artifactsValue)
  {
    applicationRoot = applicationRootValue;
    config = configValue;
    instanceEnvironment = instanceEnvironmentValue;
    artifacts = artifactsValue;
    authorizationToken = Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
    readinessClient = new HttpClient(new SocketsHttpHandler
    {
      AllowAutoRedirect = false,
      AutomaticDecompression = DecompressionMethods.None,
      MaxConnectionsPerServer = 2,
      UseCookies = false,
      UseProxy = false,
    })
    {
      Timeout = Timeout.InfiniteTimeSpan,
    };
  }

  internal async Task<int> RunAsync()
  {
    EnsurePortAvailable(config.BackendPort);
    EnsurePortAvailable(config.FrontendPort);

    ConsoleCancelEventHandler cancelHandler = (_, eventArgs) =>
    {
      eventArgs.Cancel = true;
      lifetime.Cancel();
    };
    Console.CancelKeyPress += cancelHandler;
    try
    {
      job = WindowsJobObject.Create();
      var budget = new OutputBudget(
          config.PerStreamOutputLimitBytes,
          config.TotalOutputLimitBytes);
      var backend = Start(
          "backend",
          artifacts.Backend,
          CreateBackendStartInfo(),
          budget);
      var frontend = Start(
          "frontend",
          artifacts.Node,
          CreateFrontendStartInfo(),
          budget,
          artifacts.Frontend);

      var startup = await WaitUntilReadyAsync(backend, frontend).ConfigureAwait(false);
      if (startup is not null)
        return await StopAndReturnAsync(startup.Value.ExitCode, startup.Value.Code).ConfigureAwait(false);

      Console.WriteLine("runtime_host_ready");
      var backendExit = backend.WaitForExitAsync();
      var frontendExit = frontend.WaitForExitAsync();
      var cancellation = Task.Delay(Timeout.InfiniteTimeSpan, lifetime.Token);
      var completed = await Task.WhenAny(backendExit, frontendExit, outputFailure.Task, cancellation)
          .ConfigureAwait(false);
      if (completed == outputFailure.Task)
        return await StopAndReturnAsync(Program.OutputLimitExceeded, outputFailure.Task.Result)
            .ConfigureAwait(false);
      if (completed == cancellation)
        return await StopAndReturnAsync(Program.Ready, "runtime_host_stopped").ConfigureAwait(false);
      return await StopAndReturnAsync(Program.ChildExited, "runtime_host_child_exited")
          .ConfigureAwait(false);
    }
    catch (HostFailureException)
    {
      await ConvergeAsync().ConfigureAwait(false);
      throw;
    }
    catch (Exception exception) when (exception is Win32Exception or InvalidOperationException or IOException)
    {
      await ConvergeAsync().ConfigureAwait(false);
      throw new HostFailureException("runtime_host_child_start_failed", Program.ChildStartFailure);
    }
    finally
    {
      Console.CancelKeyPress -= cancelHandler;
    }
  }

  public void Dispose()
  {
    lifetime.Cancel();
    job?.Dispose();
    job = null;
    foreach (var process in processes)
      process.Dispose();
    processes.Clear();
    readinessClient.Dispose();
    lifetime.Dispose();
  }

  internal void RequestStop() => lifetime.Cancel();

  internal bool TrackedChildrenExited => processes.All(process => process.HasExited);

  internal ProcessStartInfo CreateBackendStartInfo()
  {
    var workingDirectory = Path.GetDirectoryName(artifacts.Backend.FullPath) ?? applicationRoot;
    var startInfo = CreateBaseStartInfo(artifacts.Backend.FullPath, workingDirectory);
    startInfo.ArgumentList.Add("--host");
    startInfo.ArgumentList.Add("127.0.0.1");
    startInfo.ArgumentList.Add("--port");
    startInfo.ArgumentList.Add(config.BackendPort.ToString(System.Globalization.CultureInfo.InvariantCulture));
    startInfo.ArgumentList.Add("--data-root");
    startInfo.ArgumentList.Add(instanceEnvironment.DataRoot);
    startInfo.ArgumentList.Add("--application-version");
    startInfo.ArgumentList.Add(config.ApplicationVersion);
    AddSharedEnvironment(startInfo);
    startInfo.Environment["OMNIBASE_DESKTOP_INSTANCE_TOKEN"] = authorizationToken;
    startInfo.Environment["OMNIBASE_DESKTOP_NATIVE_PROOF_KEY"] =
        instanceEnvironment.NativeProofKey;
    startInfo.Environment["OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN"] =
        instanceEnvironment.NativeControlToken;
    return startInfo;
  }

  internal ProcessStartInfo CreateFrontendStartInfo()
  {
    var workingDirectory = Path.GetDirectoryName(artifacts.Frontend.FullPath) ?? applicationRoot;
    var startInfo = CreateBaseStartInfo(artifacts.Node.FullPath, workingDirectory);
    startInfo.ArgumentList.Add(artifacts.Frontend.FullPath);
    AddSharedEnvironment(startInfo);
    startInfo.Environment["OMNIBASE_DESKTOP_INSTANCE_TOKEN"] = authorizationToken;
    startInfo.Environment["NODE_ENV"] = "production";
    startInfo.Environment["HOSTNAME"] = "127.0.0.1";
    startInfo.Environment["PORT"] = config.FrontendPort.ToString(System.Globalization.CultureInfo.InvariantCulture);
    startInfo.Environment["API_PROXY_URL"] =
        $"http://127.0.0.1:{config.BackendPort.ToString(System.Globalization.CultureInfo.InvariantCulture)}";
    return startInfo;
  }

  private Process Start(
      string name,
      VerifiedArtifact executable,
      ProcessStartInfo startInfo,
      OutputBudget budget,
      VerifiedArtifact? argumentArtifact = null)
  {
    PathSecurity.ReverifyArtifact(executable);
    if (argumentArtifact is not null)
      PathSecurity.ReverifyArtifact(argumentArtifact);
    var process = Process.Start(startInfo);
    if (process is null)
      throw new HostFailureException("runtime_host_child_start_failed", Program.ChildStartFailure);
    processes.Add(process);
    try
    {
      job!.Assign(process);
    }
    catch
    {
      TryKill(process);
      throw;
    }

    _ = DrainAsync(process.StandardOutput.BaseStream, $"{name}_stdout", budget, lifetime.Token);
    _ = DrainAsync(process.StandardError.BaseStream, $"{name}_stderr", budget, lifetime.Token);
    if (process.HasExited)
      throw new HostFailureException("runtime_host_child_exited_during_startup", Program.ChildExited);
    return process;
  }

  private async Task<(int ExitCode, string Code)?> WaitUntilReadyAsync(Process backend, Process frontend)
  {
    var deadline = DateTime.UtcNow.AddSeconds(config.StartupTimeoutSeconds);
    var backendReady = false;
    var frontendReady = false;
    while (DateTime.UtcNow < deadline)
    {
      if (lifetime.IsCancellationRequested)
        return (Program.Ready, "runtime_host_stopped");
      if (outputFailure.Task.IsCompleted)
        return (Program.OutputLimitExceeded, outputFailure.Task.Result);
      if (backend.HasExited || frontend.HasExited)
        return (Program.ChildExited, "runtime_host_child_exited_during_startup");

      backendReady = backendReady ||
          await CanProveRuntimeAsync(config.BackendPort).ConfigureAwait(false);
      frontendReady = frontendReady ||
          await CanConnectAsync(config.FrontendPort).ConfigureAwait(false);
      if (backendReady && frontendReady)
        return null;
      try
      {
        await Task.Delay(100, lifetime.Token).ConfigureAwait(false);
      }
      catch (OperationCanceledException)
      {
        return (Program.Ready, "runtime_host_stopped");
      }
    }
    return (Program.ChildStartFailure, "runtime_host_startup_timeout");
  }

  private async Task<int> StopAndReturnAsync(int intendedCode, string message)
  {
    var converged = await ConvergeAsync().ConfigureAwait(false);
    if (!converged)
      return Program.Fail(Program.ShutdownDidNotConverge, "runtime_host_shutdown_did_not_converge");
    if (intendedCode == Program.Ready)
    {
      Console.WriteLine(message);
      return Program.Ready;
    }
    return Program.Fail(intendedCode, message);
  }

  private async Task<bool> ConvergeAsync()
  {
    lifetime.Cancel();
    job?.Dispose();
    job = null;
    var waits = new List<Task>();
    foreach (var process in processes)
    {
      try
      {
        if (!process.HasExited)
          waits.Add(process.WaitForExitAsync());
      }
      catch (InvalidOperationException)
      {
        // A process that never started is already converged.
      }
    }
    if (waits.Count == 0)
      return true;

    var allExited = Task.WhenAll(waits);
    var timeout = Task.Delay(TimeSpan.FromSeconds(config.ShutdownTimeoutSeconds));
    if (await Task.WhenAny(allExited, timeout).ConfigureAwait(false) == allExited)
      return true;

    foreach (var process in processes)
      TryKill(process);
    var finalWaits = new List<Task>();
    foreach (var process in processes)
    {
      try
      {
        if (!process.HasExited)
          finalWaits.Add(process.WaitForExitAsync());
      }
      catch (InvalidOperationException)
      {
        // A process that never started is already converged.
      }
    }
    if (finalWaits.Count == 0)
      return true;
    var final = Task.WhenAll(finalWaits);
    return await Task.WhenAny(final, Task.Delay(TimeSpan.FromSeconds(2))).ConfigureAwait(false) == final;
  }

  private async Task DrainAsync(
      Stream stream,
      string streamName,
      OutputBudget budget,
      CancellationToken cancellationToken)
  {
    var buffer = new byte[4096];
    try
    {
      while (true)
      {
        var count = await stream.ReadAsync(buffer.AsMemory(), cancellationToken).ConfigureAwait(false);
        if (count == 0)
          return;
        if (!budget.TryConsume(streamName, count))
        {
          outputFailure.TrySetResult("runtime_host_child_output_limit_exceeded");
          return;
        }
      }
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      // Expected during group shutdown.
    }
    catch (IOException)
    {
      if (!cancellationToken.IsCancellationRequested)
        outputFailure.TrySetResult("runtime_host_child_output_read_failed");
    }
  }

  private void AddSharedEnvironment(ProcessStartInfo startInfo)
  {
    startInfo.Environment["OMNIBASE_DESKTOP_DATA_ROOT"] = instanceEnvironment.DataRoot;
    startInfo.Environment["OMNIBASE_DESKTOP_MODE"] = "1";
    startInfo.Environment["TEMP"] = instanceEnvironment.DataRoot;
    startInfo.Environment["TMP"] = instanceEnvironment.DataRoot;
  }

  private static ProcessStartInfo CreateBaseStartInfo(string executable, string workingDirectory)
  {
    var startInfo = new ProcessStartInfo
    {
      FileName = executable,
      WorkingDirectory = workingDirectory,
      UseShellExecute = false,
      CreateNoWindow = true,
      RedirectStandardInput = false,
      RedirectStandardOutput = true,
      RedirectStandardError = true,
    };
    startInfo.Environment.Clear();
    var windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
    if (!string.IsNullOrEmpty(windows))
    {
      startInfo.Environment["SystemRoot"] = windows;
      startInfo.Environment["WINDIR"] = windows;
    }
    return startInfo;
  }

  private static void EnsurePortAvailable(int port)
  {
    TcpListener? listener = null;
    try
    {
      listener = new TcpListener(IPAddress.Loopback, port);
      listener.Server.ExclusiveAddressUse = true;
      listener.Start();
    }
    catch (SocketException)
    {
      throw new HostFailureException("runtime_host_port_unavailable", Program.ChildStartFailure);
    }
    finally
    {
      listener?.Stop();
    }
  }

  private async Task<bool> CanProveRuntimeAsync(int port)
  {
    var challenge = Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
    using var request = new HttpRequestMessage(
        HttpMethod.Get,
        $"http://127.0.0.1:{port.ToString(System.Globalization.CultureInfo.InvariantCulture)}/health");
    request.Headers.TryAddWithoutValidation(
        "x-omnibase-desktop-instance",
        authorizationToken);
    request.Headers.TryAddWithoutValidation(
        "x-omnibase-desktop-challenge",
        challenge);
    using var timeout = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
    timeout.CancelAfter(TimeSpan.FromMilliseconds(500));
    try
    {
      using var response = await readinessClient.SendAsync(
          request,
          HttpCompletionOption.ResponseHeadersRead,
          timeout.Token).ConfigureAwait(false);
      if (!response.IsSuccessStatusCode ||
          !response.Headers.TryGetValues("x-omnibase-desktop-proof", out var proofValues))
        return false;
      var proofs = proofValues.ToArray();
      if (proofs.Length != 1 || !InstanceEnvironment.IsValidToken(proofs[0]))
        return false;
      var expected = HMACSHA256.HashData(
          Convert.FromHexString(instanceEnvironment.NativeProofKey),
          Encoding.ASCII.GetBytes(challenge));
      return CryptographicOperations.FixedTimeEquals(
          Convert.FromHexString(proofs[0]),
          expected);
    }
    catch (Exception exception) when (
        exception is HttpRequestException or OperationCanceledException or FormatException)
    {
      return false;
    }
  }

  private static async Task<bool> CanConnectAsync(int port)
  {
    using var client = new TcpClient(AddressFamily.InterNetwork);
    using var timeout = new CancellationTokenSource(TimeSpan.FromMilliseconds(200));
    try
    {
      await client.ConnectAsync(IPAddress.Loopback, port, timeout.Token).ConfigureAwait(false);
      return true;
    }
    catch (Exception exception) when (exception is SocketException or OperationCanceledException)
    {
      return false;
    }
  }

  private static void TryKill(Process process)
  {
    try
    {
      if (!process.HasExited)
        process.Kill(entireProcessTree: true);
    }
    catch (Exception exception) when (exception is InvalidOperationException or Win32Exception)
    {
      // Convergence verification below remains authoritative.
    }
  }
}

internal sealed class OutputBudget
{
  private readonly int perStreamLimit;
  private readonly int totalLimit;
  private readonly Dictionary<string, int> perStream = new(StringComparer.Ordinal);
  private int total;

  internal OutputBudget(int perStreamLimitValue, int totalLimitValue)
  {
    perStreamLimit = perStreamLimitValue;
    totalLimit = totalLimitValue;
  }

  internal bool TryConsume(string stream, int count)
  {
    if (count < 0)
      return false;
    lock (perStream)
    {
      var streamTotal = checked(perStream.GetValueOrDefault(stream) + count);
      var combined = checked(total + count);
      if (streamTotal > perStreamLimit || combined > totalLimit)
        return false;
      perStream[stream] = streamTotal;
      total = combined;
      return true;
    }
  }
}
