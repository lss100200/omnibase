using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using OmniBase.RuntimeHost;

if (args.Length > 0)
  return await FakeChild.RunAsync(args).ConfigureAwait(false);
return Tests.Run();

internal static class Tests
{
  private static readonly List<(string Name, Action Test)> Cases = new()
  {
    ("config_accepts_exact_closed_schema", ConfigAcceptsExactClosedSchema),
    ("config_rejects_unknown_property", ConfigRejectsUnknownProperty),
    ("config_rejects_duplicate_property", ConfigRejectsDuplicateProperty),
    ("config_rejects_unknown_artifact_property", ConfigRejectsUnknownArtifactProperty),
    ("config_rejects_non_hex_digest", ConfigRejectsNonHexDigest),
    ("config_rejects_invalid_application_version", ConfigRejectsInvalidApplicationVersion),
    ("config_rejects_equal_ports", ConfigRejectsEqualPorts),
    ("config_rejects_unbounded_output_relationship", ConfigRejectsUnboundedOutputRelationship),
    ("relative_file_accepts_contained_path", RelativeFileAcceptsContainedPath),
    ("relative_file_rejects_parent_traversal", RelativeFileRejectsParentTraversal),
    ("relative_file_rejects_absolute_path", RelativeFileRejectsAbsolutePath),
    ("artifact_digest_accepts_exact_file", ArtifactDigestAcceptsExactFile),
    ("artifact_digest_rejects_mismatch", ArtifactDigestRejectsMismatch),
    ("data_root_rejects_volume_root", DataRootRejectsVolumeRoot),
    ("instance_token_accepts_exact_lowercase_hex", InstanceTokenAcceptsExactLowercaseHex),
    ("instance_token_rejects_noncanonical_values", InstanceTokenRejectsNoncanonicalValues),
    ("instance_environment_requires_explicit_valid_values", InstanceEnvironmentRequiresExplicitValidValues),
    ("output_budget_enforces_per_stream_and_total", OutputBudgetEnforcesPerStreamAndTotal),
    ("backend_start_info_is_fixed_and_closed", BackendStartInfoIsFixedAndClosed),
    ("frontend_start_info_is_fixed_and_closed", FrontendStartInfoIsFixedAndClosed),
    ("windows_job_object_can_be_configured", WindowsJobObjectCanBeConfigured),
    ("supervisor_starts_and_converges_process_group", SupervisorStartsAndConvergesProcessGroup),
    ("supervisor_fails_closed_when_child_exits", SupervisorFailsClosedWhenChildExits),
    ("supervisor_fails_closed_on_output_limit", SupervisorFailsClosedOnOutputLimit),
  };

  internal static int Run()
  {
    var failures = 0;
    foreach (var (name, test) in Cases)
    {
      try
      {
        test();
        Console.WriteLine($"PASS {name}");
      }
      catch (Exception exception)
      {
        failures++;
        Console.Error.WriteLine($"FAIL {name}: {exception.GetType().Name}: {exception.Message}");
      }
    }
    Console.WriteLine($"runtime_host_tests={Cases.Count - failures}/{Cases.Count}");
    return failures == 0 ? 0 : 1;
  }

  private static void ConfigAcceptsExactClosedSchema()
  {
    var config = Parse(ValidJson());
    Equal(31100, config.BackendPort);
    Equal(31101, config.FrontendPort);
    Equal("runtime/backend.exe", config.Backend.Path);
    Equal(new string('a', 64), config.Backend.Sha256);
    Equal("1.0.0", config.ApplicationVersion);
  }

  private static void ConfigRejectsUnknownProperty() =>
      Throws("runtime_host_config_schema_invalid", () => Parse(
          ValidJson().Replace("\"schema_version\":1", "\"schema_version\":1,\"extra\":false", StringComparison.Ordinal)));

  private static void ConfigRejectsDuplicateProperty() =>
      Throws("runtime_host_config_schema_invalid", () => Parse(
          ValidJson().Replace("\"schema_version\":1", "\"schema_version\":1,\"schema_version\":1", StringComparison.Ordinal)));

  private static void ConfigRejectsUnknownArtifactProperty() =>
      Throws("runtime_host_artifact_schema_invalid", () => Parse(
          ValidJson().Replace("\"backend\":{", "\"backend\":{\"args\":[],", StringComparison.Ordinal)));

  private static void ConfigRejectsNonHexDigest() =>
      Throws("runtime_host_artifact_digest_invalid", () => Parse(
          ValidJson().Replace(new string('a', 64), new string('z', 64), StringComparison.Ordinal)));

  private static void ConfigRejectsInvalidApplicationVersion() =>
      Throws("runtime_host_application_version_invalid", () => Parse(
          ValidJson().Replace("\"application_version\":\"1.0.0\"",
              "\"application_version\":\"1.0.0 private\"", StringComparison.Ordinal)));

  private static void ConfigRejectsEqualPorts() =>
      Throws("runtime_host_ports_must_differ", () => Parse(
          ValidJson().Replace("\"frontend_port\":31101", "\"frontend_port\":31100", StringComparison.Ordinal)));

  private static void ConfigRejectsUnboundedOutputRelationship() =>
      Throws("runtime_host_output_limits_invalid", () => Parse(
          ValidJson().Replace("\"total_output_limit_bytes\":131072", "\"total_output_limit_bytes\":200000", StringComparison.Ordinal)));

  private static void RelativeFileAcceptsContainedPath()
  {
    var result = PathSecurity.ResolveRelativeFile("C:\\OmniBase", "runtime/backend.exe");
    Equal("C:\\OmniBase\\runtime\\backend.exe", result);
  }

  private static void RelativeFileRejectsParentTraversal() =>
      Throws("runtime_host_artifact_path_invalid", () =>
          PathSecurity.ResolveRelativeFile("C:\\OmniBase", "../backend.exe"));

  private static void RelativeFileRejectsAbsolutePath() =>
      Throws("runtime_host_artifact_path_invalid", () =>
          PathSecurity.ResolveRelativeFile("C:\\OmniBase", "C:\\Windows\\notepad.exe"));

  private static void ArtifactDigestAcceptsExactFile()
  {
    var root = Directory.GetCurrentDirectory();
    const string relative = "packaging/windows/OmniBase.RuntimeHost/Program.cs";
    var fullPath = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
    var digest = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(fullPath))).ToLowerInvariant();
    var artifact = PathSecurity.VerifyArtifact(root, new ArtifactConfig(relative, digest));
    Equal(Path.GetFullPath(fullPath), artifact.FullPath);
  }

  private static void ArtifactDigestRejectsMismatch()
  {
    var root = Directory.GetCurrentDirectory();
    Throws("runtime_host_artifact_digest_mismatch", () => PathSecurity.VerifyArtifact(
        root,
        new ArtifactConfig("packaging/windows/OmniBase.RuntimeHost/Program.cs", new string('0', 64))));
  }

  private static void DataRootRejectsVolumeRoot()
  {
    Throws("runtime_host_data_root_invalid", () => PathSecurity.ValidateDataRoot("C:\\"));
  }

  private static void InstanceTokenAcceptsExactLowercaseHex()
  {
    True(InstanceEnvironment.IsValidToken("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"));
  }

  private static void InstanceTokenRejectsNoncanonicalValues()
  {
    True(!InstanceEnvironment.IsValidToken("too-short"));
    True(!InstanceEnvironment.IsValidToken(new string('A', 64)));
    True(!InstanceEnvironment.IsValidToken($"{new string('a', 63)}!"));
  }

  private static void InstanceEnvironmentRequiresExplicitValidValues()
  {
    const string tokenName = "OMNIBASE_DESKTOP_NATIVE_PROOF_KEY";
    const string controlName = "OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN";
    const string dataRootName = "OMNIBASE_DESKTOP_DATA_ROOT";
    const string token = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    const string control = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210";
    var oldToken = Environment.GetEnvironmentVariable(tokenName);
    var oldControl = Environment.GetEnvironmentVariable(controlName);
    var oldDataRoot = Environment.GetEnvironmentVariable(dataRootName);
    try
    {
      Environment.SetEnvironmentVariable(tokenName, token);
      Environment.SetEnvironmentVariable(controlName, control);
      Environment.SetEnvironmentVariable(dataRootName, Directory.GetCurrentDirectory());
      var loaded = InstanceEnvironment.Load();
      Equal(token, loaded.NativeProofKey);
      Equal(control, loaded.NativeControlToken);
      Equal(Path.TrimEndingDirectorySeparator(Path.GetFullPath(Directory.GetCurrentDirectory())), loaded.DataRoot);

      Environment.SetEnvironmentVariable(tokenName, null);
      Throws("runtime_host_native_proof_key_invalid", () => InstanceEnvironment.Load());
      Environment.SetEnvironmentVariable(tokenName, token);
      Environment.SetEnvironmentVariable(controlName, null);
      Throws("runtime_host_native_control_token_invalid", () => InstanceEnvironment.Load());
    }
    finally
    {
      Environment.SetEnvironmentVariable(tokenName, oldToken);
      Environment.SetEnvironmentVariable(controlName, oldControl);
      Environment.SetEnvironmentVariable(dataRootName, oldDataRoot);
    }
  }

  private static void OutputBudgetEnforcesPerStreamAndTotal()
  {
    var budget = new OutputBudget(10, 15);
    True(budget.TryConsume("a", 10));
    True(!budget.TryConsume("a", 1));
    True(budget.TryConsume("b", 5));
    True(!budget.TryConsume("b", 1));
  }

  private static void BackendStartInfoIsFixedAndClosed()
  {
    using var supervisor = CreateSupervisor();
    var info = supervisor.CreateBackendStartInfo();
    True(Path.IsPathFullyQualified(info.FileName));
    True(!info.UseShellExecute);
    True(info.RedirectStandardOutput && info.RedirectStandardError);
    SequenceEqual(
        new[]
        {
          "--host", "127.0.0.1", "--port", "31100", "--data-root", "D:\\OmniBaseData",
          "--application-version", "1.0.0",
        },
        info.ArgumentList);
    AssertEnvironment(info, new[]
    {
      "SystemRoot", "WINDIR", "TEMP", "TMP", "OMNIBASE_DESKTOP_INSTANCE_TOKEN",
      "OMNIBASE_DESKTOP_NATIVE_PROOF_KEY", "OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN",
      "OMNIBASE_DESKTOP_DATA_ROOT", "OMNIBASE_DESKTOP_MODE",
    });
    True(InstanceEnvironment.IsValidToken(info.Environment["OMNIBASE_DESKTOP_INSTANCE_TOKEN"]));
    Equal(
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        info.Environment["OMNIBASE_DESKTOP_NATIVE_PROOF_KEY"]!);
    True(
        info.Environment["OMNIBASE_DESKTOP_INSTANCE_TOKEN"] !=
        info.Environment["OMNIBASE_DESKTOP_NATIVE_PROOF_KEY"]);
    Equal(
        "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
        info.Environment["OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN"]!);
    True(
        info.Environment["OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN"] !=
        info.Environment["OMNIBASE_DESKTOP_NATIVE_PROOF_KEY"]);
    var frontendInfo = supervisor.CreateFrontendStartInfo();
    Equal(
        info.Environment["OMNIBASE_DESKTOP_INSTANCE_TOKEN"]!,
        frontendInfo.Environment["OMNIBASE_DESKTOP_INSTANCE_TOKEN"]!);
  }

  private static void FrontendStartInfoIsFixedAndClosed()
  {
    using var supervisor = CreateSupervisor();
    var info = supervisor.CreateFrontendStartInfo();
    Equal("C:\\OmniBase\\runtime\\node.exe", info.FileName);
    SequenceEqual(new[] { "C:\\OmniBase\\runtime\\frontend\\server.js" }, info.ArgumentList);
    Equal("127.0.0.1", info.Environment["HOSTNAME"]!);
    Equal("31101", info.Environment["PORT"]!);
    Equal("http://127.0.0.1:31100", info.Environment["API_PROXY_URL"]!);
    AssertEnvironment(info, new[]
    {
      "SystemRoot", "WINDIR", "TEMP", "TMP", "OMNIBASE_DESKTOP_INSTANCE_TOKEN",
      "OMNIBASE_DESKTOP_DATA_ROOT", "OMNIBASE_DESKTOP_MODE", "NODE_ENV",
      "HOSTNAME", "PORT", "API_PROXY_URL",
    });
    True(InstanceEnvironment.IsValidToken(info.Environment["OMNIBASE_DESKTOP_INSTANCE_TOKEN"]));
    True(!info.Environment.ContainsKey("OMNIBASE_DESKTOP_NATIVE_PROOF_KEY"));
    True(!info.Environment.ContainsKey("OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN"));
  }

  private static void WindowsJobObjectCanBeConfigured()
  {
    if (!OperatingSystem.IsWindows())
      return;
    using var job = WindowsJobObject.Create();
  }

  private static void SupervisorStartsAndConvergesProcessGroup()
  {
    var (supervisor, backendPort, frontendPort) = CreateIntegrationSupervisor("OmniBase.RuntimeHost.Tests.deps.json");
    using (supervisor)
    {
      var run = supervisor.RunAsync();
      True(WaitForPort(backendPort, TimeSpan.FromSeconds(5)));
      True(WaitForPort(frontendPort, TimeSpan.FromSeconds(5)));
      supervisor.RequestStop();
      Equal(OmniBase.RuntimeHost.Program.Ready, run.GetAwaiter().GetResult());
      True(supervisor.TrackedChildrenExited);
    }
  }

  private static void SupervisorFailsClosedWhenChildExits()
  {
    var (supervisor, _, _) = CreateIntegrationSupervisor("OmniBase.RuntimeHost.Tests.runtimeconfig.json");
    using (supervisor)
    {
      Equal(OmniBase.RuntimeHost.Program.ChildExited, supervisor.RunAsync().GetAwaiter().GetResult());
      True(supervisor.TrackedChildrenExited);
    }
  }

  private static void SupervisorFailsClosedOnOutputLimit()
  {
    var (supervisor, _, _) = CreateIntegrationSupervisor("OmniBase.RuntimeHost.Tests.dll", 4096, 8192);
    using (supervisor)
    {
      Equal(OmniBase.RuntimeHost.Program.OutputLimitExceeded, supervisor.RunAsync().GetAwaiter().GetResult());
      True(supervisor.TrackedChildrenExited);
    }
  }

  private static ChildProcessSupervisor CreateSupervisor()
  {
    var config = new RuntimeHostConfig(
        new ArtifactConfig("runtime/backend.exe", new string('a', 64)),
        new ArtifactConfig("runtime/frontend/server.js", new string('b', 64)),
        new ArtifactConfig("runtime/node.exe", new string('c', 64)),
        "1.0.0",
        31100,
        31101,
        10,
        5,
        65536,
        131072);
    var artifacts = new VerifiedArtifacts(
        new VerifiedArtifact("C:\\OmniBase\\runtime\\backend.exe", new string('a', 64)),
        new VerifiedArtifact("C:\\OmniBase\\runtime\\frontend\\server.js", new string('b', 64)),
        new VerifiedArtifact("C:\\OmniBase\\runtime\\node.exe", new string('c', 64)));
    return new ChildProcessSupervisor(
        "C:\\OmniBase",
        config,
        new InstanceEnvironment(
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
            "D:\\OmniBaseData"),
        artifacts);
  }

  private static (ChildProcessSupervisor Supervisor, int BackendPort, int FrontendPort)
      CreateIntegrationSupervisor(
          string frontendFileName,
          int perStreamLimit = 65536,
          int totalLimit = 131072)
  {
    var applicationRoot = Path.TrimEndingDirectorySeparator(Path.GetFullPath(AppContext.BaseDirectory));
    var executablePath = Path.Combine(applicationRoot, "OmniBase.RuntimeHost.Tests.exe");
    var frontendPath = Path.Combine(applicationRoot, frontendFileName);
    True(File.Exists(executablePath));
    True(File.Exists(frontendPath));
    var executableDigest = Digest(executablePath);
    var frontendDigest = Digest(frontendPath);
    var backendPort = ReservePort();
    var frontendPort = ReservePort();
    while (frontendPort == backendPort)
      frontendPort = ReservePort();

    var config = new RuntimeHostConfig(
        new ArtifactConfig("OmniBase.RuntimeHost.Tests.exe", executableDigest),
        new ArtifactConfig(frontendFileName, frontendDigest),
        new ArtifactConfig("OmniBase.RuntimeHost.Tests.exe", executableDigest),
        "1.0.0",
        backendPort,
        frontendPort,
        5,
        5,
        perStreamLimit,
        totalLimit);
    var artifacts = new VerifiedArtifacts(
        new VerifiedArtifact(executablePath, executableDigest),
        new VerifiedArtifact(frontendPath, frontendDigest),
        new VerifiedArtifact(executablePath, executableDigest));
    var supervisor = new ChildProcessSupervisor(
        applicationRoot,
        config,
        new InstanceEnvironment(
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
            applicationRoot),
        artifacts);
    return (supervisor, backendPort, frontendPort);
  }

  private static string Digest(string path) =>
      Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();

  private static int ReservePort()
  {
    var listener = new TcpListener(IPAddress.Loopback, 0);
    listener.Server.ExclusiveAddressUse = true;
    listener.Start();
    var port = ((IPEndPoint)listener.LocalEndpoint).Port;
    listener.Stop();
    return port;
  }

  private static bool WaitForPort(int port, TimeSpan timeout)
  {
    var deadline = DateTime.UtcNow.Add(timeout);
    do
    {
      using var client = new TcpClient(AddressFamily.InterNetwork);
      try
      {
        client.Connect(IPAddress.Loopback, port);
        return true;
      }
      catch (SocketException)
      {
        Thread.Sleep(25);
      }
    }
    while (DateTime.UtcNow < deadline);
    return false;
  }

  private static RuntimeHostConfig Parse(string json) =>
      RuntimeHostConfig.Parse(Encoding.UTF8.GetBytes(json));

  private static string ValidJson() => $$"""
      {
        "schema_version":1,
        "backend":{"path":"runtime/backend.exe","sha256":"{{new string('a', 64)}}"},
        "frontend":{"path":"runtime/frontend/server.js","sha256":"{{new string('b', 64)}}"},
        "node":{"path":"runtime/node.exe","sha256":"{{new string('c', 64)}}"},
        "application_version":"1.0.0",
        "backend_port":31100,
        "frontend_port":31101,
        "startup_timeout_seconds":30,
        "shutdown_timeout_seconds":10,
        "per_stream_output_limit_bytes":65536,
        "total_output_limit_bytes":131072
      }
      """;

  private static void AssertEnvironment(ProcessStartInfo info, IEnumerable<string> expected)
  {
    var expectedSet = new HashSet<string>(expected, StringComparer.OrdinalIgnoreCase);
    var actualSet = new HashSet<string>(info.Environment.Keys, StringComparer.OrdinalIgnoreCase);
    if (!actualSet.SetEquals(expectedSet))
      throw new InvalidOperationException(
          $"environment mismatch: {string.Join(',', actualSet.OrderBy(value => value, StringComparer.OrdinalIgnoreCase))}");
  }

  private static void Throws(string expectedCode, Action action)
  {
    try
    {
      action();
    }
    catch (HostFailureException exception) when (exception.Code == expectedCode)
    {
      return;
    }
    throw new InvalidOperationException($"expected HostFailureException {expectedCode}");
  }

  private static void Equal<T>(T expected, T actual) where T : notnull
  {
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
      throw new InvalidOperationException($"expected {expected}, got {actual}");
  }

  private static void True(bool value)
  {
    if (!value)
      throw new InvalidOperationException("expected true");
  }

  private static void SequenceEqual(IEnumerable<string> expected, IEnumerable<string> actual)
  {
    if (!expected.SequenceEqual(actual, StringComparer.Ordinal))
      throw new InvalidOperationException("sequence mismatch");
  }
}

internal static class FakeChild
{
  internal static async Task<int> RunAsync(IReadOnlyList<string> arguments)
  {
    if (arguments[0].EndsWith(".runtimeconfig.json", StringComparison.OrdinalIgnoreCase))
      return 0;
    if (arguments[0].EndsWith(".dll", StringComparison.OrdinalIgnoreCase))
      Console.Out.Write(new string('x', 8192));

    var port = arguments[0] == "--host"
        ? int.Parse(arguments[3], System.Globalization.CultureInfo.InvariantCulture)
        : int.Parse(Environment.GetEnvironmentVariable("PORT")!, System.Globalization.CultureInfo.InvariantCulture);
    var listener = new TcpListener(IPAddress.Loopback, port);
    listener.Server.ExclusiveAddressUse = true;
    listener.Start();
    while (true)
    {
      using var client = await listener.AcceptTcpClientAsync().ConfigureAwait(false);
      if (arguments[0] == "--host")
        await RespondToBackendHealthAsync(client).ConfigureAwait(false);
    }
  }

  private static async Task RespondToBackendHealthAsync(TcpClient client)
  {
    var stream = client.GetStream();
    var buffer = new byte[8192];
    var count = 0;
    while (count < buffer.Length)
    {
      var read = await stream.ReadAsync(buffer.AsMemory(count, buffer.Length - count))
          .ConfigureAwait(false);
      if (read == 0)
        break;
      count += read;
      if (Encoding.ASCII.GetString(buffer, 0, count).Contains("\r\n\r\n", StringComparison.Ordinal))
        break;
    }
    var headers = Encoding.ASCII.GetString(buffer, 0, count)
        .Split("\r\n", StringSplitOptions.None)
        .Skip(1)
        .Select(line => line.Split(':', 2))
        .Where(parts => parts.Length == 2)
        .ToDictionary(
            parts => parts[0].Trim(),
            parts => parts[1].Trim(),
            StringComparer.OrdinalIgnoreCase);
    var authorization = Environment.GetEnvironmentVariable("OMNIBASE_DESKTOP_INSTANCE_TOKEN");
    var proofKey = Environment.GetEnvironmentVariable("OMNIBASE_DESKTOP_NATIVE_PROOF_KEY");
    headers.TryGetValue("x-omnibase-desktop-instance", out var suppliedAuthorization);
    headers.TryGetValue("x-omnibase-desktop-challenge", out var challenge);
    var valid = authorization is not null &&
        proofKey is not null &&
        suppliedAuthorization == authorization &&
        InstanceEnvironment.IsValidToken(challenge) &&
        InstanceEnvironment.IsValidToken(proofKey);
    var proof = valid
        ? Convert.ToHexString(HMACSHA256.HashData(
            Convert.FromHexString(proofKey!),
            Encoding.ASCII.GetBytes(challenge!))).ToLowerInvariant()
        : string.Empty;
    var status = valid ? "200 OK" : "401 Unauthorized";
    var proofHeader = valid ? $"x-omnibase-desktop-proof: {proof}\r\n" : string.Empty;
    var raw = Encoding.ASCII.GetBytes(
        $"HTTP/1.1 {status}\r\nContent-Length: 2\r\n{proofHeader}Connection: close\r\n\r\nok");
    await stream.WriteAsync(raw).ConfigureAwait(false);
  }
}
