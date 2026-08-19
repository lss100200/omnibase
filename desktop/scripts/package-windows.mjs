import { createHash } from 'node:crypto'
import { createReadStream } from 'node:fs'
import { lstat, opendir, realpath } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

import { packager as electronPackager } from '@electron/packager'

const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+$/
const EXPECTED_ARGUMENTS = new Set([
  '--app-dir',
  '--electron-zip-dir',
  '--runtime-dir',
  '--output-dir',
  '--version',
])
const ELECTRON_ZIP_NAME = 'electron-v43.4.0-win32-x64.zip'
const ELECTRON_ZIP_SHA256 = 'ef0709cfa719739acce73de6f9b684304baf38c6454376638a70d34a7cecffe0'
const MAX_TREE_FILES = 4096
const MAX_TREE_BYTES = 8 * 1024 * 1024 * 1024
const MAX_FILE_BYTES = 1024 * 1024 * 1024
const PACKAGE_IGNORES = Object.freeze([
  /^\/node_modules(?:\/|$)/u,
  /^\/pnpm-lock\.yaml$/u,
  /^\/src(?:\/|$)/u,
  /^\/tsconfig(?:\.build)?\.json$/u,
])

function fail(code) {
  throw new Error(code)
}

function parseArguments(argv) {
  if (argv.length !== EXPECTED_ARGUMENTS.size * 2) {
    fail('desktop_packager_arguments_invalid')
  }
  const values = new Map()
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index]
    const value = argv[index + 1]
    if (
      typeof name !== 'string' ||
      typeof value !== 'string' ||
      !EXPECTED_ARGUMENTS.has(name) ||
      values.has(name) ||
      value.length === 0
    ) {
      fail('desktop_packager_arguments_invalid')
    }
    values.set(name, value)
  }
  return Object.freeze({
    appDir: values.get('--app-dir'),
    electronZipDir: values.get('--electron-zip-dir'),
    runtimeDir: values.get('--runtime-dir'),
    outputDir: values.get('--output-dir'),
    version: values.get('--version'),
  })
}

function samePath(left, right) {
  return path.normalize(left).toLowerCase() === path.normalize(right).toLowerCase()
}

async function requireOrdinaryDirectory(directory, code) {
  if (!path.isAbsolute(directory)) fail(code)
  let metadata
  let resolved
  try {
    metadata = await lstat(directory)
    resolved = await realpath(directory)
  } catch {
    fail(code)
  }
  if (
    !metadata.isDirectory() ||
    metadata.isSymbolicLink() ||
    !samePath(directory, resolved) ||
    samePath(directory, path.parse(directory).root)
  ) {
    fail(code)
  }
  return path.resolve(directory)
}

async function requireOrdinaryFile(file, code) {
  let metadata
  let resolved
  try {
    metadata = await lstat(file)
    resolved = await realpath(file)
  } catch {
    fail(code)
  }
  if (
    !metadata.isFile() ||
    metadata.isSymbolicLink() ||
    metadata.nlink !== 1 ||
    metadata.size <= 0 ||
    metadata.size > MAX_FILE_BYTES ||
    !samePath(file, resolved)
  ) {
    fail(code)
  }
}

async function digestFile(file) {
  const digest = createHash('sha256')
  const stream = createReadStream(file)
  for await (const chunk of stream) {
    digest.update(chunk)
  }
  return digest.digest('hex')
}

async function snapshotTree(root, code) {
  const files = []
  let totalBytes = 0

  async function walk(directory) {
    let handle
    try {
      handle = await opendir(directory)
    } catch {
      fail(code)
    }
    const entries = []
    for await (const entry of handle) {
      entries.push(entry)
    }
    entries.sort((left, right) => {
      const folded = left.name.toLowerCase().localeCompare(right.name.toLowerCase())
      return folded || left.name.localeCompare(right.name)
    })
    for (const entry of entries) {
      const target = path.join(directory, entry.name)
      let metadata
      try {
        metadata = await lstat(target)
      } catch {
        fail(code)
      }
      if (metadata.isDirectory() && !metadata.isSymbolicLink()) {
        await walk(target)
        continue
      }
      if (
        !metadata.isFile() ||
        metadata.isSymbolicLink() ||
        metadata.nlink !== 1 ||
        metadata.size < 0 ||
        metadata.size > MAX_FILE_BYTES
      ) {
        fail(code)
      }
      totalBytes += metadata.size
      if (files.length >= MAX_TREE_FILES || totalBytes > MAX_TREE_BYTES) {
        fail(code)
      }
      files.push({
        path: path.relative(root, target).split(path.sep).join('/'),
        size: metadata.size,
        sha256: await digestFile(target),
      })
    }
  }

  await walk(root)
  if (files.length === 0) fail(code)
  return JSON.stringify(files)
}

export async function packageWindows(argv, dependencies = {}) {
  if (process.platform !== 'win32') fail('desktop_packager_requires_windows')
  const options = parseArguments(argv)
  if (!VERSION.test(options.version)) fail('desktop_packager_version_invalid')
  const appDir = await requireOrdinaryDirectory(
    options.appDir,
    'desktop_packager_app_dir_invalid',
  )
  const runtimeDir = await requireOrdinaryDirectory(
    options.runtimeDir,
    'desktop_packager_runtime_dir_invalid',
  )
  const electronZipDir = await requireOrdinaryDirectory(
    options.electronZipDir,
    'desktop_packager_electron_zip_dir_invalid',
  )
  const outputDir = await requireOrdinaryDirectory(
    options.outputDir,
    'desktop_packager_output_dir_invalid',
  )
  if (path.basename(runtimeDir).toLowerCase() !== 'runtime') {
    fail('desktop_packager_runtime_dir_name_invalid')
  }
  await requireOrdinaryFile(
    path.join(appDir, 'dist', 'main.js'),
    'desktop_packager_main_entry_invalid',
  )
  await requireOrdinaryFile(
    path.join(runtimeDir, 'runtime-manifest.json'),
    'desktop_packager_runtime_manifest_invalid',
  )
  const electronZip = path.join(electronZipDir, ELECTRON_ZIP_NAME)
  await requireOrdinaryFile(electronZip, 'desktop_packager_electron_zip_invalid')
  const expectedElectronZipSha256 =
    dependencies.expectedElectronZipSha256 ?? ELECTRON_ZIP_SHA256
  if ((await digestFile(electronZip)) !== expectedElectronZipSha256) {
    fail('desktop_packager_electron_zip_digest_mismatch')
  }
  const runtimeSnapshot = await snapshotTree(
    runtimeDir,
    'desktop_packager_runtime_tree_invalid',
  )

  const target = path.join(outputDir, 'OmniBase-win32-x64')
  try {
    await lstat(target)
    fail('desktop_packager_target_exists')
  } catch (error) {
    if (error?.message === 'desktop_packager_target_exists') throw error
    if (error?.code !== 'ENOENT') fail('desktop_packager_target_identity_invalid')
  }

  const packager = dependencies.packager ?? electronPackager
  const outputPaths = await packager({
    dir: appDir,
    out: outputDir,
    name: 'OmniBase',
    platform: 'win32',
    arch: 'x64',
    electronVersion: '43.4.0',
    electronZipDir,
    appVersion: options.version,
    buildVersion: options.version,
    appCopyright: 'Copyright (c) OmniBase Contributors',
    asar: true,
    derefSymlinks: false,
    extraResource: [runtimeDir],
    ignore: PACKAGE_IGNORES,
    overwrite: false,
    prune: false,
    quiet: false,
    win32metadata: {
      CompanyName: 'OmniBase Contributors',
      FileDescription: 'OmniBase personal AI workbench',
      InternalName: 'OmniBase',
      OriginalFilename: 'OmniBase.exe',
      ProductName: 'OmniBase',
      'requested-execution-level': 'asInvoker',
    },
  })
  if (
    outputPaths.length !== 1 ||
    path.normalize(outputPaths[0]) !== path.normalize(target)
  ) {
    fail('desktop_packager_output_invalid')
  }
  await requireOrdinaryDirectory(target, 'desktop_packager_output_invalid')
  await requireOrdinaryFile(
    path.join(target, 'OmniBase.exe'),
    'desktop_packager_output_entry_invalid',
  )
  await requireOrdinaryFile(
    path.join(target, 'resources', 'app.asar'),
    'desktop_packager_output_asar_invalid',
  )
  const packagedRuntime = path.join(target, 'resources', 'runtime')
  await requireOrdinaryDirectory(
    packagedRuntime,
    'desktop_packager_output_runtime_invalid',
  )
  if (
    (await snapshotTree(
      packagedRuntime,
      'desktop_packager_output_runtime_invalid',
    )) !== runtimeSnapshot
  ) {
    fail('desktop_packager_output_runtime_mismatch')
  }
  if ((await digestFile(electronZip)) !== expectedElectronZipSha256) {
    fail('desktop_packager_electron_zip_changed')
  }
  return target
}

async function main() {
  try {
    const target = await packageWindows(process.argv.slice(2))
    process.stdout.write(`${JSON.stringify({ output_dir: target })}\n`)
  } catch (error) {
    const code =
      error instanceof Error && /^desktop_packager_[a-z_]+$/.test(error.message)
        ? error.message
        : 'desktop_packager_failed'
    process.stderr.write(`${JSON.stringify({ error: code })}\n`)
    process.exitCode = 2
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main()
}
