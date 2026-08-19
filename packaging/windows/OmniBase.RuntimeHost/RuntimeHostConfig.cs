using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;

namespace OmniBase.RuntimeHost;

internal sealed record ArtifactConfig(string Path, string Sha256);

internal sealed record RuntimeHostConfig(
    ArtifactConfig Backend,
    ArtifactConfig Frontend,
    ArtifactConfig Node,
    string ApplicationVersion,
    int BackendPort,
    int FrontendPort,
    int StartupTimeoutSeconds,
    int ShutdownTimeoutSeconds,
    int PerStreamOutputLimitBytes,
    int TotalOutputLimitBytes)
{
  private const int MaximumConfigBytes = 16 * 1024;

  internal static RuntimeHostConfig Load(string configPath)
  {
    PathSecurity.ValidateExistingRegularFile(configPath, "runtime_host_config_invalid");
    var bytes = File.ReadAllBytes(configPath);
    return Parse(bytes);
  }

  internal static RuntimeHostConfig Parse(ReadOnlyMemory<byte> bytes)
  {
    if (bytes.Length is 0 or > MaximumConfigBytes)
      throw new HostFailureException("runtime_host_config_size_invalid");

    using var document = JsonDocument.Parse(bytes, new JsonDocumentOptions
    {
      AllowTrailingCommas = false,
      CommentHandling = JsonCommentHandling.Disallow,
      MaxDepth = 4,
    });
    var root = document.RootElement;
    RequireClosedObject(root, new[]
    {
      "schema_version",
      "backend",
      "frontend",
      "node",
      "application_version",
      "backend_port",
      "frontend_port",
      "startup_timeout_seconds",
      "shutdown_timeout_seconds",
      "per_stream_output_limit_bytes",
      "total_output_limit_bytes",
    }, "runtime_host_config_schema_invalid");

    if (ReadInt(root, "schema_version") != 1)
      throw new HostFailureException("runtime_host_config_version_unsupported");

    var backend = ReadArtifact(root, "backend");
    var frontend = ReadArtifact(root, "frontend");
    var node = ReadArtifact(root, "node");
    var applicationVersion = ReadApplicationVersion(root);
    var backendPort = ReadRange(root, "backend_port", 1024, 65535);
    var frontendPort = ReadRange(root, "frontend_port", 1024, 65535);
    if (backendPort == frontendPort)
      throw new HostFailureException("runtime_host_ports_must_differ");

    var startupTimeout = ReadRange(root, "startup_timeout_seconds", 1, 120);
    var shutdownTimeout = ReadRange(root, "shutdown_timeout_seconds", 1, 30);
    var perStreamLimit = ReadRange(root, "per_stream_output_limit_bytes", 4096, 1024 * 1024);
    var totalLimit = ReadRange(root, "total_output_limit_bytes", perStreamLimit, 2 * 1024 * 1024);
    if (totalLimit > checked(perStreamLimit * 2))
      throw new HostFailureException("runtime_host_output_limits_invalid");

    return new RuntimeHostConfig(
        backend,
        frontend,
        node,
        applicationVersion,
        backendPort,
        frontendPort,
        startupTimeout,
        shutdownTimeout,
        perStreamLimit,
        totalLimit);
  }

  private static ArtifactConfig ReadArtifact(JsonElement root, string name)
  {
    var element = root.GetProperty(name);
    RequireClosedObject(element, new[] { "path", "sha256" }, "runtime_host_artifact_schema_invalid");
    var path = ReadString(element, "path", 1, 512);
    var digest = ReadString(element, "sha256", 64, 64);
    if (!digest.All(IsHexDigit))
      throw new HostFailureException("runtime_host_artifact_digest_invalid");
    return new ArtifactConfig(path, digest.ToLowerInvariant());
  }

  private static string ReadApplicationVersion(JsonElement root)
  {
    var value = ReadString(root, "application_version", 1, 64);
    for (var index = 0; index < value.Length; index++)
    {
      var character = value[index];
      var allowed = character is >= '0' and <= '9' or >= 'A' and <= 'Z' or
          >= 'a' and <= 'z' || index > 0 && character is '.' or '+' or '-';
      if (!allowed)
        throw new HostFailureException("runtime_host_application_version_invalid");
    }
    return value;
  }

  private static string ReadString(JsonElement element, string name, int minimum, int maximum)
  {
    var property = element.GetProperty(name);
    if (property.ValueKind != JsonValueKind.String)
      throw new HostFailureException("runtime_host_config_type_invalid");
    var value = property.GetString();
    if (value is null || value.Length < minimum || value.Length > maximum || value != value.Trim())
      throw new HostFailureException("runtime_host_config_value_invalid");
    return value;
  }

  private static int ReadRange(JsonElement root, string name, int minimum, int maximum)
  {
    var value = ReadInt(root, name);
    if (value < minimum || value > maximum)
      throw new HostFailureException("runtime_host_config_value_invalid");
    return value;
  }

  private static int ReadInt(JsonElement root, string name)
  {
    var property = root.GetProperty(name);
    if (property.ValueKind != JsonValueKind.Number || !property.TryGetInt32(out var value))
      throw new HostFailureException("runtime_host_config_type_invalid");
    return value;
  }

  private static void RequireClosedObject(JsonElement element, IEnumerable<string> expected, string code)
  {
    if (element.ValueKind != JsonValueKind.Object)
      throw new HostFailureException(code);
    var expectedNames = new HashSet<string>(expected, StringComparer.Ordinal);
    var seen = new HashSet<string>(StringComparer.Ordinal);
    foreach (var property in element.EnumerateObject())
    {
      if (!expectedNames.Contains(property.Name) || !seen.Add(property.Name))
        throw new HostFailureException(code);
    }
    if (!seen.SetEquals(expectedNames))
      throw new HostFailureException(code);
  }

  private static bool IsHexDigit(char value) =>
      value is >= '0' and <= '9' or >= 'a' and <= 'f' or >= 'A' and <= 'F';
}
