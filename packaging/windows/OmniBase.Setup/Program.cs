using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

return Companion.Run(args);

static class Companion
{
  const int Ready = 0;
  const int NeedsAction = 10;
  const int Unsupported = 20;
  const int SecurityFailure = 30;
  const int ImagesNotPublished = 40;

  public static int Run(string[] args)
  {
    try
    {
      if (args.Length == 1 && args[0] == "help")
        return Help();
      if (args.Length is 1 or 2 && args[0] == "locations")
        return LocationsCommand(args);
      if (args.Length >= 3 && args[0] == "plan-install")
        return PlanInstallCommand(args);
      if (args.Length == 3 && args[0] == "--verify-and-extract")
        return Install(args[1], args[2]);
      if (args.Length == 2 && args[0] == "verify")
        return Verify(args[1]);
      if (args.Length == 3 && args[0] == "install")
        return Install(args[1], args[2]);
      if (args.Length == 3 && args[0] == "init-config" && args[1] == "--output")
        return InitConfig(args[2]);
      if (args.Length >= 3 && args[0] == "doctor")
        return DoctorCommand(args);
      return Fail(SecurityFailure, "usage_invalid");
    }
    catch (CompanionFailureException exception)
    {
      return Fail(SecurityFailure, exception.Code);
    }
    catch (Exception exception) when (exception is IOException or InvalidDataException or
        JsonException or UnauthorizedAccessException or OverflowException or
        CryptographicException or ArgumentException or InvalidOperationException or
        KeyNotFoundException or FormatException)
    {
      return Fail(SecurityFailure, "companion_operation_failed");
    }
  }

  static int Help()
  {
    Console.WriteLine("OmniBase Windows Companion (engineering preview)");
    Console.WriteLine("help");
    Console.WriteLine("locations [--json]");
    Console.WriteLine("plan-install --scope user|machine|custom [--target <absolute-path>] [--json]");
    Console.WriteLine("verify <release.zip>");
    Console.WriteLine("install <release.zip> <new-absolute-local-target>");
    Console.WriteLine("init-config --output <operator.env>");
    Console.WriteLine("doctor --install <install-dir> [--env-file <operator.env>] [--json]");
    Console.WriteLine("Machine scope is planning-only. The Companion never elevates through UAC.");
    Console.WriteLine("Install is frozen: path-identity binding is not implemented and no files are written.");
    Console.WriteLine("Custom/elevated install acceptance and handle-relative rename remain not proven.");
    Console.WriteLine("No command changes PATH, registry, services, firewall, Docker, WSL or VHDX.");
    return Ready;
  }

  static int LocationsCommand(IReadOnlyList<string> args)
  {
    var json = args.Count == 2 && args[1] == "--json";
    if (args.Count == 2 && !json)
      return Fail(SecurityFailure, "locations_usage_invalid");
    var locations = InstallLocations.Resolve();
    if (json)
    {
      Console.WriteLine(JsonSerializer.Serialize(new
      {
        schema_version = 1,
        user = new
        {
          install_path = locations.UserInstall,
          config_path = locations.UserConfig,
          requires_elevation = false,
        },
        machine = new
        {
          install_path = locations.MachineInstall,
          config_path = locations.MachineConfig,
          requires_elevation = true,
          planning_only = true,
        },
        custom = new
        {
          requires_absolute_local_path = true,
          config_path = locations.UserConfig,
        },
        mutation_performed = false,
      }));
    }
    else
    {
      Console.WriteLine($"user.install={locations.UserInstall}");
      Console.WriteLine($"user.config={locations.UserConfig}");
      Console.WriteLine($"machine.install={locations.MachineInstall}");
      Console.WriteLine($"machine.config={locations.MachineConfig}");
      Console.WriteLine("machine.requires_elevation=true; machine.planning_only=true");
      Console.WriteLine("custom.requires_absolute_local_path=true");
      Console.WriteLine("mutation_performed=false");
    }
    return Ready;
  }

  static int PlanInstallCommand(IReadOnlyList<string> args)
  {
    string? scope = null;
    string? customTarget = null;
    var json = false;
    for (var index = 1; index < args.Count; index++)
    {
      if (args[index] == "--scope" && scope is null && index + 1 < args.Count)
      {
        scope = args[++index];
        continue;
      }
      if (args[index] == "--target" && customTarget is null && index + 1 < args.Count)
      {
        customTarget = args[++index];
        continue;
      }
      if (args[index] == "--json" && !json)
      {
        json = true;
        continue;
      }
      return Fail(SecurityFailure, "plan_install_usage_invalid");
    }
    if (scope is not ("user" or "machine" or "custom") ||
        (scope == "custom") != (customTarget is not null) ||
        (scope != "custom" && customTarget is not null))
      return Fail(SecurityFailure, "plan_install_usage_invalid");

    var plan = InstallPlan.Create(scope, customTarget);
    if (json)
    {
      Console.WriteLine(JsonSerializer.Serialize(new
      {
        schema_version = 1,
        scope = plan.Scope,
        install_path = plan.InstallPath,
        config_path = plan.ConfigPath,
        requires_elevation = plan.RequiresElevation,
        machine_install_is_planning_only = plan.Scope == "machine",
        custom_or_elevated_acceptance_proven = false,
        handle_relative_install_proven = false,
        target_state = "new",
        mutation_performed = false,
      }));
    }
    else
    {
      Console.WriteLine($"scope={plan.Scope}");
      Console.WriteLine($"install_path={plan.InstallPath}");
      Console.WriteLine($"config_path={plan.ConfigPath}");
      Console.WriteLine($"requires_elevation={plan.RequiresElevation.ToString().ToLowerInvariant()}");
      Console.WriteLine($"machine_install_is_planning_only={(plan.Scope == "machine").ToString().ToLowerInvariant()}");
      Console.WriteLine("custom_or_elevated_acceptance_proven=false");
      Console.WriteLine("handle_relative_install_proven=false");
      Console.WriteLine("target_state=new");
      Console.WriteLine("mutation_performed=false");
    }
    return Ready;
  }

  static int Verify(string archivePath)
  {
    using var verified = VerifiedRelease.Open(archivePath);
    Console.WriteLine("release_integrity_verified");
    Console.WriteLine("production_ready=false; publisher_signature=not_proven; authenticode=not_signed");
    return Ready;
  }

  static int Install(string archivePath, string targetPath)
  {
    _ = archivePath;
    _ = targetPath;
    return Fail(SecurityFailure, "install_path_identity_binding_not_implemented");
  }

  static int InitConfig(string outputPath)
  {
    var output = Path.GetFullPath(outputPath);
    if (File.Exists(output) || Directory.Exists(output))
      return Fail(SecurityFailure, "config_target_exists");
    Directory.CreateDirectory(Path.GetDirectoryName(output)!);
    var postgres = Secret(24);
    var redis = Secret(24);
    var minio = Secret(24);
    var jwt = Secret(48);
    var providerKey = Base64Url(RandomNumberGenerator.GetBytes(32));
    var memoryKey = Base64Url(RandomNumberGenerator.GetBytes(32));
    while (memoryKey == providerKey) memoryKey = Base64Url(RandomNumberGenerator.GetBytes(32));
    var lines = new[]
    {
            "# Generated by OmniBase Companion. Keep this file outside source and release directories.",
            "OMNIBASE_FRONTEND_PORT=3000",
            "OMNIBASE_BACKEND_IMAGE=ghcr.io/lss100200/omnibase-backend@sha256:REPLACE_WITH_64_HEX_DIGEST",
            "OMNIBASE_FRONTEND_IMAGE=ghcr.io/lss100200/omnibase-frontend@sha256:REPLACE_WITH_64_HEX_DIGEST",
            "OMNIBASE_POSTGRES_IMAGE=pgvector/pgvector@sha256:REPLACE_WITH_64_HEX_DIGEST",
            "OMNIBASE_REDIS_IMAGE=redis@sha256:REPLACE_WITH_64_HEX_DIGEST",
            "OMNIBASE_MINIO_IMAGE=minio/minio@sha256:REPLACE_WITH_64_HEX_DIGEST",
            "OMNIBASE_MINIO_MC_IMAGE=minio/mc@sha256:REPLACE_WITH_64_HEX_DIGEST",
            "POSTGRES_USER=omnibase",
            $"POSTGRES_PASSWORD={postgres}",
            "POSTGRES_DB=omnibase",
            $"DATABASE_URL=postgresql+psycopg://omnibase:{Uri.EscapeDataString(postgres)}@postgres:5432/omnibase",
            $"REDIS_PASSWORD={redis}",
            $"REDIS_URL=redis://:{Uri.EscapeDataString(redis)}@redis:6379/0",
            "MINIO_ROOT_USER=omnibase",
            $"MINIO_ROOT_PASSWORD={minio}",
            "MINIO_BUCKET=omnibase-files",
            $"JWT_SECRET={jwt}",
            $"PROVIDER_CREDENTIAL_ENCRYPTION_KEY={providerKey}",
            $"MEMORY_CONTENT_ENCRYPTION_KEY={memoryKey}",
            "PROVIDER_ENDPOINT_ALLOWLIST=[\"api.deepseek.com\",\"api.openai.com\"]",
            "CORS_ORIGINS=[\"http://127.0.0.1:3000\"]",
            $"OMNIBASE_DEPLOYMENT_INSTANCE_ID={Guid.NewGuid()}",
            "AGENT_RUNTIME_ENABLED=false",
            "AGENT_PLANNER_ENABLED=false",
            "MULTI_AGENT_ENABLED=false",
            "MCP_RUNTIME_ENABLED=false",
        };
    using var stream = new FileStream(output, FileMode.CreateNew, FileAccess.Write, FileShare.None);
    using var writer = new StreamWriter(stream, new UTF8Encoding(false));
    foreach (var line in lines) writer.WriteLine(line);
    Console.WriteLine("config_initialized_without_secret_echo");
    Console.WriteLine("image_metadata remains publisher-owned and unpublished");
    return Ready;
  }

  static int DoctorCommand(IReadOnlyList<string> args)
  {
    if (args.Count < 3 || args[1] != "--install")
      return Fail(SecurityFailure, "doctor_usage_invalid");
    string? configPath = null;
    var json = false;
    for (var index = 3; index < args.Count; index++)
    {
      if (args[index] == "--json" && !json)
      {
        json = true;
        continue;
      }
      if (args[index] == "--env-file" && configPath is null && index + 1 < args.Count)
      {
        configPath = args[++index];
        continue;
      }
      return Fail(SecurityFailure, "doctor_usage_invalid");
    }
    return Doctor(args[2], configPath, json);
  }

  static int Doctor(string installPath, string? explicitConfigPath, bool json)
  {
    var checks = new List<DoctorCheck>();
    var install = Path.GetFullPath(installPath);
    var releaseIntegrity = false;
    if (Directory.Exists(install))
    {
      try { VerifiedRelease.VerifyDirectory(install); releaseIntegrity = true; }
      catch (Exception exception) when (exception is IOException or InvalidDataException or
          JsonException or UnauthorizedAccessException or OverflowException or
          CryptographicException or ArgumentException or InvalidOperationException or
          KeyNotFoundException or FormatException)
      { }
    }
    checks.Add(Check("RELEASE_INTEGRITY", "INSTALLED_RELEASE", releaseIntegrity,
        !Directory.Exists(install) ? "INSTALL_DIRECTORY_MISSING" :
        releaseIntegrity ? "PASS" : "INSTALLED_RELEASE_INTEGRITY_FAILED"));
    if (!OperatingSystem.IsWindows())
      checks.Add(Check("HOST", "WINDOWS_X64", false, "WINDOWS_X64_REQUIRED"));
    else
      checks.Add(Check("HOST", "WINDOWS_X64", Environment.Is64BitOperatingSystem,
          Environment.Is64BitOperatingSystem ? "PASS" : "WINDOWS_X64_REQUIRED"));

    var root = Path.GetPathRoot(install) ?? install;
    try
    {
      var drive = new DriveInfo(root);
      checks.Add(Check("HOST", "DISK", drive.AvailableFreeSpace >= 20L * 1024 * 1024 * 1024,
          drive.AvailableFreeSpace >= 20L * 1024 * 1024 * 1024 ? "PASS" : "INSUFFICIENT_DISK"));
    }
    catch { checks.Add(Check("HOST", "DISK", false, "DISK_STATUS_UNAVAILABLE")); }

    var dockerVersion = RunBounded("docker", new[] { "--version" }, 4);
    checks.Add(Check("HOST", "DOCKER_CLI", dockerVersion.Success,
        dockerVersion.Success ? "PASS" : "DOCKER_CLI_NOT_FOUND"));
    var daemon = dockerVersion.Success ? RunBounded("docker", new[] { "info", "--format", "{{.OSType}}" }, 5) : default;
    checks.Add(Check("HOST", "DOCKER_DAEMON", daemon.Success && daemon.Output.Trim() == "linux",
        !dockerVersion.Success ? "DOCKER_CLI_NOT_FOUND" : !daemon.Success ? "DOCKER_DAEMON_NOT_RUNNING" : daemon.Output.Trim() == "linux" ? "PASS" : "DOCKER_WINDOWS_CONTAINER_MODE"));
    var compose = dockerVersion.Success ? RunBounded("docker", new[] { "compose", "version", "--short" }, 4) : default;
    checks.Add(Check("HOST", "COMPOSE", compose.Success, compose.Success ? "PASS" : "COMPOSE_PLUGIN_NOT_FOUND"));
    var wsl = OperatingSystem.IsWindows() ? RunBounded("wsl.exe", new[] { "--status" }, 4) : default;
    checks.Add(Check("HOST", "WSL2", wsl.Success, wsl.Success ? "PASS" : "WSL2_NOT_AVAILABLE"));

    var envFile = Path.Combine(install, "operator.env");
    var template = Path.Combine(install, "deployment", "release", "windows", "operator.env.template");
    var configPath = explicitConfigPath is null
        ? (File.Exists(envFile) ? envFile : template)
        : Path.GetFullPath(explicitConfigPath);
    var config = ConfigInspector.Inspect(configPath);
    checks.Add(Check("CONFIG", "ENV", config.Valid, config.Code));
    checks.Add(Check("IMAGE_METADATA", "DIGESTS", config.Valid && config.ImagesPublished,
        !config.Valid ? "CONFIG_NOT_VALIDATED" :
        config.ImagesPublished ? "PASS" : "RELEASE_IMAGES_NOT_PUBLISHED"));
    checks.Add(Check("RUNTIME", "FEATURE_GATES", config.Valid && config.FeatureGatesClosed,
        !config.Valid ? "CONFIG_NOT_VALIDATED" :
        config.FeatureGatesClosed ? "PASS" : "FEATURE_GATE_MUST_REMAIN_FALSE"));

    var securityFailure = !releaseIntegrity ||
        (!config.Valid && config.Code != "CONFIG_FILE_MISSING") ||
        checks.Any(item => item.Code == "FEATURE_GATE_MUST_REMAIN_FALSE");
    var unsupported = checks.Any(item => item.Code is "WINDOWS_X64_REQUIRED");
    var imagesMissing = checks.Any(item => item.Code == "RELEASE_IMAGES_NOT_PUBLISHED");
    var needsAction = checks.Any(item => !item.Passed);
    var exit = securityFailure ? SecurityFailure : unsupported ? Unsupported : imagesMissing ? ImagesNotPublished : needsAction ? NeedsAction : Ready;
    var overall = exit == Ready ? "READY_FOR_PULL" : exit == ImagesNotPublished ? "NOT_READY_FOR_PULL" : "NEEDS_ACTION";
    if (json)
      Console.WriteLine(JsonSerializer.Serialize(new { schema_version = 1, overall, exit_code = exit, checks, mutation_performed = false }));
    else
    {
      Console.WriteLine($"OmniBase 离线诊断：{overall}");
      foreach (var check in checks) Console.WriteLine($"[{check.Section}] {check.Name}: {check.Code}");
      Console.WriteLine("本次诊断未启动 Docker/WSL，未拉取镜像，未修改 VHDX 或系统配置。");
    }
    return exit;
  }

  static DoctorCheck Check(string section, string name, bool passed, string code) => new(section, name, passed, code);

  static ProcessResult RunBounded(string executable, IReadOnlyList<string> arguments, int timeoutSeconds)
  {
    try
    {
      var startInfo = new ProcessStartInfo(executable)
      {
        UseShellExecute = false,
        RedirectStandardOutput = true,
        RedirectStandardError = true,
        CreateNoWindow = true,
      };
      foreach (var argument in arguments) startInfo.ArgumentList.Add(argument);
      using var process = Process.Start(startInfo);
      if (process is null || !process.WaitForExit(timeoutSeconds * 1000))
      {
        try { process?.Kill(entireProcessTree: true); } catch { }
        return new(false, "");
      }
      var output = process.StandardOutput.ReadToEnd();
      return new(process.ExitCode == 0 && output.Length <= 64 * 1024, output);
    }
    catch { return new(false, ""); }
  }

  static string Secret(int bytes) => Base64Url(RandomNumberGenerator.GetBytes(bytes));
  static string Base64Url(byte[] bytes) => Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
  static int Fail(int code, string reason) { Console.Error.WriteLine(reason); return code; }

  readonly record struct ProcessResult(bool Success, string Output);
  readonly record struct DoctorCheck(string Section, string Name, bool Passed, string Code);
}

sealed class CompanionFailureException(string code) : Exception(code)
{
  public string Code { get; } = code;
}

readonly record struct InstallLocations(
    string UserInstall,
    string UserConfig,
    string MachineInstall,
    string MachineConfig)
{
  public static InstallLocations Resolve()
  {
    if (!OperatingSystem.IsWindows())
      throw new CompanionFailureException("windows_install_locations_unavailable");
    var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
    var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
    var programData = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
    if (string.IsNullOrWhiteSpace(local) || string.IsNullOrWhiteSpace(programFiles) ||
        string.IsNullOrWhiteSpace(programData))
      throw new CompanionFailureException("windows_install_locations_unavailable");
    return new InstallLocations(
        Path.Combine(local, "Programs", "OmniBase"),
        Path.Combine(local, "OmniBase", "config", "operator.env"),
        Path.Combine(programFiles, "OmniBase"),
        Path.Combine(programData, "OmniBase", "config", "operator.env"));
  }
}

readonly record struct InstallPlan(
    string Scope,
    string InstallPath,
    string ConfigPath,
    bool RequiresElevation)
{
  public static InstallPlan Create(string scope, string? customTarget)
  {
    var locations = InstallLocations.Resolve();
    var target = scope switch
    {
      "user" => locations.UserInstall,
      "machine" => locations.MachineInstall,
      "custom" => customTarget!,
      _ => throw new CompanionFailureException("plan_install_scope_invalid"),
    };
    var config = scope == "machine" ? locations.MachineConfig : locations.UserConfig;
    return new InstallPlan(
        scope,
        InstallPathPolicy.ValidateNewTarget(target),
        Path.GetFullPath(config),
        scope == "machine");
  }
}

static class InstallPathPolicy
{
  public static string ValidateNewTarget(string input)
  {
    if (string.IsNullOrWhiteSpace(input) || !Path.IsPathFullyQualified(input))
      throw new CompanionFailureException("install_target_must_be_absolute");
    var full = Path.TrimEndingDirectorySeparator(Path.GetFullPath(input));
    if (full.StartsWith("\\\\", StringComparison.Ordinal) ||
        full.StartsWith("//", StringComparison.Ordinal))
      throw new CompanionFailureException("install_target_unc_forbidden");
    var root = Path.GetPathRoot(full);
    if (string.IsNullOrEmpty(root))
      throw new CompanionFailureException("install_target_root_unavailable");
    var normalizedRoot = Path.TrimEndingDirectorySeparator(Path.GetFullPath(root));
    if (string.Equals(full, normalizedRoot, StringComparison.OrdinalIgnoreCase))
      throw new CompanionFailureException("install_target_root_forbidden");
    if (full[root.Length..].Contains(':'))
      throw new CompanionFailureException("install_target_ads_forbidden");
    foreach (var component in full[root.Length..].Split(
        new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar },
        StringSplitOptions.RemoveEmptyEntries))
      if (component.EndsWith(' ') || component.EndsWith('.'))
        throw new CompanionFailureException("install_target_component_invalid");
    RejectNetworkVolume(root);
    RejectExistingReparseAncestors(full);
    if (File.Exists(full) || Directory.Exists(full))
      throw new CompanionFailureException("install_target_exists");
    return full;
  }

  static void RejectNetworkVolume(string root)
  {
    try
    {
      var drive = new DriveInfo(root);
      if (drive.DriveType is DriveType.Network or DriveType.NoRootDirectory or DriveType.Unknown)
        throw new CompanionFailureException("install_target_volume_forbidden");
    }
    catch (CompanionFailureException) { throw; }
    catch (Exception exception) when (exception is ArgumentException or IOException or UnauthorizedAccessException)
    {
      throw new CompanionFailureException("install_target_volume_unavailable");
    }
  }

  static void RejectExistingReparseAncestors(string full)
  {
    string? current = full;
    while (!string.IsNullOrEmpty(current))
    {
      if ((File.Exists(current) || Directory.Exists(current)) &&
          File.GetAttributes(current).HasFlag(FileAttributes.ReparsePoint))
        throw new CompanionFailureException("install_target_reparse_forbidden");
      var parent = Path.GetDirectoryName(current);
      if (string.IsNullOrEmpty(parent) || string.Equals(parent, current, StringComparison.OrdinalIgnoreCase))
        break;
      current = parent;
    }
  }
}

sealed class VerifiedRelease : IDisposable
{
  const int MaxArchiveEntries = 16;
  const long MaxManifestBytes = 64 * 1024;
  const long MaxFileBytes = 2 * 1024 * 1024;
  const long MaxTotalBytes = 10 * 1024 * 1024;
  const long MaxCompressionRatio = 100;
  static readonly HashSet<string> ExpectedPayload = new(StringComparer.Ordinal)
    {
        "LICENSE", "deployment/release/windows/README.zh-CN.md", "deployment/release/windows/compose.yml",
        "deployment/release/windows/operator.env.template", "scripts/release/validate_windows_release_config.py",
    };
  readonly ZipArchive archive;
  readonly Dictionary<string, ZipArchiveEntry> entries;

  VerifiedRelease(ZipArchive archive, Dictionary<string, ZipArchiveEntry> entries) { this.archive = archive; this.entries = entries; }

  public static VerifiedRelease Open(string input)
  {
    var path = Path.GetFullPath(input);
    if (!File.Exists(path) || File.GetAttributes(path).HasFlag(FileAttributes.ReparsePoint))
      throw new InvalidDataException("release_input_invalid");
    var archive = ZipFile.OpenRead(path);
    try
    {
      if (archive.Entries.Count is < 2 or > MaxArchiveEntries) throw new InvalidDataException("release_archive_entry_count_invalid");
      var entries = new Dictionary<string, ZipArchiveEntry>(StringComparer.Ordinal);
      long total = 0;
      foreach (var entry in archive.Entries)
      {
        if (!entries.TryAdd(entry.FullName, entry) || !ValidArchivePath(entry.FullName) || string.IsNullOrEmpty(entry.Name))
          throw new InvalidDataException("release_archive_path_invalid");
        checked { total += entry.Length; }
        if (entry.Length < 0 || entry.Length > MaxFileBytes || entry.CompressedLength < 0 || total > MaxTotalBytes)
          throw new InvalidDataException("release_archive_size_invalid");
        if (entry.Length > 0 &&
            (entry.CompressedLength == 0 ||
             entry.CompressedLength < (entry.Length + MaxCompressionRatio - 1) / MaxCompressionRatio))
          throw new InvalidDataException("release_archive_compression_ratio_invalid");
      }
      if (!entries.TryGetValue("release.json", out var manifestEntry) ||
          manifestEntry.Length is <= 0 or > MaxManifestBytes)
        throw new InvalidDataException("release_manifest_missing_or_size_invalid");
      using var document = JsonDocument.Parse(manifestEntry.Open(), new JsonDocumentOptions
      {
        AllowTrailingCommas = false,
        CommentHandling = JsonCommentHandling.Disallow,
        MaxDepth = 8,
      });
      var root = document.RootElement;
      var expectedManifestKeys = new HashSet<string>(StringComparer.Ordinal)
            {
                "schema_version", "product", "release", "platform", "source_commit",
                "production_ready", "requires_digest_pinned_images",
                "publisher_signature_verified", "authenticode_verified",
                "vhdx_mutation_allowed", "files",
            };
      if (root.ValueKind != JsonValueKind.Object || !ExactKeys(root, expectedManifestKeys) ||
          !ExactInt(root, "schema_version", 1) ||
          !ExactString(root, "product", "OmniBase") ||
          !ExactString(root, "release", "v1.0.0-preview") ||
          !ExactString(root, "platform", "windows-x64") ||
          !ExactBoolean(root, "production_ready", false) ||
          !ExactBoolean(root, "requires_digest_pinned_images", true) ||
          !ExactBoolean(root, "publisher_signature_verified", false) ||
          !ExactBoolean(root, "authenticode_verified", false) ||
          !ExactBoolean(root, "vhdx_mutation_allowed", false))
        throw new InvalidDataException("release_manifest_schema_or_posture_invalid");
      var sourceCommit = root.GetProperty("source_commit");
      if (sourceCommit.ValueKind != JsonValueKind.String ||
          !Regex.IsMatch(sourceCommit.GetString()!, "^[0-9a-f]{40}$", RegexOptions.CultureInvariant))
        throw new InvalidDataException("release_manifest_source_commit_invalid");
      var files = root.GetProperty("files");
      if (files.ValueKind != JsonValueKind.Array || files.GetArrayLength() != ExpectedPayload.Count)
        throw new InvalidDataException("release_manifest_files_invalid");
      var manifestPaths = new HashSet<string>(StringComparer.Ordinal);
      var expectedFileKeys = new HashSet<string>(StringComparer.Ordinal) { "path", "sha256", "size" };
      foreach (var file in files.EnumerateArray())
      {
        if (file.ValueKind != JsonValueKind.Object || !ExactKeys(file, expectedFileKeys))
          throw new InvalidDataException("release_manifest_file_schema_invalid");
        var relative = file.GetProperty("path").GetString() ?? "";
        var digest = file.GetProperty("sha256").GetString() ?? "";
        var size = file.GetProperty("size").GetInt64();
        if (size < 0 || size > MaxFileBytes || !ExpectedPayload.Contains(relative) ||
            !manifestPaths.Add(relative) || !entries.TryGetValue(relative, out var entry) ||
            entry.Length != size ||
            !Regex.IsMatch(digest, "^[0-9a-f]{64}$", RegexOptions.CultureInvariant))
          throw new InvalidDataException("release_file_metadata_invalid");
        using var stream = entry.Open();
        if (!CryptographicOperations.FixedTimeEquals(SHA256.HashData(stream), Convert.FromHexString(digest)))
          throw new InvalidDataException("release_file_digest_drifted");
      }
      if (!manifestPaths.SetEquals(ExpectedPayload) || !entries.Keys.ToHashSet(StringComparer.Ordinal).SetEquals(ExpectedPayload.Append("release.json")))
        throw new InvalidDataException("release_archive_closed_set_drifted");
      return new VerifiedRelease(archive, entries);
    }
    catch { archive.Dispose(); throw; }
  }

  public static void VerifyDirectory(string input)
  {
    var root = Path.TrimEndingDirectorySeparator(Path.GetFullPath(input));
    if (!Directory.Exists(root) || File.GetAttributes(root).HasFlag(FileAttributes.ReparsePoint))
      throw new InvalidDataException("installed_release_root_invalid");
    var expectedFiles = ExpectedPayload.Append("release.json").ToHashSet(StringComparer.Ordinal);
    var expectedDirectories = new HashSet<string>(StringComparer.Ordinal)
        {
            "deployment", "deployment/release", "deployment/release/windows",
            "scripts", "scripts/release",
        };
    var actualFiles = new HashSet<string>(StringComparer.Ordinal);
    var actualDirectories = new HashSet<string>(StringComparer.Ordinal);
    var pending = new Stack<DirectoryInfo>();
    pending.Push(new DirectoryInfo(root));
    long total = 0;
    while (pending.Count > 0)
    {
      foreach (var item in pending.Pop().EnumerateFileSystemInfos())
      {
        if (item.Attributes.HasFlag(FileAttributes.ReparsePoint))
          throw new InvalidDataException("installed_release_reparse_forbidden");
        var relative = Path.GetRelativePath(root, item.FullName).Replace('\\', '/');
        if (!ValidArchivePath(relative))
          throw new InvalidDataException("installed_release_path_invalid");
        if (item is DirectoryInfo directory)
        {
          if (!actualDirectories.Add(relative))
            throw new InvalidDataException("installed_release_directory_duplicate");
          pending.Push(directory);
          continue;
        }
        if (item is not FileInfo file || !actualFiles.Add(relative) || file.Length > MaxFileBytes)
          throw new InvalidDataException("installed_release_file_invalid");
        checked { total += file.Length; }
        if (total > MaxTotalBytes) throw new InvalidDataException("installed_release_size_invalid");
      }
    }
    if (!actualFiles.SetEquals(expectedFiles) || !actualDirectories.SetEquals(expectedDirectories))
      throw new InvalidDataException("installed_release_closed_set_drifted");
    var manifestPath = Path.Combine(root, "release.json");
    var manifestLength = new FileInfo(manifestPath).Length;
    if (manifestLength is <= 0 or > MaxManifestBytes)
      throw new InvalidDataException("installed_release_manifest_size_invalid");
    using var document = JsonDocument.Parse(File.OpenRead(manifestPath), new JsonDocumentOptions
    {
      AllowTrailingCommas = false,
      CommentHandling = JsonCommentHandling.Disallow,
      MaxDepth = 8,
    });
    var manifest = document.RootElement;
    var expectedManifestKeys = new HashSet<string>(StringComparer.Ordinal)
        {
            "schema_version", "product", "release", "platform", "source_commit",
            "production_ready", "requires_digest_pinned_images",
            "publisher_signature_verified", "authenticode_verified",
            "vhdx_mutation_allowed", "files",
        };
    if (manifest.ValueKind != JsonValueKind.Object || !ExactKeys(manifest, expectedManifestKeys) ||
        !ExactInt(manifest, "schema_version", 1) ||
        !ExactString(manifest, "product", "OmniBase") ||
        !ExactString(manifest, "release", "v1.0.0-preview") ||
        !ExactString(manifest, "platform", "windows-x64") ||
        !ExactBoolean(manifest, "production_ready", false) ||
        !ExactBoolean(manifest, "requires_digest_pinned_images", true) ||
        !ExactBoolean(manifest, "publisher_signature_verified", false) ||
        !ExactBoolean(manifest, "authenticode_verified", false) ||
        !ExactBoolean(manifest, "vhdx_mutation_allowed", false))
      throw new InvalidDataException("installed_release_manifest_invalid");
    var sourceCommit = manifest.GetProperty("source_commit");
    if (sourceCommit.ValueKind != JsonValueKind.String ||
        !Regex.IsMatch(sourceCommit.GetString()!, "^[0-9a-f]{40}$", RegexOptions.CultureInvariant))
      throw new InvalidDataException("installed_release_source_commit_invalid");
    var manifestFiles = manifest.GetProperty("files");
    if (manifestFiles.ValueKind != JsonValueKind.Array || manifestFiles.GetArrayLength() != ExpectedPayload.Count)
      throw new InvalidDataException("installed_release_manifest_files_invalid");
    var manifestPaths = new HashSet<string>(StringComparer.Ordinal);
    var expectedFileKeys = new HashSet<string>(StringComparer.Ordinal) { "path", "sha256", "size" };
    foreach (var file in manifestFiles.EnumerateArray())
    {
      if (file.ValueKind != JsonValueKind.Object || !ExactKeys(file, expectedFileKeys))
        throw new InvalidDataException("installed_release_file_schema_invalid");
      var relative = file.GetProperty("path").GetString() ?? "";
      var digest = file.GetProperty("sha256").GetString() ?? "";
      var size = file.GetProperty("size").GetInt64();
      var path = Path.GetFullPath(Path.Combine(root, relative));
      if (!ExpectedPayload.Contains(relative) || !manifestPaths.Add(relative) ||
          !path.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) ||
          !File.Exists(path) || new FileInfo(path).Length != size || size < 0 || size > MaxFileBytes ||
          !Regex.IsMatch(digest, "^[0-9a-f]{64}$", RegexOptions.CultureInvariant))
        throw new InvalidDataException("installed_release_file_metadata_invalid");
      using var stream = File.OpenRead(path);
      if (!CryptographicOperations.FixedTimeEquals(SHA256.HashData(stream), Convert.FromHexString(digest)))
        throw new InvalidDataException("installed_release_file_digest_drifted");
    }
    if (!manifestPaths.SetEquals(ExpectedPayload))
      throw new InvalidDataException("installed_release_manifest_closed_set_drifted");
  }

  public void Dispose() => archive.Dispose();
  static bool ValidArchivePath(string path) => !string.IsNullOrEmpty(path) && !path.StartsWith('/') && !path.Contains('\\') && !path.Contains(':') && !path.Split('/').Any(part => part is "" or "." or "..");
  static bool ExactKeys(JsonElement value, HashSet<string> expected)
  {
    var actual = new HashSet<string>(StringComparer.Ordinal);
    foreach (var property in value.EnumerateObject())
      if (!actual.Add(property.Name)) return false;
    return actual.SetEquals(expected);
  }
  static bool ExactString(JsonElement root, string name, string expected) =>
      root.GetProperty(name).ValueKind == JsonValueKind.String && root.GetProperty(name).GetString() == expected;
  static bool ExactBoolean(JsonElement root, string name, bool expected) =>
      root.GetProperty(name).ValueKind == (expected ? JsonValueKind.True : JsonValueKind.False);
  static bool ExactInt(JsonElement root, string name, int expected) =>
      root.GetProperty(name).ValueKind == JsonValueKind.Number &&
      root.GetProperty(name).TryGetInt32(out var actual) && actual == expected;
}

static class ConfigInspector
{
  static readonly IReadOnlyDictionary<string, string> ImageRepositories =
      new Dictionary<string, string>(StringComparer.Ordinal)
      {
        ["OMNIBASE_BACKEND_IMAGE"] = "ghcr.io/lss100200/omnibase-backend",
        ["OMNIBASE_FRONTEND_IMAGE"] = "ghcr.io/lss100200/omnibase-frontend",
        ["OMNIBASE_POSTGRES_IMAGE"] = "pgvector/pgvector",
        ["OMNIBASE_REDIS_IMAGE"] = "redis",
        ["OMNIBASE_MINIO_IMAGE"] = "minio/minio",
        ["OMNIBASE_MINIO_MC_IMAGE"] = "minio/mc",
      };
  static readonly string[] GateKeys = { "AGENT_RUNTIME_ENABLED", "AGENT_PLANNER_ENABLED", "MULTI_AGENT_ENABLED", "MCP_RUNTIME_ENABLED" };
  static readonly Regex EnvironmentName = new("^[A-Z][A-Z0-9_]*$", RegexOptions.CultureInvariant);
  static readonly HashSet<string> RequiredKeys = new(
      ImageRepositories.Keys.Concat(new[]
      {
            "OMNIBASE_FRONTEND_PORT", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
            "DATABASE_URL", "REDIS_PASSWORD", "REDIS_URL", "MINIO_ROOT_USER",
            "MINIO_ROOT_PASSWORD", "MINIO_BUCKET", "JWT_SECRET",
            "PROVIDER_CREDENTIAL_ENCRYPTION_KEY", "MEMORY_CONTENT_ENCRYPTION_KEY",
            "PROVIDER_ENDPOINT_ALLOWLIST", "CORS_ORIGINS", "OMNIBASE_DEPLOYMENT_INSTANCE_ID",
      }).Concat(GateKeys),
      StringComparer.Ordinal);

  public static ConfigResult Inspect(string path)
  {
    if (!File.Exists(path)) return new(false, false, false, "CONFIG_FILE_MISSING");
    if (File.GetAttributes(path).HasFlag(FileAttributes.ReparsePoint)) return new(false, false, false, "CONFIG_REPARSE_FORBIDDEN");
    Dictionary<string, string> values = new(StringComparer.Ordinal);
    try
    {
      foreach (var raw in File.ReadAllLines(path, new UTF8Encoding(false, true)))
      {
        var line = raw.Trim();
        if (line.Length == 0 || line.StartsWith('#')) continue;
        var split = line.IndexOf('=');
        if (split <= 0) return new(false, false, false, "CONFIG_ASSIGNMENT_INVALID");
        var key = line[..split];
        var value = line[(split + 1)..];
        if (!EnvironmentName.IsMatch(key) || value.Length == 0)
          return new(false, false, false, "CONFIG_ASSIGNMENT_INVALID");
        if (!values.TryAdd(key, value))
          return new(false, false, false, "CONFIG_DUPLICATE_KEY");
      }
    }
    catch (DecoderFallbackException) { return new(false, false, false, "CONFIG_UTF8_INVALID"); }
    if (!values.Keys.ToHashSet(StringComparer.Ordinal).SetEquals(RequiredKeys))
      return new(false, false, false, "CONFIG_KEY_SET_INVALID");
    if (!int.TryParse(values["OMNIBASE_FRONTEND_PORT"], out var port) || port is < 1024 or > 65535)
      return new(false, false, false, "CONFIG_FRONTEND_PORT_INVALID");
    if (values["POSTGRES_USER"] != "omnibase" || values["POSTGRES_DB"] != "omnibase" ||
        values["MINIO_ROOT_USER"] != "omnibase" || values["MINIO_BUCKET"] != "omnibase-files")
      return new(false, false, false, "CONFIG_FIXED_IDENTITY_INVALID");
    var postgres = values["POSTGRES_PASSWORD"];
    var redis = values["REDIS_PASSWORD"];
    var minio = values["MINIO_ROOT_PASSWORD"];
    if (!StrongSecret(postgres, 20) || !StrongSecret(redis, 20) ||
        !StrongSecret(minio, 20) || !StrongSecret(values["JWT_SECRET"], 32))
      return new(false, false, false, "CONFIG_SECRET_STRENGTH_INVALID");
    if (values["DATABASE_URL"] !=
            $"postgresql+psycopg://omnibase:{Uri.EscapeDataString(postgres)}@postgres:5432/omnibase" ||
        values["REDIS_URL"] != $"redis://:{Uri.EscapeDataString(redis)}@redis:6379/0")
      return new(false, false, false, "CONFIG_CREDENTIAL_URL_MISMATCH");
    if (!Base64Url32(values["PROVIDER_CREDENTIAL_ENCRYPTION_KEY"]) ||
        !Base64Url32(values["MEMORY_CONTENT_ENCRYPTION_KEY"]) ||
        values["PROVIDER_CREDENTIAL_ENCRYPTION_KEY"] == values["MEMORY_CONTENT_ENCRYPTION_KEY"])
      return new(false, false, false, "CONFIG_ENCRYPTION_KEY_INVALID");
    if (!Guid.TryParseExact(values["OMNIBASE_DEPLOYMENT_INSTANCE_ID"], "D", out _))
      return new(false, false, false, "CONFIG_DEPLOYMENT_ID_INVALID");
    if (values["CORS_ORIGINS"] != $"[\"http://127.0.0.1:{port}\"]")
      return new(false, false, false, "CONFIG_CORS_INVALID");
    var imagesPublished = ImageRepositories.All(item =>
        values.TryGetValue(item.Key, out var value) &&
        value.StartsWith(item.Value + "@sha256:", StringComparison.Ordinal) &&
        Regex.IsMatch(value, "^[a-z0-9./_-]+@sha256:[0-9a-f]{64}$", RegexOptions.CultureInvariant));
    var gatesClosed = GateKeys.All(key =>
        values.TryGetValue(key, out var value) && value.Equals("false", StringComparison.Ordinal));
    return new(true, imagesPublished, gatesClosed, "PASS");
  }

  static bool StrongSecret(string value, int minimumLength) =>
      value.Length >= minimumLength && !value.Any(char.IsWhiteSpace) &&
      !value.Contains("REPLACE_WITH", StringComparison.Ordinal);

  static bool Base64Url32(string value)
  {
    if (!Regex.IsMatch(value, "^[A-Za-z0-9_-]{43}$", RegexOptions.CultureInvariant)) return false;
    var base64 = value.Replace('-', '+').Replace('_', '/') + "=";
    try { return Convert.FromBase64String(base64).Length == 32; }
    catch (FormatException) { return false; }
  }

  public readonly record struct ConfigResult(bool Valid, bool ImagesPublished, bool FeatureGatesClosed, string Code);
}
