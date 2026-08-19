using System;

namespace OmniBase.RuntimeHost;

internal sealed record InstanceEnvironment(string NativeProofKey, string DataRoot)
{
  private const string NativeProofKeyName = "OMNIBASE_DESKTOP_NATIVE_PROOF_KEY";
  private const string DataRootName = "OMNIBASE_DESKTOP_DATA_ROOT";

  internal static InstanceEnvironment Load()
  {
    var nativeProofKey = Environment.GetEnvironmentVariable(NativeProofKeyName);
    var dataRoot = Environment.GetEnvironmentVariable(DataRootName);
    if (!IsValidToken(nativeProofKey))
      throw new HostFailureException("runtime_host_native_proof_key_invalid");
    if (dataRoot is null)
      throw new HostFailureException("runtime_host_data_root_missing");
    return new InstanceEnvironment(nativeProofKey!, PathSecurity.ValidateDataRoot(dataRoot));
  }

  internal static bool IsValidToken(string? token)
  {
    if (token is null || token.Length != 64)
      return false;
    foreach (var value in token)
    {
      if (value is not (>= '0' and <= '9' or >= 'a' and <= 'f'))
        return false;
    }
    return true;
  }
}
