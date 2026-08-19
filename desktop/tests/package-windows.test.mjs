import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { cp, mkdir, mkdtemp, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { packageWindows } from '../scripts/package-windows.mjs'

const ELECTRON_ZIP_BYTES = 'verified electron archive'
const ELECTRON_ZIP_SHA256 = createHash('sha256').update(ELECTRON_ZIP_BYTES).digest('hex')

async function write(file, value) {
  await mkdir(path.dirname(file), { recursive: true })
  await writeFile(file, value)
}

async function fixture(label) {
  const root = await mkdtemp(path.join(os.tmpdir(), `omnibase-packager-${label}-`))
  const appDir = path.join(root, 'app')
  const electronZipDir = path.join(root, 'electron')
  const runtimeDir = path.join(root, 'runtime')
  const outputDir = path.join(root, 'output')
  await write(path.join(appDir, 'dist', 'main.js'), 'main')
  await write(path.join(appDir, 'package.json'), '{"name":"@omnibase/desktop"}\n')
  await write(path.join(runtimeDir, 'runtime-manifest.json'), '{"schemaVersion":1}\n')
  await write(path.join(runtimeDir, 'backend', 'backend.exe'), 'backend')
  await write(path.join(runtimeDir, 'frontend', 'node_modules', 'client-only', 'index.js'), '')
  await write(
    path.join(electronZipDir, 'electron-v43.4.0-win32-x64.zip'),
    ELECTRON_ZIP_BYTES,
  )
  await mkdir(outputDir)
  const argv = [
    '--app-dir',
    appDir,
    '--electron-zip-dir',
    electronZipDir,
    '--runtime-dir',
    runtimeDir,
    '--output-dir',
    outputDir,
    '--version',
    '1.0.0',
  ]
  return { appDir, argv, electronZipDir, outputDir, root, runtimeDir }
}

async function writePackagedOutput(options, { tamperRuntime = false } = {}) {
  const target = path.join(options.out, 'OmniBase-win32-x64')
  await write(path.join(target, 'OmniBase.exe'), 'electron')
  await write(path.join(target, 'resources', 'app.asar'), 'asar')
  await cp(options.extraResource[0], path.join(target, 'resources', 'runtime'), {
    recursive: true,
    errorOnExist: true,
    force: false,
  })
  if (tamperRuntime) {
    await write(path.join(target, 'resources', 'runtime', 'backend', 'backend.exe'), 'tampered')
  }
  return [target]
}

test('Windows packager copies an exact runtime and excludes build dependencies', async () => {
  const current = await fixture('valid')
  let seenOptions
  const target = await packageWindows(current.argv, {
    expectedElectronZipSha256: ELECTRON_ZIP_SHA256,
    packager: async (options) => {
      seenOptions = options
      return writePackagedOutput(options)
    },
  })

  assert.equal(target, path.join(current.outputDir, 'OmniBase-win32-x64'))
  assert.equal(seenOptions.prune, false)
  assert.equal(seenOptions.derefSymlinks, false)
  assert.equal(seenOptions.asar, true)
  assert.deepEqual(seenOptions.extraResource, [current.runtimeDir])
  assert.equal(seenOptions.electronZipDir, current.electronZipDir)
  assert.equal(seenOptions.ignore.length, 4)
})

test('Windows packager rejects malformed arguments before filesystem access', async () => {
  await assert.rejects(
    () => packageWindows(['--version', '1.0.0']),
    /desktop_packager_arguments_invalid/u,
  )
})

test('Windows packager rejects an unverified local Electron archive', async () => {
  const current = await fixture('electron-digest')
  let called = false
  await assert.rejects(
    () =>
      packageWindows(current.argv, {
        packager: async () => {
          called = true
          return []
        },
      }),
    /desktop_packager_electron_zip_digest_mismatch/u,
  )
  assert.equal(called, false)
})

test('Windows packager rejects a pre-existing target without overwriting it', async () => {
  const current = await fixture('existing')
  await mkdir(path.join(current.outputDir, 'OmniBase-win32-x64'))
  let called = false
  await assert.rejects(
    () =>
      packageWindows(current.argv, {
        expectedElectronZipSha256: ELECTRON_ZIP_SHA256,
        packager: async () => {
          called = true
          return []
        },
      }),
    /desktop_packager_target_exists/u,
  )
  assert.equal(called, false)
})

test('Windows packager rejects an unpinned local Electron archive', async () => {
  const current = await fixture('electron-digest')
  await write(
    path.join(current.electronZipDir, 'electron-v43.4.0-win32-x64.zip'),
    'tampered archive',
  )
  let called = false
  await assert.rejects(
    () =>
      packageWindows(current.argv, {
        expectedElectronZipSha256: ELECTRON_ZIP_SHA256,
        packager: async () => {
          called = true
          return []
        },
      }),
    /desktop_packager_electron_zip_digest_mismatch/u,
  )
  assert.equal(called, false)
})

test('Windows packager rejects copied runtime drift', async () => {
  const current = await fixture('tamper')
  await assert.rejects(
    () =>
      packageWindows(current.argv, {
        expectedElectronZipSha256: ELECTRON_ZIP_SHA256,
        packager: (options) => writePackagedOutput(options, { tamperRuntime: true }),
      }),
    /desktop_packager_output_runtime_mismatch/u,
  )
})
