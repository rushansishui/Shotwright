import { defineConfig, devices } from "@playwright/test";

/**
 * 端到端测试：启动一个独立的 Shotwright 服务（临时数据目录），
 * 用真实浏览器走一遍主流程：新建项目 → 编辑镜头 → 渲染 → 下载。
 *
 * 首次运行前需要安装浏览器：npx playwright install chromium
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.SHOTWRIGHT_E2E_URL || "http://127.0.0.1:8765",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.SHOTWRIGHT_E2E_URL
    ? undefined
    : {
        // 用仓库里的 python 起服务；数据目录放在临时位置，跑完即弃。
        command:
          'python -m shotwright serve --host 127.0.0.1 --port 8765',
        url: "http://127.0.0.1:8765/api/v2/health",
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
        env: {
          SHOTWRIGHT_STORAGE: "storage/e2e-projects",
          SHOTWRIGHT_CONFIG: "config.e2e.toml",
        },
      },
});
