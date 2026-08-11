import { mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { chromium } from "playwright";

const frontendRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const projectRoot = resolve(frontendRoot, "..");
const python = process.env.SHOPPING_AGENT_PYTHON ?? join(projectRoot, ".venv", "bin", "python");
const npm = process.env.NPM ?? "npm";
const backendPort = Number(process.env.SHOPPING_AGENT_BACKEND_PORT ?? 8000);
const frontendPort = Number(process.env.SHOPPING_AGENT_FRONTEND_PORT ?? 5173);
const baseUrl = `http://127.0.0.1:${frontendPort}`;
const outputDir = resolve(projectRoot, "output/playwright/issue-29");
const interactionTimeoutMs = Number(process.env.BROWSER_ACCEPTANCE_INTERACTION_TIMEOUT_MS ?? 30_000);
const scenarioTimeoutMs = Number(process.env.BROWSER_ACCEPTANCE_SCENARIO_TIMEOUT_MS ?? 90_000);

const viewports = [
  { name: "desktop-1280", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
  { name: "mobile-320", width: 320, height: 720 },
];
const scenarios = [
  "task-ready",
  "running",
  "awaiting-clarification",
  "partial",
  "no-match",
  "empty",
  "error",
  "cancelled",
  "completed",
  "developer-diagnostic-mixed",
];

function sleep(milliseconds) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));
}

function withTimeout(promise, timeoutMs, label) {
  return new Promise((resolvePromise, rejectPromise) => {
    const timer = setTimeout(() => {
      rejectPromise(new Error(`${label} timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolvePromise(value);
      },
      (error) => {
        clearTimeout(timer);
        rejectPromise(error);
      },
    );
  });
}

function startProcess(command, args, options) {
  const child = spawn(command, args, { ...options, stdio: ["ignore", "pipe", "pipe"] });
  let output = "";
  child.stdout.on("data", (chunk) => {
    output += chunk.toString();
  });
  child.stderr.on("data", (chunk) => {
    output += chunk.toString();
  });
  child.getOutput = () => output;
  return child;
}

async function waitForHttp(url, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = "unknown error";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await sleep(150);
  }
  throw new Error(`Timed out waiting for ${url}: ${lastError}`);
}

async function assertLayout(page, viewport) {
  const layout = await page.evaluate(() => {
    const isVisible = (element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const root = document.documentElement;
    const body = document.body;
    const directChildren = Array.from(document.querySelector("main")?.children ?? [])
      .filter((element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
      })
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return { tag: element.tagName, top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right };
      });
    const overlaps = [];
    for (let leftIndex = 0; leftIndex < directChildren.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < directChildren.length; rightIndex += 1) {
        const left = directChildren[leftIndex];
        const right = directChildren[rightIndex];
        const width = Math.min(left.right, right.right) - Math.max(left.left, right.left);
        const height = Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top);
        if (width > 1 && height > 1) overlaps.push([left.tag, right.tag, width, height]);
      }
    }
    const clipped = Array.from(document.querySelectorAll("main *"))
      .filter((element) => {
        if (!isVisible(element)) return false;
        const rect = element.getBoundingClientRect();
        if (rect.width <= 1 || rect.height <= 1) return false;
        if (["INPUT", "TEXTAREA", "SELECT", "TABLE"].includes(element.tagName)) return false;
        const style = getComputedStyle(element);
        if (style.clip !== "auto" || style.clipPath !== "none") return false;
        return element.scrollWidth > element.clientWidth + 1 && !["auto", "scroll"].includes(style.overflowX);
      })
      .slice(0, 12)
      .map((element) => element.tagName);
    return {
      rootOverflow: root.scrollWidth - root.clientWidth,
      bodyOverflow: body.scrollWidth - body.clientWidth,
      overlaps,
      clipped,
      liveRegions: document.querySelectorAll("[aria-live]").length,
    };
  });
  if (layout.rootOverflow > 1 || layout.bodyOverflow > 1) {
    throw new Error(`${viewport.name}: horizontal overflow ${JSON.stringify(layout)}`);
  }
  if (layout.overlaps.length > 0) {
    throw new Error(`${viewport.name}: direct workspace overlap ${JSON.stringify(layout.overlaps)}`);
  }
  if (layout.clipped.length > 0) {
    throw new Error(`${viewport.name}: clipped visible content ${JSON.stringify(layout.clipped)}`);
  }
  if (layout.liveRegions === 0) throw new Error(`${viewport.name}: no live region found`);
}

async function assertAccessibility(page, viewport) {
  const violations = await page.evaluate(() => {
    const isVisible = (element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const unlabeledControls = Array.from(document.querySelectorAll("input, textarea, select"))
      .filter((element) => isVisible(element) && element.type !== "hidden")
      .filter((element) => {
        const labelled = element.getAttribute("aria-label") || element.getAttribute("aria-labelledby");
        const id = element.getAttribute("id");
        return !labelled && !(id && document.querySelector(`label[for="${CSS.escape(id)}"]`));
      })
      .map((element) => element.tagName);
    const iconButtonsWithoutTooltips = Array.from(document.querySelectorAll("button"))
      .filter((element) => isVisible(element))
      .filter((element) => !(element.textContent ?? "").trim())
      .filter((element) => !(element.getAttribute("aria-label") && element.getAttribute("title")))
      .map((element) => element.getAttribute("aria-label") ?? "unnamed");
    return { unlabeledControls, iconButtonsWithoutTooltips };
  });
  if (violations.unlabeledControls.length > 0) {
    throw new Error(`${viewport.name}: unlabeled controls ${JSON.stringify(violations.unlabeledControls)}`);
  }
  if (violations.iconButtonsWithoutTooltips.length > 0) {
    throw new Error(`${viewport.name}: icon buttons without tooltip ${JSON.stringify(violations.iconButtonsWithoutTooltips)}`);
  }
}

async function assertSkipLink(page, viewport) {
  await page.keyboard.press("Tab");
  const focused = await page.evaluate(() => document.activeElement?.className ?? "");
  if (!String(focused).includes("skip-link")) throw new Error(`${viewport.name}: first keyboard focus is not the skip link`);
  await page.keyboard.press("Enter");
  const target = await page.evaluate(() => document.activeElement?.id ?? "");
  if (target !== "main-content") throw new Error(`${viewport.name}: skip link did not restore main focus`);
}

async function assertNoReferenceImage(page, viewport) {
  const referenceImageControls = await page.locator('input[type="file"], button[aria-label*="参考图"]').count();
  if (referenceImageControls !== 0) throw new Error(`${viewport.name}: image analysis disabled but reference-image controls are visible`);
}

async function assertReadinessComponents(page, viewport) {
  const environmentSummary = page.getByText("当前使用演示数据", { exact: true });
  await environmentSummary.click();
  const environmentOpen = await environmentSummary.evaluate((element) =>
    element.closest("details")?.hasAttribute("open"),
  );
  if (!environmentOpen) throw new Error(`${viewport.name}: data-source disclosure did not open`);

  const summary = page.getByText("运行组件状态", { exact: true });
  await summary.click();
  await page.getByText("Storage", { exact: true }).waitFor();
  const open = await summary.evaluate((element) => element.parentElement?.hasAttribute("open"));
  if (!open) throw new Error(`${viewport.name}: readiness component disclosure did not open`);
  const componentRows = page.locator('ul[aria-label="运行组件状态"] > li');
  const expectedComponentRows = 9 + (await page.locator('ul[aria-label="平台数据状态"] > li').count());
  const componentRowCount = await componentRows.count();
  if (componentRowCount !== expectedComponentRows) {
    throw new Error(
      `${viewport.name}: readiness exposed ${componentRowCount} component rows, expected ${expectedComponentRows}`,
    );
  }
  const falselyReady = await componentRows.evaluateAll((rows) =>
    rows
      .filter((row) => ["disabled", "unavailable"].includes(row.dataset.state ?? "") && row.dataset.ready === "true")
      .map((row) => row.textContent?.trim() ?? "unknown"),
  );
  if (falselyReady.length > 0) {
    throw new Error(`${viewport.name}: disabled or unavailable component marked ready ${JSON.stringify(falselyReady)}`);
  }
  await assertAccessibility(page, viewport);
  await assertLayout(page, viewport);
  await summary.click();
  const closed = await summary.evaluate((element) => !element.parentElement?.hasAttribute("open"));
  if (!closed) throw new Error(`${viewport.name}: readiness component disclosure did not close`);

  const recallSummary = page.getByText(/候选检索状态：/, { exact: false });
  await recallSummary.click();
  await page.getByText("OpenSearch 类目知识", { exact: true }).waitFor();
  const recallOpen = await recallSummary.evaluate((element) => element.parentElement?.hasAttribute("open"));
  if (!recallOpen) throw new Error(`${viewport.name}: recall disclosure did not open`);
  await assertAccessibility(page, viewport);
  await assertLayout(page, viewport);
  await recallSummary.click();
  const recallClosed = await recallSummary.evaluate((element) => !element.parentElement?.hasAttribute("open"));
  if (!recallClosed) throw new Error(`${viewport.name}: recall disclosure did not close`);

  await environmentSummary.click();
  const environmentClosed = await environmentSummary.evaluate((element) =>
    !element.closest("details")?.hasAttribute("open"),
  );
  if (!environmentClosed) throw new Error(`${viewport.name}: data-source disclosure did not close`);
}

async function scrollScenarioIntoView(page, scenario) {
  const targetSelectors = {
    "task-ready": 'section[aria-labelledby="starter-title"]',
    running: 'section[aria-label="研究进行中"]',
    error: 'section[role="alert"]',
    cancelled: "section:has(h2)",
    empty: 'section[aria-label="空结果"]',
    "no-match": 'section[aria-label="无匹配结果"]',
  };
  const targetSelector = targetSelectors[scenario] ?? 'section[aria-labelledby="result-heading"]';
  const scrollArea = page.locator('main [class*="scrollArea"]');
  await scrollArea.evaluate((area, selector) => {
    const target = area.querySelector(selector);
    if (!target) throw new Error(`state target not found: ${selector}`);
    area.scrollTop = Math.max(0, target.getBoundingClientRect().top - area.getBoundingClientRect().top + area.scrollTop - 8);
    if (["section[aria-label=\"空结果\"]", "section[aria-label=\"无匹配结果\"]"].includes(selector)) {
      const areaRect = area.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      if (targetRect.top < areaRect.top || targetRect.bottom > areaRect.bottom) {
        throw new Error(`state target is obscured by the composer: ${selector}`);
      }
    }
  }, targetSelector);
  await page.waitForTimeout(40);
}

async function assertDeleteTask(page, viewport) {
  const deleteButton = page.getByRole("button", { name: /删除研究：state-completed/ });
  let dialogSeen = false;
  let dialogMessage = "";
  page.once("dialog", (dialog) => {
    dialogSeen = true;
    dialogMessage = dialog.message();
    void dialog.accept();
  });
  await deleteButton.click();
  if (!dialogSeen) throw new Error(`${viewport.name}: task deletion did not present a confirmation dialog`);
  if (!dialogMessage.includes("无法撤销")) throw new Error(`${viewport.name}: delete confirmation text is incomplete`);
  await page.getByText("研究已删除", { exact: true }).waitFor();
  await page.getByRole("textbox", { name: "购物需求" }).waitFor();
  if (!(await page.getByRole("textbox", { name: "购物需求" }).evaluate((element) => element === document.activeElement))) {
    throw new Error(`${viewport.name}: task deletion did not restore focus to the composer`);
  }
}

async function assertClarificationVisible(page, viewport) {
  const bounds = await page.getByRole("textbox", { name: "澄清回答" }).evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return { top: rect.top, bottom: rect.bottom, viewport: window.innerHeight };
  });
  if (bounds.top < 0 || bounds.bottom > bounds.viewport) {
    throw new Error(`${viewport.name}: clarification input is clipped by the viewport ${JSON.stringify(bounds)}`);
  }
}

async function assertCostEvidence(page, viewport) {
  const detailsSummary = page.getByText("价格与证据明细", { exact: true });
  await detailsSummary.click();
  await page.getByText(/1 USD = ¥7\.00.*controlled-fx-feed/).waitFor();
  await page.getByText(/US → 中国大陆.*Controlled Tracked Air.*计费重量 0\.58 kg/).waitFor();
  await page.getByText("controlled-logistics-feed", { exact: true }).waitFor();
  await assertAccessibility(page, viewport);
  await assertLayout(page, viewport);
  await detailsSummary.click();
}

async function runScenario(page, scenario, viewport) {
  let scenarioScreenshotTaken = false;
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.getByRole("textbox", { name: "购物需求" }).waitFor();
  await page.getByText("当前使用演示数据", { exact: true }).waitFor();
  await assertSkipLink(page, viewport);
  await assertNoReferenceImage(page, viewport);
  await assertAccessibility(page, viewport);
  await assertLayout(page, viewport);
  await assertReadinessComponents(page, viewport);

  if (scenario === "task-ready") {
    await scrollScenarioIntoView(page, scenario);
    const startButton = page.getByRole("button", { name: "开始研究" });
    if (await startButton.isDisabled()) throw new Error(`${viewport.name}: task-ready composer is disabled`);
    if ((await page.locator('section[aria-labelledby="result-heading"]').count()) !== 0) {
      throw new Error(`${viewport.name}: task-ready rendered a result before submission`);
    }
    return;
  }

  await page.getByRole("textbox", { name: "购物需求" }).fill(`state-${scenario}`);
  await page.getByRole("button", { name: "开始研究" }).click();

  if (scenario === "running" || scenario === "cancelled") {
    await page.getByRole("region", { name: "研究进行中" }).waitFor();
    if (scenario === "cancelled") {
      await page.getByRole("button", { name: "取消研究" }).click();
      await page.getByRole("heading", { name: "研究已取消" }).waitFor();
    }
  } else if (scenario === "awaiting-clarification") {
    const answer = page.getByRole("textbox", { name: "澄清回答" });
    await answer.waitFor();
    await answer.scrollIntoViewIfNeeded();
    await assertAccessibility(page, viewport);
    await assertLayout(page, viewport);
    await assertClarificationVisible(page, viewport);
    if (!(await answer.evaluate((element) => element === document.activeElement))) {
      throw new Error(`${viewport.name}: clarification input did not receive focus`);
    }
    await page.screenshot({
      path: join(outputDir, `${viewport.name}-${scenario}.png`),
      fullPage: true,
    });
    scenarioScreenshotTaken = true;
    await answer.fill("中国大陆");
    await answer.press("Enter");
    await page.getByRole("heading", { name: "购物建议" }).waitFor();
    if (!(await page.getByRole("textbox", { name: "购物需求" }).evaluate((element) => element === document.activeElement))) {
      throw new Error(`${viewport.name}: clarification focus was not restored to the composer`);
    }
  } else if (scenario === "error") {
    await page.getByRole("heading", { name: "这次研究没有完成" }).waitFor();
  } else {
    const resultSection = page.locator('section[aria-labelledby="result-heading"]');
    await resultSection.waitFor();
    const expectedText = {
      partial: "部分平台结果",
      "no-match": "研究已正常完成",
      empty: "没有可用的商品证据",
      completed: "演示结果",
      "developer-diagnostic-mixed": "部分结果含演示数据",
    }[scenario];
    if (expectedText && !(await resultSection.getByText(expectedText, { exact: false }).count())) {
      throw new Error(`${viewport.name}: ${scenario} did not render ${expectedText}`);
    }
    if (scenario === "empty" && !(await resultSection.getByRole("status", { name: "空结果" }).count())) {
      throw new Error(`${viewport.name}: empty result status is missing`);
    }
    if (scenario === "no-match" && !(await resultSection.getByRole("status", { name: "无匹配结果" }).count())) {
      throw new Error(`${viewport.name}: no-match status is missing`);
    }
    if (scenario === "developer-diagnostic-mixed" && !(await resultSection.getByText("当前结果包含多种数据来源", { exact: false }).count())) {
      throw new Error(`${viewport.name}: mixed diagnostic disclosure is missing`);
    }
    if (scenario === "completed") {
      await resultSection.getByRole("region", { name: "已理解的需求" }).waitFor();
      await resultSection.getByRole("button", { name: "以后也按“简约”推荐" }).waitFor();
      await assertCostEvidence(page, viewport);
    }
  }

  await scrollScenarioIntoView(page, scenario);
  await assertAccessibility(page, viewport);
  await assertLayout(page, viewport);

  if (["completed", "partial", "no-match", "empty", "developer-diagnostic-mixed"].includes(scenario)) {
    const recommendationsTab = page.getByRole("tab", { name: /推荐/ });
    await recommendationsTab.focus();
    await recommendationsTab.press("ArrowRight");
    await page.waitForFunction(() => document.activeElement?.id === "comparison-tab");
    if (!(await page.getByRole("tab", { name: "价格对比" }).evaluate((element) => element === document.activeElement))) {
      throw new Error(`${viewport.name}: result tab keyboard navigation did not move focus`);
    }
    if (scenario === "completed") {
      await page.getByRole("region", { name: "专业比价概览" }).getByText("1/1", { exact: true }).waitFor();
    }
    await scrollScenarioIntoView(page, scenario);
  }
  return scenarioScreenshotTaken;
}

async function main() {
  await mkdir(outputDir, { recursive: true });
  const backend = startProcess(
    python,
    [join(projectRoot, "tests/browser/controlled_backend.py"), "--host", "127.0.0.1", "--port", String(backendPort)],
    { cwd: projectRoot, env: process.env },
  );
  const frontend = startProcess(
    npm,
    ["run", "dev", "--", "--host", "127.0.0.1", "--port", String(frontendPort)],
    {
      cwd: frontendRoot,
      env: {
        ...process.env,
        SHOPPING_AGENT_BACKEND_URL: `http://127.0.0.1:${backendPort}`,
        SHOPPING_AGENT_FRONTEND_PORT: String(frontendPort),
      },
    },
  );
  let browser;
  try {
    await waitForHttp(`http://127.0.0.1:${backendPort}/api/readiness`);
    await waitForHttp(baseUrl);
    browser = await chromium.launch({ headless: true });
    let checks = 0;
    for (const viewport of viewports) {
      for (const scenario of scenarios) {
        const scenarioLabel = `${viewport.name}/${scenario}`;
        const context = await browser.newContext({
          viewport: { width: viewport.width, height: viewport.height },
          reducedMotion: "reduce",
        });
        context.setDefaultTimeout(interactionTimeoutMs);
        context.setDefaultNavigationTimeout(interactionTimeoutMs);
        const page = await context.newPage();
        const consoleErrors = [];
        page.on("console", (message) => {
          if (message.type() === "error") consoleErrors.push(message.text());
        });
        page.on("pageerror", (error) => consoleErrors.push(error.message));
        try {
          console.log(`Browser acceptance starting: ${scenarioLabel}`);
          const scenarioScreenshotTaken = await withTimeout(
            runScenario(page, scenario, viewport),
            scenarioTimeoutMs,
            scenarioLabel,
          );
          if (!scenarioScreenshotTaken) {
            await page.screenshot({
              path: join(outputDir, `${viewport.name}-${scenario}.png`),
              fullPage: true,
            });
          }
          if (viewport.name === "desktop-1280" && scenario === "completed") {
            await assertDeleteTask(page, viewport);
          }
          if (consoleErrors.length > 0) {
            throw new Error(`${viewport.name}/${scenario}: console errors ${JSON.stringify(consoleErrors)}`);
          }
          checks += 1;
          console.log(`Browser acceptance passed: ${scenarioLabel}`);
        } finally {
          await withTimeout(context.close(), 10_000, `${scenarioLabel} context close`);
        }
      }
    }
    console.log(`Browser acceptance passed: ${checks} state/viewport checks (10 states x 3 viewports).`);
  } finally {
    if (browser) await withTimeout(browser.close(), 10_000, "browser close");
    for (const child of [frontend, backend]) {
      if (!child.killed) child.kill("SIGTERM");
    }
    await sleep(150);
    if (frontend.exitCode === null) frontend.kill("SIGKILL");
    if (backend.exitCode === null) backend.kill("SIGKILL");
    if (backend.exitCode && backend.exitCode !== 0) {
      console.error(backend.getOutput());
    }
    if (frontend.exitCode && frontend.exitCode !== 0) {
      console.error(frontend.getOutput());
    }
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack ?? error.message : error);
  process.exitCode = 1;
});
