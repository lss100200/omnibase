using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace OmniBase.RuntimeHost;

internal sealed class WindowsJobObject : IDisposable
{
  private const uint JobObjectLimitKillOnJobClose = 0x00002000;
  private const int ExtendedLimitInformationClass = 9;
  private SafeFileHandle? handle;

  private WindowsJobObject(SafeFileHandle handleValue)
  {
    handle = handleValue;
  }

  internal static WindowsJobObject Create()
  {
    var nativeHandle = CreateJobObjectW(IntPtr.Zero, null);
    if (nativeHandle == IntPtr.Zero || nativeHandle == new IntPtr(-1))
      throw new HostFailureException("runtime_host_job_create_failed", Program.ChildStartFailure);
    var safeHandle = new SafeFileHandle(nativeHandle, ownsHandle: true);
    var information = new JobObjectExtendedLimitInformation
    {
      BasicLimitInformation = new JobObjectBasicLimitInformation
      {
        LimitFlags = JobObjectLimitKillOnJobClose,
      },
    };
    var length = Marshal.SizeOf<JobObjectExtendedLimitInformation>();
    var pointer = Marshal.AllocHGlobal(length);
    try
    {
      Marshal.StructureToPtr(information, pointer, fDeleteOld: false);
      if (!SetInformationJobObject(safeHandle, ExtendedLimitInformationClass, pointer, (uint)length))
      {
        safeHandle.Dispose();
        throw new HostFailureException("runtime_host_job_configure_failed", Program.ChildStartFailure);
      }
    }
    finally
    {
      Marshal.FreeHGlobal(pointer);
    }
    return new WindowsJobObject(safeHandle);
  }

  internal void Assign(Process process)
  {
    var current = handle;
    if (current is null || current.IsClosed || current.IsInvalid ||
        !AssignProcessToJobObject(current, process.Handle))
      throw new HostFailureException("runtime_host_job_assign_failed", Program.ChildStartFailure);
  }

  public void Dispose()
  {
    var current = handle;
    handle = null;
    current?.Dispose();
  }

  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  private static extern IntPtr CreateJobObjectW(IntPtr jobAttributes, string? name);

  [DllImport("kernel32.dll", SetLastError = true)]
  [return: MarshalAs(UnmanagedType.Bool)]
  private static extern bool SetInformationJobObject(
      SafeFileHandle job,
      int informationClass,
      IntPtr information,
      uint informationLength);

  [DllImport("kernel32.dll", SetLastError = true)]
  [return: MarshalAs(UnmanagedType.Bool)]
  private static extern bool AssignProcessToJobObject(SafeFileHandle job, IntPtr process);

  [StructLayout(LayoutKind.Sequential)]
  private struct JobObjectBasicLimitInformation
  {
    internal long PerProcessUserTimeLimit;
    internal long PerJobUserTimeLimit;
    internal uint LimitFlags;
    internal UIntPtr MinimumWorkingSetSize;
    internal UIntPtr MaximumWorkingSetSize;
    internal uint ActiveProcessLimit;
    internal UIntPtr Affinity;
    internal uint PriorityClass;
    internal uint SchedulingClass;
  }

  [StructLayout(LayoutKind.Sequential)]
  private struct IoCounters
  {
    internal ulong ReadOperationCount;
    internal ulong WriteOperationCount;
    internal ulong OtherOperationCount;
    internal ulong ReadTransferCount;
    internal ulong WriteTransferCount;
    internal ulong OtherTransferCount;
  }

  [StructLayout(LayoutKind.Sequential)]
  private struct JobObjectExtendedLimitInformation
  {
    internal JobObjectBasicLimitInformation BasicLimitInformation;
    internal IoCounters IoInfo;
    internal UIntPtr ProcessMemoryLimit;
    internal UIntPtr JobMemoryLimit;
    internal UIntPtr PeakProcessMemoryUsed;
    internal UIntPtr PeakJobMemoryUsed;
  }
}
