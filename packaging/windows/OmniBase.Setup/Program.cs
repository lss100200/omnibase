using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;

const int MaxArchiveEntries = 16;
const long MaxManifestBytes = 64 * 1024;
const long MaxFileBytes = 2 * 1024 * 1024;
const long MaxTotalBytes = 10 * 1024 * 1024;
const long MaxCompressionRatio = 100;
var expectedPayload = new HashSet<string>(StringComparer.Ordinal)
{
    "LICENSE",
    "deployment/release/windows/README.zh-CN.md",
    "deployment/release/windows/compose.yml",
    "deployment/release/windows/operator.env.template",
    "scripts/release/validate_windows_release_config.py",
};

if (args.Length != 3 || args[0] != "--verify-and-extract")
{
    return Fail("usage: OmniBase.Setup --verify-and-extract <release.zip> <target-directory>");
}

var archivePath = Path.GetFullPath(args[1]);
var target = Path.TrimEndingDirectorySeparator(Path.GetFullPath(args[2]));
if (!File.Exists(archivePath) || File.GetAttributes(archivePath).HasFlag(FileAttributes.ReparsePoint) ||
    File.Exists(target) || Directory.Exists(target))
{
    return Fail("invalid_input_or_target_exists");
}

var staging = target + ".staging-" + Guid.NewGuid().ToString("N");
try
{
    using var archive = ZipFile.OpenRead(archivePath);
    if (archive.Entries.Count is < 2 or > MaxArchiveEntries)
    {
        return Fail("release_archive_entry_count_invalid");
    }

    var entries = new Dictionary<string, ZipArchiveEntry>(StringComparer.Ordinal);
    long totalLength = 0;
    foreach (var entry in archive.Entries)
    {
        if (!entries.TryAdd(entry.FullName, entry))
        {
            return Fail("release_archive_duplicate_path");
        }
        if (!ValidArchivePath(entry.FullName) || string.IsNullOrEmpty(entry.Name))
        {
            return Fail("release_archive_path_invalid");
        }
        if (entry.Length < 0 || entry.Length > MaxFileBytes || entry.CompressedLength < 0)
        {
            return Fail("release_archive_file_size_invalid");
        }
        if (entry.Length > 0 &&
            (entry.CompressedLength == 0 || entry.CompressedLength < (entry.Length + MaxCompressionRatio - 1) / MaxCompressionRatio))
        {
            return Fail("release_archive_compression_ratio_invalid");
        }
        checked { totalLength += entry.Length; }
        if (totalLength > MaxTotalBytes)
        {
            return Fail("release_archive_total_size_invalid");
        }
    }

    if (!entries.TryGetValue("release.json", out var manifestEntry) ||
        manifestEntry.Length is <= 0 or > MaxManifestBytes)
    {
        return Fail("release_manifest_missing_or_size_invalid");
    }

    JsonDocument document;
    using (var stream = manifestEntry.Open())
    {
        document = JsonDocument.Parse(stream, new JsonDocumentOptions
        {
            AllowTrailingCommas = false,
            CommentHandling = JsonCommentHandling.Disallow,
            MaxDepth = 8,
        });
    }
    using (document)
    {
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
        {
            return Fail("release_manifest_schema_or_posture_invalid");
        }
        var commit = root.GetProperty("source_commit");
        if (commit.ValueKind != JsonValueKind.String ||
            !Regex.IsMatch(commit.GetString()!, "^[0-9a-f]{40}$", RegexOptions.CultureInvariant))
        {
            return Fail("release_manifest_source_commit_invalid");
        }

        var files = root.GetProperty("files");
        if (files.ValueKind != JsonValueKind.Array || files.GetArrayLength() != expectedPayload.Count)
        {
            return Fail("release_manifest_files_invalid");
        }
        var manifestPaths = new HashSet<string>(StringComparer.Ordinal);
        var expectedFileKeys = new HashSet<string>(StringComparer.Ordinal) { "path", "sha256", "size" };
        foreach (var file in files.EnumerateArray())
        {
            if (file.ValueKind != JsonValueKind.Object || !ExactKeys(file, expectedFileKeys))
            {
                return Fail("release_manifest_file_schema_invalid");
            }
            var pathElement = file.GetProperty("path");
            var digestElement = file.GetProperty("sha256");
            var sizeElement = file.GetProperty("size");
            if (pathElement.ValueKind != JsonValueKind.String ||
                digestElement.ValueKind != JsonValueKind.String ||
                sizeElement.ValueKind != JsonValueKind.Number ||
                !sizeElement.TryGetInt64(out var size) || size < 0 || size > MaxFileBytes)
            {
                return Fail("release_manifest_file_value_invalid");
            }
            var path = pathElement.GetString()!;
            var expectedDigest = digestElement.GetString()!;
            if (!expectedPayload.Contains(path) || !manifestPaths.Add(path) ||
                !Regex.IsMatch(expectedDigest, "^[0-9a-f]{64}$", RegexOptions.CultureInvariant) ||
                !entries.TryGetValue(path, out var entry) || entry.Length != size)
            {
                return Fail("release_file_missing_or_metadata_drifted");
            }
            using var stream = entry.Open();
            var actualDigest = SHA256.HashData(stream);
            if (!CryptographicOperations.FixedTimeEquals(
                    actualDigest, Convert.FromHexString(expectedDigest)))
            {
                return Fail("release_file_digest_drifted");
            }
        }
        if (!manifestPaths.SetEquals(expectedPayload) ||
            !entries.Keys.ToHashSet(StringComparer.Ordinal).SetEquals(expectedPayload.Append("release.json")))
        {
            return Fail("release_archive_closed_set_drifted");
        }
    }

    Directory.CreateDirectory(staging);
    foreach (var entry in entries.Values.OrderBy(entry => entry.FullName, StringComparer.Ordinal))
    {
        var destination = Path.GetFullPath(Path.Combine(staging, entry.FullName));
        if (!destination.StartsWith(staging + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
        {
            return Fail("release_archive_path_escape");
        }
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        entry.ExtractToFile(destination, overwrite: false);
    }
    MoveDirectoryWithRetry(staging, target);
}
catch (Exception exception) when (exception is IOException or InvalidDataException or
                                   JsonException or UnauthorizedAccessException or OverflowException or
                                   CryptographicException or ArgumentException)
{
    return Fail("release_install_failed");
}
finally
{
    if (Directory.Exists(staging))
    {
        try { Directory.Delete(staging, recursive: true); }
        catch { /* Best-effort cleanup; target is never partially installed. */ }
    }
}

Console.WriteLine("verified_and_atomically_installed_preview_release");
Console.WriteLine("Publisher signature and Authenticode remain NOT_PROVEN.");
Console.WriteLine("Docker/WSL VHDX was not modified. Runtime, Planner, Multi-Agent and MCP remain disabled.");
return 0;

static int Fail(string code)
{
    Console.Error.WriteLine(code);
    return 2;
}

static void MoveDirectoryWithRetry(string source, string destination)
{
    const int maxAttempts = 8;
    const int retryDelayMilliseconds = 100;

    for (var attempt = 1; attempt <= maxAttempts; attempt++)
    {
        if (File.Exists(destination) || Directory.Exists(destination))
        {
            throw new IOException("release target appeared before atomic install");
        }

        try
        {
            Directory.Move(source, destination);
            return;
        }
        catch (IOException exception)
        {
            if (attempt == maxAttempts)
            {
                throw new IOException("atomic install retry budget exhausted", exception);
            }
            Thread.Sleep(retryDelayMilliseconds * attempt);
        }
    }

    throw new IOException("atomic install retry loop terminated unexpectedly");
}

static bool ValidArchivePath(string path)
{
    if (string.IsNullOrEmpty(path) || path.StartsWith('/') || path.Contains('\\') || path.Contains(':'))
    {
        return false;
    }
    return !path.Split('/', StringSplitOptions.None).Any(segment => segment is "" or "." or "..");
}

static bool ExactKeys(JsonElement value, HashSet<string> expected)
{
    var actual = new HashSet<string>(StringComparer.Ordinal);
    foreach (var property in value.EnumerateObject())
    {
        if (!actual.Add(property.Name)) return false;
    }
    return actual.SetEquals(expected);
}

static bool ExactString(JsonElement root, string name, string expected) =>
    root.GetProperty(name).ValueKind == JsonValueKind.String &&
    root.GetProperty(name).GetString() == expected;

static bool ExactBoolean(JsonElement root, string name, bool expected) =>
    (expected ? JsonValueKind.True : JsonValueKind.False) == root.GetProperty(name).ValueKind;

static bool ExactInt(JsonElement root, string name, int expected) =>
    root.GetProperty(name).ValueKind == JsonValueKind.Number &&
    root.GetProperty(name).TryGetInt32(out var actual) && actual == expected;
