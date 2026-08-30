import { expect, test, type Page, type TestInfo } from '@playwright/test'
import axe from 'axe-core'

import { installP7VisualDesktopBridge } from './p7-desktop-visual-bridge'

const SETTINGS_SECTIONS = [
  '外观',
  'Workspace',
  '布局组件',
  '能力',
  'Provider',
  '布局审阅',
  '布局审计',
  'Catalog',
  'Installed',
  'Slots',
  'Skills',
  'MCP',
  'Sandbox',
  'Local Adapters',
  'Permissions',
  'Health',
  '组件审阅',
  '组件审计',
  'Recovery',
] as const

interface AxeViolation {
  readonly id: string
  readonly impact: string | null
  readonly help: string
  readonly nodes: readonly { readonly target: readonly string[] }[]
}

interface LayoutIssue {
  readonly kind: 'clipped-control' | 'horizontal-overflow' | 'overlap' | 'outside-viewport'
  readonly first: string
  readonly second?: string
}

async function openSettings(page: Page, reduceMotion = false) {
  await page.addInitScript(installP7VisualDesktopBridge, { reduceMotion })
  await page.goto('/desktop')
  await expect(page.locator('.p7-root')).toBeVisible()
  await page.locator('.p7-activity button[aria-label="设置"]').click()
  await expect(page.locator('.p7-settings-center')).toBeVisible()
  await expect(page.locator('.p7-settings-nav-item')).toHaveCount(SETTINGS_SECTIONS.length)
}

async function criticalAxeViolations(page: Page): Promise<readonly AxeViolation[]> {
  await page.addScriptTag({ content: axe.source })
  return page.evaluate(async () => {
    const runner = (
      window as typeof window & {
        axe: {
          run: (
            context: Document,
            options: Record<string, unknown>,
          ) => Promise<{ violations: AxeViolation[] }>
        }
      }
    ).axe
    const result = await runner.run(document, {
      resultTypes: ['violations'],
      runOnly: {
        type: 'tag',
        values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'],
      },
    })
    return result.violations.filter(
      (violation) => violation.impact === 'critical' || violation.impact === 'serious',
    )
  })
}

async function visibleLayoutIssues(page: Page): Promise<readonly LayoutIssue[]> {
  return page.evaluate(() => {
    const issues: LayoutIssue[] = []
    const describe = (element: Element) => {
      const html = element as HTMLElement
      const label =
        html.getAttribute('aria-label') ??
        html.getAttribute('title') ??
        html.textContent?.trim().replace(/\s+/g, ' ').slice(0, 80) ??
        html.tagName.toLowerCase()
      return `${html.tagName.toLowerCase()}:${label}`
    }
    const visibleRect = (element: HTMLElement) => {
      const style = getComputedStyle(element)
      if (
        style.display === 'none' ||
        style.visibility === 'hidden' ||
        Number.parseFloat(style.opacity) === 0
      ) {
        return null
      }
      const original = element.getBoundingClientRect()
      let left = Math.max(0, original.left)
      let top = Math.max(0, original.top)
      let right = Math.min(innerWidth, original.right)
      let bottom = Math.min(innerHeight, original.bottom)
      let clippedByScrollableAncestor = false
      for (
        let ancestor = element.parentElement;
        ancestor !== null;
        ancestor = ancestor.parentElement
      ) {
        const ancestorStyle = getComputedStyle(ancestor)
        const clipsX = ['auto', 'clip', 'hidden', 'scroll'].includes(ancestorStyle.overflowX)
        const clipsY = ['auto', 'clip', 'hidden', 'scroll'].includes(ancestorStyle.overflowY)
        if (!clipsX && !clipsY) continue
        const ancestorRect = ancestor.getBoundingClientRect()
        if (clipsX) {
          left = Math.max(left, ancestorRect.left)
          right = Math.min(right, ancestorRect.right)
        }
        if (clipsY) {
          top = Math.max(top, ancestorRect.top)
          bottom = Math.min(bottom, ancestorRect.bottom)
        }
        if (
          ancestor !== document.body &&
          ancestor !== document.documentElement &&
          (['auto', 'scroll'].includes(ancestorStyle.overflowX) ||
            ['auto', 'scroll'].includes(ancestorStyle.overflowY))
        ) {
          clippedByScrollableAncestor = true
        }
      }
      if (right - left <= 1 || bottom - top <= 1) return null
      return {
        clippedByScrollableAncestor,
        original,
        rect: { left, top, right, bottom },
      }
    }
    const controls = Array.from(
      document.querySelectorAll<HTMLElement>(
        'button, input, select, textarea, a[href], [role="button"], [tabindex="0"]',
      ),
    )
      .map((element) => ({ element, visible: visibleRect(element) }))
      .filter(
        (
          entry,
        ): entry is {
          element: HTMLElement
          visible: NonNullable<ReturnType<typeof visibleRect>>
        } => entry.visible !== null,
      )

    if (document.documentElement.scrollWidth > innerWidth + 2) {
      issues.push({
        kind: 'horizontal-overflow',
        first: `${document.documentElement.scrollWidth}>${innerWidth}`,
      })
    }
    for (const { element, visible } of controls) {
      const style = getComputedStyle(element)
      if (
        !visible.clippedByScrollableAncestor &&
        (visible.original.left < -1 || visible.original.right > innerWidth + 1)
      ) {
        issues.push({ kind: 'outside-viewport', first: describe(element) })
      }
      if (
        element.clientWidth > 0 &&
        element.clientHeight > 0 &&
        ((element.scrollWidth > element.clientWidth + 2 &&
          !['auto', 'scroll'].includes(style.overflowX)) ||
          (element.scrollHeight > element.clientHeight + 2 &&
            !['auto', 'scroll'].includes(style.overflowY)))
      ) {
        issues.push({ kind: 'clipped-control', first: describe(element) })
      }
    }
    for (let leftIndex = 0; leftIndex < controls.length; leftIndex += 1) {
      const left = controls[leftIndex]!
      const leftRect = left.visible.rect
      for (let rightIndex = leftIndex + 1; rightIndex < controls.length; rightIndex += 1) {
        const right = controls[rightIndex]!
        if (left.element.contains(right.element) || right.element.contains(left.element)) continue
        const rightRect = right.visible.rect
        const overlapWidth =
          Math.min(leftRect.right, rightRect.right) - Math.max(leftRect.left, rightRect.left)
        const overlapHeight =
          Math.min(leftRect.bottom, rightRect.bottom) - Math.max(leftRect.top, rightRect.top)
        if (overlapWidth > 2 && overlapHeight > 2) {
          issues.push({
            kind: 'overlap',
            first: describe(left.element),
            second: describe(right.element),
          })
        }
      }
    }
    return issues
  })
}

async function attachJson(testInfo: TestInfo, name: string, value: unknown) {
  await testInfo.attach(name, {
    body: Buffer.from(`${JSON.stringify(value, null, 2)}\n`, 'utf8'),
    contentType: 'application/json',
  })
}

test('all Settings views pass serious accessibility and visible layout gates', async ({
  page,
}, testInfo) => {
  await openSettings(page)
  const report: Record<string, unknown> = {
    project: testInfo.project.name,
    viewport: page.viewportSize(),
    sections: [],
  }
  const sectionReports: Record<string, unknown>[] = []
  for (let index = 0; index < SETTINGS_SECTIONS.length; index += 1) {
    const label = SETTINGS_SECTIONS[index]!
    const navigation = page.locator('.p7-settings-nav-item').nth(index)
    await navigation.click()
    await expect(navigation).toHaveAttribute('aria-current', 'page')
    const [axeViolations, layoutIssues] = await Promise.all([
      criticalAxeViolations(page),
      visibleLayoutIssues(page),
    ])
    sectionReports.push({ label, axeViolations, layoutIssues })
    expect(axeViolations, `${label}: critical/serious accessibility violations`).toEqual([])
    expect(layoutIssues, `${label}: clipped, overlapping or overflowing controls`).toEqual([])
  }
  report.sections = sectionReports
  await attachJson(testInfo, 'p7-settings-rendering-report', report)
  await page.screenshot({
    path: testInfo.outputPath(`${testInfo.project.name}-settings.png`),
    fullPage: true,
  })
})

test('Settings keyboard traversal, visible focus, Escape and focus restore are deterministic', async ({
  page,
}) => {
  await openSettings(page)
  const navigation = page.locator('.p7-settings-nav-item')
  await expect(navigation.first()).toBeFocused()
  await page.keyboard.press('End')
  await expect(navigation.last()).toBeFocused()
  await page.keyboard.press('Home')
  await expect(navigation.first()).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(navigation.nth(1)).toBeFocused()
  const focusStyle = await navigation.nth(1).evaluate((element) => {
    const style = getComputedStyle(element)
    return { outlineStyle: style.outlineStyle, outlineWidth: style.outlineWidth }
  })
  expect(focusStyle.outlineStyle).not.toBe('none')
  expect(Number.parseFloat(focusStyle.outlineWidth)).toBeGreaterThanOrEqual(2)
  await page.keyboard.press('Enter')
  await expect(page.locator('.p7-settings-main')).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(page.locator('.p7-settings-center')).toHaveCount(0)
  await expect(page.locator('.p7-activity button[aria-label="设置"]')).toBeFocused()
})

test('reduced motion and forced colors retain reachable controls', async ({ page }, testInfo) => {
  await page.emulateMedia({ colorScheme: 'dark', forcedColors: 'active', reducedMotion: 'reduce' })
  await openSettings(page, true)
  await expect(page.locator('.p7-root')).toHaveClass(/p7-reduce-motion/)
  const layoutIssues = await visibleLayoutIssues(page)
  const axeViolations = await criticalAxeViolations(page)
  await attachJson(testInfo, 'p7-settings-forced-colors-report', {
    project: testInfo.project.name,
    viewport: page.viewportSize(),
    deviceScaleFactor: testInfo.project.use.deviceScaleFactor,
    effectiveZoom: testInfo.project.name === 'p7-zoom-200' ? 200 : 100,
    forcedColors: 'active',
    reducedMotion: 'reduce',
    axeViolations,
    layoutIssues,
  })
  expect(axeViolations).toEqual([])
  expect(layoutIssues).toEqual([])
  await page.screenshot({
    path: testInfo.outputPath(`${testInfo.project.name}-forced-colors.png`),
    fullPage: true,
  })
})
