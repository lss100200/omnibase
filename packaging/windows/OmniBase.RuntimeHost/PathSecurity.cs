using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;

namespace OmniBase.RuntimeHost;

internal sealed record VerifiedArtifact(string FullPath, string ExpectedSha256);

internal sealed record VerifiedArtifacts(
    VerifiedArtifact Backend,
    VerifiedArtifact Frontend,
    VerifiedArtifact Node)
{
  internal static VerifiedArtifacts Load(string applicationRoot, RuntimeHostConfig config)
  {
    var backend = PathSecurity.VerifyArtifact(applicationRoot, config.Backend);
    var frontend = PathSecurity.VerifyArtifact(applicationRoot, config.Frontend);
    var node = PathSecurity.VerifyArtifact(applicationRoot, config.Node);
    var identities = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
    {
      backend.FullPath,
      frontend.FullPath,
      node.FullPath,
    };
    if (identities.Count != 3)
      throw new HostFailureException("runtime_host_artifact_paths_must_differ");
    return new VerifiedArtifacts(backend, frontend, node);
  }
}

internal static class PathSecurity
{
  internal static string ValidateApplicationRoot(string value)
  {
    var fullPath = Path.GetFullPath(value);
    if (!Directory.Exists(fullPath) || IsUnc(fullPath))
      throw new HostFailureException("runtime_host_application_root_invalid");
    EnsureNoReparsePoints(fullPath, includeLeaf: true, "runtime_host_application_root_reparse_forbidden");
    return Path.TrimEndingDirectorySeparator(fullPath);
  }

  internal static string ValidateDataRoot(string value)
  {
    if (value.Length is 0 or > 1024 || value != value.Trim() || !Path.IsPathFullyQualified(value))
      throw new HostFailureException("runtime_host_data_root_invalid");
    var fullPath = Path.GetFullPath(value);
    if (IsUnc(fullPath) || HasAlternateDataStream(fullPath) || IsRoot(fullPath) || !Directory.Exists(fullPath))
      throw new HostFailureException("runtime_host_data_root_invalid");
    EnsureNoReparsePoints(fullPath, includeLeaf: true, "runtime_host_data_root_reparse_forbidden");
    return Path.TrimEndingDirectorySeparator(fullPath);
  }

  internal static VerifiedArtifact VerifyArtifact(string applicationRoot, ArtifactConfig artifact)
  {
    var fullPath = ResolveRelativeFile(applicationRoot, artifact.Path);
    ValidateExistingRegularFile(fullPath, "runtime_host_artifact_invalid");
    EnsureNoReparsePoints(fullPath, includeLeaf: true, "runtime_host_artifact_reparse_forbidden");
    VerifyDigest(fullPath, artifact.Sha256);
    return new VerifiedArtifact(fullPath, artifact.Sha256);
  }

  internal static void ReverifyArtifact(VerifiedArtifact artifact)
  {
    ValidateExistingRegularFile(artifact.FullPath, "runtime_host_artifact_invalid");
    EnsureNoReparsePoints(artifact.FullPath, includeLeaf: true, "runtime_host_artifact_reparse_forbidden");
    VerifyDigest(artifact.FullPath, artifact.ExpectedSha256);
  }

  internal static string ResolveRelativeFile(string applicationRoot, string relativePath)
  {
    if (relativePath.Length is 0 or > 512 || relativePath != relativePath.Trim() ||
        Path.IsPathRooted(relativePath) || relativePath.Contains(':', StringComparison.Ordinal))
      throw new HostFailureException("runtime_host_artifact_path_invalid");

    var components = relativePath.Split(new[] { '/', '\\' }, StringSplitOptions.None);
    if (components.Any(component => component.Length == 0 || component is "." or ".." ||
        component.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0))
      throw new HostFailureException("runtime_host_artifact_path_invalid");

    var root = Path.TrimEndingDirectorySeparator(Path.GetFullPath(applicationRoot));
    var fullPath = Path.GetFullPath(Path.Combine(root, Path.Combine(components)));
    var relative = Path.GetRelativePath(root, fullPath);
    if (relative is "." || Path.IsPathRooted(relative) || relative == ".." ||
        relative.StartsWith($"..{Path.DirectorySeparatorChar}", StringComparison.Ordinal))
      throw new HostFailureException("runtime_host_artifact_path_invalid");
    return fullPath;
  }

  internal static void ValidateExistingRegularFile(string path, string code)
  {
    if (!File.Exists(path))
      throw new HostFailureException(code);
    var attributes = File.GetAttributes(path);
    if ((attributes & (FileAttributes.Directory | FileAttributes.Device | FileAttributes.ReparsePoint)) != 0)
      throw new HostFailureException(code);
  }

  private static void VerifyDigest(string path, string expectedHex)
  {
    byte[] actual;
    using (var stream = new FileStream(
        path,
        FileMode.Open,
        FileAccess.Read,
        FileShare.Read,
        64 * 1024,
        FileOptions.SequentialScan))
    {
      actual = SHA256.HashData(stream);
    }
    var expected = Convert.FromHexString(expectedHex);
    if (!CryptographicOperations.FixedTimeEquals(actual, expected))
      throw new HostFailureException("runtime_host_artifact_digest_mismatch");
  }

  private static void EnsureNoReparsePoints(string fullPath, bool includeLeaf, string code)
  {
    var normalized = Path.GetFullPath(fullPath);
    var root = Path.GetPathRoot(normalized);
    if (string.IsNullOrEmpty(root))
      throw new HostFailureException(code);

    var remainder = normalized[root.Length..];
    var components = remainder.Split(
        new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar },
        StringSplitOptions.RemoveEmptyEntries);
    var limit = includeLeaf ? components.Length : Math.Max(components.Length - 1, 0);
    var current = root;
    for (var index = 0; index < limit; index++)
    {
      current = Path.Combine(current, components[index]);
      var attributes = File.GetAttributes(current);
      if ((attributes & FileAttributes.ReparsePoint) != 0)
        throw new HostFailureException(code);
    }
  }

  private static bool IsRoot(string path)
  {
    var root = Path.GetPathRoot(path);
    return root is not null && string.Equals(
        Path.TrimEndingDirectorySeparator(path),
        Path.TrimEndingDirectorySeparator(root),
        StringComparison.OrdinalIgnoreCase);
  }

  private static bool IsUnc(string path) =>
      new Uri(path, UriKind.Absolute).IsUnc;

  private static bool HasAlternateDataStream(string path)
  {
    var rootLength = Path.GetPathRoot(path)?.Length ?? 0;
    return path.IndexOf(':', rootLength) >= 0;
  }
}
