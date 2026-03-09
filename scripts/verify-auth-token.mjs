#!/usr/bin/env node
/**
 * 验证 Auth Token 有效性
 * 通过调用受保护的 API 端点来测试 token 是否有效
 */

const BASE_URL = "http://localhost:5173";
const API_BASE = `${BASE_URL}/api`;

// 从 exec_test.md 获取的 token
const AUTH_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiIzYTAwZjFhOC0xMmQyLTRmNzctODNiMy00MjRhMDRlMTlmMDEiLCJhY2NvdW50Tm8iOiJhZG1pbiIsImVtYWlsIjoiYWRtaW5AYWlvcHMuY29tIiwidHlwZSI6InJlZnJlc2giLCJpYXQiOjE3NzI2ODkwNDksImV4cCI6MTc3MzI5Mzg0OX0.ED9ME6XH-WzYCCAlQlUQfS0GGMjQUHo7hqhTnjzGxIA";

// 颜色输出
const colors = {
  reset: "\x1b[0m",
  green: "\x1b[32m",
  red: "\x1b[31m",
  yellow: "\x1b[33m",
  blue: "\x1b[34m",
  cyan: "\x1b[36m",
};

function logInfo(message) {
  console.log(`${colors.blue}[INFO]${colors.reset} ${message}`);
}

function logSuccess(message) {
  console.log(`${colors.green}[SUCCESS]${colors.reset} ${message}`);
}

function logError(message) {
  console.log(`${colors.red}[ERROR]${colors.reset} ${message}`);
}

function logWarn(message) {
  console.log(`${colors.yellow}[WARN]${colors.reset} ${message}`);
}

// 解析 JWT token
function parseJwt(token) {
  try {
    const base64Url = token.split(".")[1];
    const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
    const jsonPayload = decodeURIComponent(
      atob(base64)
        .split("")
        .map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
        .join("")
    );
    return JSON.parse(jsonPayload);
  } catch (e) {
    return null;
  }
}

// 测试 API 端点
async function testEndpoint(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
    });
    clearTimeout(timeout);
    return {
      ok: response.ok,
      status: response.status,
      statusText: response.statusText,
      headers: Object.fromEntries(response.headers.entries()),
      body: await response.text(),
    };
  } catch (error) {
    clearTimeout(timeout);
    return {
      ok: false,
      error: error.message,
      isNetworkError: true,
    };
  }
}

// 主函数
async function main() {
  console.log(`${colors.cyan}========================================${colors.reset}`);
  console.log(`${colors.cyan}      Auth Token 验证工具${colors.reset}`);
  console.log(`${colors.cyan}========================================${colors.reset}\n`);

  // 1. 解析 Token 信息
  logInfo("正在解析 Token...");
  const decoded = parseJwt(AUTH_TOKEN);

  if (!decoded) {
    logError("Token 解析失败，格式无效");
    process.exit(1);
  }

  console.log(`\n${colors.cyan}Token 信息:${colors.reset}`);
  console.log(`  用户ID: ${decoded.userId}`);
  console.log(`  账号: ${decoded.accountNo}`);
  console.log(`  邮箱: ${decoded.email}`);
  console.log(`  类型: ${decoded.type}`);
  console.log(`  签发时间: ${new Date(decoded.iat * 1000).toLocaleString()}`);
  console.log(`  过期时间: ${new Date(decoded.exp * 1000).toLocaleString()}`);

  // 检查是否过期
  const now = Math.floor(Date.now() / 1000);
  const isExpired = decoded.exp < now;
  const expiresIn = decoded.exp - now;

  if (isExpired) {
    logError(`Token 已过期 (过期 ${Math.abs(expiresIn)} 秒)`);
  } else {
    const days = Math.floor(expiresIn / 86400);
    const hours = Math.floor((expiresIn % 86400) / 3600);
    const minutes = Math.floor((expiresIn % 3600) / 60);
    logSuccess(`Token 未过期，还剩 ${days}天 ${hours}小时 ${minutes}分钟`);
  }

  console.log(`\n${colors.cyan}----------------------------------------${colors.reset}\n`);

  // 2. 测试无认证访问（应该失败）
  logInfo("测试1: 无认证访问受保护接口...");
  const noAuthResult = await testEndpoint(`${API_BASE}/skills`);

  if (noAuthResult.ok) {
    logWarn("无认证也能访问，API 可能没有启用认证");
  } else if (noAuthResult.status === 401 || noAuthResult.status === 403) {
    logSuccess(`无认证访问被拒绝 (HTTP ${noAuthResult.status})，认证机制正常`);
  } else if (noAuthResult.isNetworkError) {
    logError(`网络错误: ${noAuthResult.error}`);
    console.log("\n请确保服务器已启动: http://localhost:5173");
    process.exit(1);
  } else {
    logWarn(`意外响应: HTTP ${noAuthResult.status}`);
  }

  console.log("");

  // 3. 测试带认证访问
  logInfo("测试2: 使用 Token 访问受保护接口...");

  // 尝试多种认证方式
  const authMethods = [
    {
      name: "Bearer Token (Authorization Header)",
      headers: {
        Authorization: `Bearer ${AUTH_TOKEN}`,
        "Content-Type": "application/json",
      },
    },
    {
      name: "Cookie (refreshToken)",
      headers: {
        Cookie: `refreshToken=${AUTH_TOKEN}`,
        "Content-Type": "application/json",
      },
    },
    {
      name: "Cookie (authorization)",
      headers: {
        Cookie: `authorization=${AUTH_TOKEN}`,
        "Content-Type": "application/json",
      },
    },
  ];

  let anySuccess = false;

  for (const method of authMethods) {
    logInfo(`尝试: ${method.name}`);
    const result = await testEndpoint(`${API_BASE}/skills`, {
      headers: method.headers,
    });

    if (result.ok) {
      logSuccess(`认证成功! HTTP ${result.status}`);
      console.log(`  响应: ${result.body.substring(0, 200)}...`);
      anySuccess = true;
      break;
    } else if (result.status === 401) {
      logError(`认证失败 (401) - ${method.name}`);
    } else if (result.status === 403) {
      logError(`权限不足 (403) - ${method.name}`);
    } else if (!result.isNetworkError) {
      logWarn(`HTTP ${result.status} - ${result.statusText}`);
    }
  }

  console.log(`\n${colors.cyan}----------------------------------------${colors.reset}\n`);

  // 4. 测试其他可能的端点
  logInfo("测试3: 尝试其他 API 端点...");
  const endpoints = [
    "/api/auth/me",
    "/api/auth/refresh",
    "/api/user/profile",
    "/api/skills/my",
  ];

  for (const endpoint of endpoints) {
    const result = await testEndpoint(`${BASE_URL}${endpoint}`, {
      headers: {
        Authorization: `Bearer ${AUTH_TOKEN}`,
        "Content-Type": "application/json",
      },
    });

    if (result.ok) {
      logSuccess(`${endpoint} -> HTTP ${result.status}`);
    } else if (!result.isNetworkError) {
      console.log(`  ${endpoint} -> HTTP ${result.status}`);
    }
  }

  console.log(`\n${colors.cyan}========================================${colors.reset}`);

  if (anySuccess) {
    logSuccess("Token 验证通过，可以正常使用!");
    process.exit(0);
  } else if (isExpired) {
    logError("Token 已过期，需要重新登录获取新 Token");
    process.exit(1);
  } else {
    logError("Token 验证失败，请检查 Token 是否正确或 API 端点是否匹配");
    process.exit(1);
  }
}

main().catch((error) => {
  logError(`脚本执行错误: ${error.message}`);
  process.exit(1);
});
