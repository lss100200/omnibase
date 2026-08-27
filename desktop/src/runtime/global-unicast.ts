import { BlockList, isIP } from "node:net";

/**
 * Local fail-closed replica of CPython ``ipaddress`` special-purpose
 * ranges plus unicast-only constraints. Production team HTTPS asks
 * desktop-local ``POST /desktop/v1/provider-endpoints/pin`` so the
 * connect set is the same ``is_global_unicast`` helper as endpoint.py.
 * This table is the test/fallback path when that pin hook is absent.
 * It is not a guaranteed CPython match. Documented extra-rejects are
 * examples, not an exhaustive IANA disagreement list.
 */
const IPV4_NON_GLOBAL = new BlockList();
const IPV6_NON_GLOBAL = new BlockList();
const IPV4_GLOBAL_EXCEPTIONS = new BlockList();
const IPV6_GLOBAL_UNICAST_SPACE = new BlockList();
const IPV4_LOOPBACK = new BlockList();
const IPV6_LOOPBACK = new BlockList();

for (const [address, prefix] of [
  ["0.0.0.0", 8],
  ["10.0.0.0", 8],
  ["100.64.0.0", 10],
  ["127.0.0.0", 8],
  ["169.254.0.0", 16],
  ["172.16.0.0", 12],
  ["192.0.0.0", 24],
  ["192.0.0.170", 31],
  ["192.0.2.0", 24],
  ["192.168.0.0", 16],
  ["198.18.0.0", 15],
  ["198.51.100.0", 24],
  ["203.0.113.0", 24],
  ["224.0.0.0", 4],
  ["240.0.0.0", 4],
  ["255.255.255.255", 32],
] as const) {
  IPV4_NON_GLOBAL.addSubnet(address, prefix, "ipv4");
}

IPV4_GLOBAL_EXCEPTIONS.addAddress("192.0.0.9", "ipv4");
IPV4_GLOBAL_EXCEPTIONS.addAddress("192.0.0.10", "ipv4");
IPV4_LOOPBACK.addSubnet("127.0.0.0", 8, "ipv4");
IPV6_LOOPBACK.addAddress("::1", "ipv6");
IPV6_GLOBAL_UNICAST_SPACE.addSubnet("2000::", 3, "ipv6");

for (const [address, prefix] of [
  ["::", 128],
  ["::1", 128],
  ["64:ff9b:1::", 48],
  ["100::", 64],
  ["2001::", 23],
  ["2001:db8::", 32],
  ["2002::", 16],
  ["3fff::", 20],
  ["fc00::", 7],
  ["fe80::", 10],
  ["ff00::", 8],
  ["::", 8],
  ["100::", 8],
  ["200::", 7],
  ["400::", 6],
  ["800::", 5],
  ["1000::", 4],
  ["4000::", 3],
  ["6000::", 3],
  ["8000::", 3],
  ["a000::", 3],
  ["c000::", 3],
  ["e000::", 4],
  ["f000::", 5],
  ["f800::", 6],
  ["fe00::", 9],
] as const) {
  IPV6_NON_GLOBAL.addSubnet(address, prefix, "ipv6");
}

export function unwrapIpv4MappedAddress(address: string): string {
  const raw = address.trim().toLowerCase();
  if (raw.startsWith("::ffff:")) {
    const mapped = raw.slice("::ffff:".length);
    if (isIP(mapped) === 4) return mapped;
  }
  return raw;
}

export function isLoopbackConnectAddress(address: string): boolean {
  const raw = unwrapIpv4MappedAddress(address);
  const kind = isIP(raw);
  if (kind === 4) return IPV4_LOOPBACK.check(raw, "ipv4");
  if (kind === 6) return IPV6_LOOPBACK.check(raw, "ipv6");
  return false;
}

export function isGlobalUnicastAddress(address: string): boolean {
  const raw = unwrapIpv4MappedAddress(address);
  const kind = isIP(raw);
  if (kind === 4) {
    if (IPV4_GLOBAL_EXCEPTIONS.check(raw, "ipv4")) return true;
    return !IPV4_NON_GLOBAL.check(raw, "ipv4");
  }
  if (kind === 6) {
    if (!IPV6_GLOBAL_UNICAST_SPACE.check(raw, "ipv6")) return false;
    return !IPV6_NON_GLOBAL.check(raw, "ipv6");
  }
  return false;
}
