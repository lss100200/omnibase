import {
  createHash,
  createHmac,
  randomBytes,
  timingSafeEqual,
} from "node:crypto";
import { lstat, readFile, realpath } from "node:fs/promises";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const IS_MAIN =
  process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
const SHA256 = /^[a-f0-9]{64}$/u;
const TOKEN = /^[a-f0-9]{64}$/u;
const MAX_CONFIG_BYTES = 16 * 1024;
const MAX_ARTIFACT_BYTES = 256 * 1024 * 1024;
const MAX_RELATIVE_PATH_BYTES = 512;
const CONFIG_KEYS = new Set([
  "schema_version",
  "backend",
  "frontend",
  "node",
  "application_version",
  "backend_port",
  "frontend_port",
  "startup_timeout_seconds",
  "shutdown_timeout_seconds",
]);
const ARTIFACT_KEYS = new Set(["path", "sha256"]);
const EXIT_READY = 0;
const EXIT_CHILD = 32;
const EXIT_START = 31;
const EXIT_SECURITY = 30;

function fail(code, exitCode = EXIT_SECURITY) {
  process.stderr.write(`runtime_host_error=${code}\n`);
  if (IS_MAIN) process.exitCode = exitCode;
  throw new Error(code);
}

function contained(root, relative) {
  if (
    typeof relative !== "string" ||
    relative.length === 0 ||
    relative.length > MAX_RELATIVE_PATH_BYTES ||
    relative.includes("\\") ||
    relative.includes("\0") ||
    relative.includes(":") ||
    path.isAbsolute(relative) ||
    relative
      .split("/")
      .some((part) => part.length === 0 || part === "." || part === "..")
  ) {
    fail("runtime_host_path_invalid");
  }
  const target = path.resolve(root, ...relative.split("/"));
  const relation = path.relative(root, target);
  if (
    relation === "" ||
    relation.startsWith("..") ||
    path.isAbsolute(relation)
  ) {
    fail("runtime_host_path_invalid");
  }
  return target;
}

export async function verifiedFile(
  root,
  descriptor,
  requiresExecutable = false,
) {
  if (
    !descriptor ||
    typeof descriptor.path !== "string" ||
    !SHA256.test(descriptor.sha256)
  ) {
    fail("runtime_host_artifact_invalid");
  }
  const target = contained(root, descriptor.path);
  let stat;
  let resolved;
  try {
    stat = await lstat(target);
    resolved = await realpath(target);
  } catch {
    fail("runtime_host_artifact_unavailable");
  }
  if (
    !stat.isFile() ||
    stat.isSymbolicLink() ||
    stat.nlink !== 1 ||
    stat.size <= 0 ||
    stat.size > MAX_ARTIFACT_BYTES ||
    (requiresExecutable &&
      process.platform === "linux" &&
      (stat.mode & 0o111) === 0) ||
    resolved !== target
  ) {
    fail("runtime_host_artifact_identity_invalid");
  }
  const digest = createHash("sha256")
    .update(await readFile(target))
    .digest("hex");
  if (digest !== descriptor.sha256)
    fail("runtime_host_artifact_digest_mismatch");
  return target;
}

export function requireDistinctArtifacts(paths) {
  if (!Array.isArray(paths) || new Set(paths).size !== paths.length) {
    throw new Error("runtime_host_artifact_paths_must_differ");
  }
  return paths;
}

export function buildChildEnvironments(input) {
  if (
    !input ||
    !TOKEN.test(input.instanceToken ?? "") ||
    !TOKEN.test(input.proofKey ?? "") ||
    !TOKEN.test(input.controlToken ?? "") ||
    !path.isAbsolute(input.dataRoot ?? "") ||
    !Number.isInteger(input.backendPort) ||
    !Number.isInteger(input.frontendPort)
  ) {
    throw new Error("runtime_host_environment_invalid");
  }
  const common = {
    OMNIBASE_DESKTOP_MODE: "1",
    OMNIBASE_DESKTOP_DATA_ROOT: input.dataRoot,
  };
  return Object.freeze({
    backend: Object.freeze({
      ...common,
      OMNIBASE_BIND_HOST: "127.0.0.1",
      OMNIBASE_DESKTOP_INSTANCE_TOKEN: input.instanceToken,
      OMNIBASE_DESKTOP_NATIVE_PROOF_KEY: input.proofKey,
      OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN: input.controlToken,
    }),
    frontend: Object.freeze({
      ...common,
      OMNIBASE_DESKTOP_INSTANCE_TOKEN: input.instanceToken,
      NODE_ENV: "production",
      HOSTNAME: "127.0.0.1",
      PORT: String(input.frontendPort),
      API_PROXY_URL: `http://127.0.0.1:${input.backendPort}`,
    }),
  });
}

export async function loadConfig(root) {
  const configPath = path.join(root, "runtime-host.json");
  const raw = await readFile(configPath);
  if (raw.byteLength === 0 || raw.byteLength > MAX_CONFIG_BYTES) {
    fail("runtime_host_config_size_invalid");
  }
  let value;
  try {
    value = JSON.parse(raw.toString("utf8"));
  } catch {
    fail("runtime_host_config_json_invalid");
  }
  if (
    !value ||
    typeof value !== "object" ||
    Object.keys(value).some((key) => !CONFIG_KEYS.has(key)) ||
    CONFIG_KEYS.size !== Object.keys(value).length ||
    value.schema_version !== 1 ||
    typeof value.application_version !== "string" ||
    !/^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$/u.test(value.application_version) ||
    !Number.isInteger(value.backend_port) ||
    !Number.isInteger(value.frontend_port) ||
    value.backend_port < 1024 ||
    value.backend_port > 65535 ||
    value.frontend_port < 1024 ||
    value.frontend_port > 65535 ||
    value.backend_port === value.frontend_port ||
    !Number.isInteger(value.startup_timeout_seconds) ||
    value.startup_timeout_seconds < 1 ||
    value.startup_timeout_seconds > 120 ||
    !Number.isInteger(value.shutdown_timeout_seconds) ||
    value.shutdown_timeout_seconds < 1 ||
    value.shutdown_timeout_seconds > 30
  ) {
    fail("runtime_host_config_invalid");
  }
  for (const name of ["backend", "frontend", "node"]) {
    const descriptor = value[name];
    if (
      !descriptor ||
      typeof descriptor !== "object" ||
      Array.isArray(descriptor) ||
      Object.keys(descriptor).some((key) => !ARTIFACT_KEYS.has(key)) ||
      Object.keys(descriptor).length !== ARTIFACT_KEYS.size ||
      typeof descriptor.path !== "string" ||
      !SHA256.test(descriptor.sha256)
    ) {
      fail("runtime_host_config_invalid");
    }
  }
  return value;
}

function groupSignal(child, signal) {
  if (!child || child.pid === undefined) return;
  try {
    process.kill(-child.pid, signal);
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
}

async function waitForHealth(
  port,
  instanceToken,
  proofKey,
  deadline,
  shouldAbort,
) {
  while (Date.now() < deadline) {
    if (shouldAbort()) return false;
    const challenge = randomBytes(32).toString("hex");
    try {
      const response = await fetch(`http://127.0.0.1:${port}/health`, {
        headers: {
          "x-omnibase-desktop-instance": instanceToken,
          "x-omnibase-desktop-challenge": challenge,
        },
        signal: AbortSignal.timeout(500),
      });
      const proof = response.headers.get("x-omnibase-desktop-proof");
      const expected = createHmac("sha256", Buffer.from(proofKey, "hex"))
        .update(challenge, "ascii")
        .digest("hex");
      if (
        response.ok &&
        proof &&
        SHA256.test(proof) &&
        timingSafeEqual(Buffer.from(proof), Buffer.from(expected))
      ) {
        return true;
      }
    } catch {
      // The backend is expected to refuse connections while it initializes.
    }
    if (shouldAbort()) return false;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return false;
}

async function main() {
  if (process.argv.length !== 2) fail("runtime_host_arguments_forbidden");
  const root = path.resolve(ROOT);
  const config = await loadConfig(root);
  const proofKey = process.env.OMNIBASE_DESKTOP_NATIVE_PROOF_KEY;
  const controlToken = process.env.OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN;
  const dataRoot = process.env.OMNIBASE_DESKTOP_DATA_ROOT;
  if (
    !TOKEN.test(proofKey ?? "") ||
    !TOKEN.test(controlToken ?? "") ||
    !path.isAbsolute(dataRoot ?? "")
  ) {
    fail("runtime_host_environment_invalid");
  }
  const instanceToken = randomBytes(32).toString("hex");
  const backend = await verifiedFile(root, config.backend, true);
  const node = await verifiedFile(root, config.node, true);
  const frontend = await verifiedFile(root, config.frontend);
  try {
    requireDistinctArtifacts([backend, node, frontend]);
  } catch {
    fail("runtime_host_artifact_paths_must_differ");
  }
  const childEnvironments = buildChildEnvironments({
    instanceToken,
    proofKey,
    controlToken,
    dataRoot,
    backendPort: config.backend_port,
    frontendPort: config.frontend_port,
  });
  const children = [];
  const childSettlements = [];
  let ready = false;
  let stopping = false;
  let childExited = false;
  const trackChild = (child) => {
    const settlement = new Promise((resolve) => {
      const settle = () => {
        childExited = true;
        resolve();
      };
      child.once("error", settle);
      child.once("exit", settle);
    });
    children.push(child);
    childSettlements.push(settlement);
  };
  const stop = async (exitCode = EXIT_READY) => {
    if (stopping) return;
    stopping = true;
    for (const child of children) groupSignal(child, "SIGTERM");
    let shutdownTimer;
    try {
      await Promise.race([
        Promise.all(childSettlements),
        new Promise((resolve) => {
          shutdownTimer = setTimeout(
            resolve,
            config.shutdown_timeout_seconds * 1000,
          );
        }),
      ]);
    } finally {
      clearTimeout(shutdownTimer);
    }
    for (const child of children) groupSignal(child, "SIGKILL");
    process.exitCode = exitCode;
  };
  process.once("SIGTERM", () => void stop());
  process.once("SIGINT", () => void stop());
  const backendChild = spawn(
    backend,
    [
      "--host",
      "127.0.0.1",
      "--port",
      String(config.backend_port),
      "--data-root",
      dataRoot,
      "--application-version",
      config.application_version,
    ],
    {
      cwd: root,
      env: childEnvironments.backend,
      stdio: "ignore",
      detached: true,
    },
  );
  trackChild(backendChild);
  const frontendChild = spawn(node, [frontend], {
    cwd: root,
    env: childEnvironments.frontend,
    stdio: "ignore",
    detached: true,
  });
  trackChild(frontendChild);
  const deadline = Date.now() + config.startup_timeout_seconds * 1000;
  if (
    !(await waitForHealth(
      config.backend_port,
      instanceToken,
      proofKey,
      deadline,
      () => childExited,
    )) ||
    childExited
  ) {
    await stop(EXIT_START);
    return;
  }
  ready = true;
  process.stdout.write("runtime_host_ready\n");
  await Promise.race(childSettlements);
  if (ready && !stopping) await stop(EXIT_CHILD);
}

if (IS_MAIN) {
  main().catch(() => {
    if (process.exitCode === undefined || process.exitCode === 0)
      process.exitCode = EXIT_SECURITY;
  });
}
