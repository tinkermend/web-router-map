#!/usr/bin/env node
import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const BASE_URL = "http://localhost:5173/";
const CREDENTIALS = {
  username: "admin",
  password: "admin123",
};
const SELECTORS = {
  username: [
    'input[name="accountNo"]',
    'input[name="username"]',
    'input[name="email"]',
    'input[type="email"]',
    'input[autocomplete="username"]',
    "#username",
    "#email",
    'input[placeholder*="用户名"]',
    'input[placeholder*="账号"]',
    'input[placeholder*="email"]',
  ],
  password: [
    'input[name="password"]',
    'input[type="password"]',
    'input[autocomplete="current-password"]',
    "#password",
    'input[placeholder*="密码"]',
    'input[placeholder*="Password"]',
  ],
  submit: [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("登录")',
    'button:has-text("立即登录")',
    'button:has-text("Log in")',
    'button:has-text("Sign in")',
  ],
  postLogin: [
    '[data-testid="user-menu"]',
    '[data-testid="logout"]',
    'button:has-text("退出")',
    'a:has-text("退出")',
    'text=Dashboard',
  ],
};
const TIMEOUT_MS = 15_000;
const POST_LOGIN_SIGNAL_TIMEOUT_MS = 5_000;
const COOKIE_OUTPUT_PATH =
  "/Users/wangpei/src/singe/web-router-map/output/playwright/login-cookies.json";
const HEADLESS = true;
const PER_SELECTOR_TIMEOUT_MS = 1_200;
const SESSION_ACCESS_TOKEN_KEY = "skillsflow:access_token";
const LOGIN_API_KEYWORDS = ["/api/auth/login", "/auth/login"];

function logInfo(message) {
  console.log(`[INFO] ${message}`);
}

function logError(message) {
  console.error(`[ERROR] ${message}`);
}

async function checkBaseUrlReachable(baseUrl) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(baseUrl, {
      method: "GET",
      redirect: "follow",
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new Error(`状态码=${res.status}`);
    }
  } catch (error) {
    throw new Error(
      `BASE_URL 不可访问 (${baseUrl})，请先启动 localhost:5173。原始错误: ${error.message}`,
    );
  } finally {
    clearTimeout(timeout);
  }
}

async function openLoginPage(page) {
  logInfo(`打开登录页: ${BASE_URL}`);
  await page.goto(BASE_URL, {
    waitUntil: "domcontentloaded",
    timeout: TIMEOUT_MS,
  });
}

async function findFirstUsableSelector(page, candidates, fieldName) {
  for (const selector of candidates) {
    const locator = page.locator(selector).first();
    try {
      await locator.waitFor({
        state: "visible",
        timeout: PER_SELECTOR_TIMEOUT_MS,
      });
      const enabled = await locator.isEnabled();
      if (enabled) {
        return selector;
      }
    } catch {
      // Ignore and continue trying next selector.
    }
  }
  throw new Error(`未找到${fieldName}候选选择器，请检查 SELECTORS.${fieldName}`);
}

async function resolveLoginSelectors(page) {
  logInfo("解析登录元素选择器");
  const usernameSelector = await findFirstUsableSelector(
    page,
    SELECTORS.username,
    "username",
  );
  const passwordSelector = await findFirstUsableSelector(
    page,
    SELECTORS.password,
    "password",
  );
  const submitSelector = await findFirstUsableSelector(
    page,
    SELECTORS.submit,
    "submit",
  );
  return { usernameSelector, passwordSelector, submitSelector };
}

async function waitPostLoginSignals(page, initialUrl, timeoutMs = TIMEOUT_MS) {
  const urlChanged = page
    .waitForURL((url) => url.toString() !== initialUrl, { timeout: timeoutMs })
    .then(() => "url-changed");
  const networkIdle = page
    .waitForLoadState("networkidle", { timeout: timeoutMs })
    .then(() => "network-idle");
  const postLoginMarker = Promise.any(
    SELECTORS.postLogin.map((selector) =>
      page
        .locator(selector)
        .first()
        .waitFor({ state: "visible", timeout: timeoutMs })
        .then(() => `post-login:${selector}`),
    ),
  );

  try {
    return await Promise.any([urlChanged, networkIdle, postLoginMarker]);
  } catch {
    throw new Error("登录提交后未检测到完成信号（URL变化/页面变化/网络空闲）");
  }
}

async function waitForLoginApiSuccess(page) {
  logInfo("等待登录接口成功响应");
  const response = await page.waitForResponse(
    (res) =>
      res.request().method() === "POST" &&
      LOGIN_API_KEYWORDS.some((keyword) => res.url().includes(keyword)),
    { timeout: TIMEOUT_MS },
  );

  if (!response.ok()) {
    throw new Error(
      `登录接口返回非成功状态: status=${response.status()} url=${response.url()}`,
    );
  }

  let accessToken = null;
  try {
    const payload = await response.json();
    if (typeof payload?.accessToken === "string" && payload.accessToken) {
      accessToken = payload.accessToken;
    }
  } catch {
    // Ignore response body parsing failures.
  }

  return {
    accessToken,
    status: response.status(),
    url: response.url(),
  };
}

async function performLogin(page, selectors, credentials) {
  logInfo("填充账号密码并提交");
  const initialUrl = page.url();
  const loginApiPromise = waitForLoginApiSuccess(page);
  await page.locator(selectors.usernameSelector).first().fill(credentials.username);
  await page.locator(selectors.passwordSelector).first().fill(credentials.password);
  await page.locator(selectors.submitSelector).first().click({ timeout: TIMEOUT_MS });
  const loginApiResult = await loginApiPromise;
  logInfo(`登录接口成功: ${loginApiResult.url} status=${loginApiResult.status}`);

  let signal = "login-api-success";
  try {
    const postSignal = await waitPostLoginSignals(
      page,
      initialUrl,
      POST_LOGIN_SIGNAL_TIMEOUT_MS,
    );
    signal = `${signal}+${postSignal}`;
  } catch {
    logInfo("未检测到额外页面变化信号，继续执行抓取");
  }

  logInfo(`检测到登录完成信号: ${signal}`);
  return loginApiResult;
}

async function captureCookies(context) {
  logInfo("抓取 Cookie");
  const deadline = Date.now() + TIMEOUT_MS;
  let cookies = [];
  let authorizationCookie = null;
  let refreshTokenCookie = null;

  while (Date.now() < deadline) {
    cookies = await context.cookies();
    authorizationCookie = cookies.find((cookie) => cookie.name === "authorization");
    refreshTokenCookie = cookies.find((cookie) => cookie.name === "refreshToken");
    if (authorizationCookie || refreshTokenCookie || cookies.length > 0) {
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }

  if (cookies.length === 0) {
    cookies = await context.cookies();
  }

  authorizationCookie =
    authorizationCookie || cookies.find((cookie) => cookie.name === "authorization");
  refreshTokenCookie =
    refreshTokenCookie || cookies.find((cookie) => cookie.name === "refreshToken");

  return { cookies, authorizationCookie, refreshTokenCookie };
}

async function readSessionAccessToken(page) {
  return page.evaluate((key) => {
    try {
      return window.sessionStorage.getItem(key);
    } catch {
      return null;
    }
  }, SESSION_ACCESS_TOKEN_KEY);
}

function resolveAuthorizationValue({
  authorizationCookie,
  sessionAccessToken,
  loginAccessToken,
  refreshTokenCookie,
}) {
  if (authorizationCookie?.value) {
    return {
      value: authorizationCookie.value,
      source: "cookie:authorization",
    };
  }

  if (sessionAccessToken) {
    return {
      value: sessionAccessToken,
      source: `sessionStorage:${SESSION_ACCESS_TOKEN_KEY}`,
    };
  }

  if (loginAccessToken) {
    return {
      value: loginAccessToken,
      source: "response:accessToken",
    };
  }

  if (refreshTokenCookie?.value) {
    return {
      value: refreshTokenCookie.value,
      source: "cookie:refreshToken",
    };
  }

  return {
    value: null,
    source: null,
  };
}

async function persistCookies(cookies) {
  const outputDir = path.dirname(COOKIE_OUTPUT_PATH);
  await mkdir(outputDir, { recursive: true });
  const payload = {
    capturedAt: new Date().toISOString(),
    baseUrl: BASE_URL,
    cookies,
  };
  await writeFile(COOKIE_OUTPUT_PATH, JSON.stringify(payload, null, 2), "utf8");
  logInfo(`Cookie 已写入: ${COOKIE_OUTPUT_PATH}`);
}

function printAuthorization(authorizationResult) {
  if (!authorizationResult.value) {
    throw new Error(
      "登录后未找到可用授权信息（authorization Cookie / sessionStorage access_token / refreshToken Cookie）",
    );
  }

  console.log(`authorization=${authorizationResult.value}`);
  logInfo(`authorization 来源: ${authorizationResult.source}`);
}

async function main() {
  let browser;
  try {
    await checkBaseUrlReachable(BASE_URL);
    browser = await chromium.launch({ headless: HEADLESS });
    const context = await browser.newContext();
    context.setDefaultTimeout(TIMEOUT_MS);
    const page = await context.newPage();
    page.setDefaultTimeout(TIMEOUT_MS);

    await openLoginPage(page);
    const selectors = await resolveLoginSelectors(page);
    const loginApiResult = await performLogin(page, selectors, CREDENTIALS);

    const { cookies, authorizationCookie, refreshTokenCookie } =
      await captureCookies(context);
    const sessionAccessToken = await readSessionAccessToken(page);
    const authorizationResult = resolveAuthorizationValue({
      authorizationCookie,
      sessionAccessToken,
      loginAccessToken: loginApiResult.accessToken,
      refreshTokenCookie,
    });
    await persistCookies(cookies);
    printAuthorization(authorizationResult);
  } catch (error) {
    logError(error.message);
    process.exitCode = 1;
  } finally {
    if (browser) {
      await browser.close();
    }
  }
}

await main();
