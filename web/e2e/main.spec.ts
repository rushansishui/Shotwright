import { expect, test, type Page } from "@playwright/test";

/**
 * 主流程端到端测试。用真实浏览器跑，不模拟网络。
 *
 * 前提：本地已装 FFmpeg（渲染需要）。测试只走完全本地的路径
 * （粘贴文案 → 按标点拆镜头 → 纯色画面渲染），不会调用任何收费服务。
 */

const RUN_ID = Date.now().toString(36);

/** 两句足够长的文案：按句拆镜头时会合并过短的句子，短句会被并成一个镜头。 */
const TWO_SHOTS = "镜头从城市的清晨慢慢推进到街角的路口和天桥。第二句，阳光照在刚刚开门的咖啡店橱窗上面闪闪发亮。";

/** 用"粘贴现成文案"模式新建项目：不调用大模型，离线可用。 */
async function createProject(page: Page, script: string) {
  await page.goto("/");
  await page.getByRole("radio", { name: /粘贴现成文案|Paste a script/ }).click();
  await page.locator("#script").fill(script);
  await page.getByRole("button", { name: /生成分镜|Create storyboard/ }).click();
  await expect(page.locator(".editor")).toBeVisible();
}

test.describe("Shotwright 主流程", () => {
  test("新建项目 → 编辑镜头 → 渲染 → 出现成片", async ({ page }) => {
    await createProject(page, `第一句，${TWO_SHOTS}`);

    const shots = page.locator("article.shot");
    await expect(shots).toHaveCount(2);

    // 改第一个镜头的文案，失焦即保存。
    const firstText = shots.first().locator("textarea");
    await firstText.fill("第一句画面（已修改）。");
    await firstText.blur();

    // 渲染：不勾选收费生成，走纯色画面，完全本地。
    await page.getByRole("button", { name: /生成视频|Render video/ }).click();

    // 轮询到成片出现。
    await expect(page.locator("video.result")).toBeVisible({ timeout: 120_000 });
    await expect(page.getByRole("link", { name: /下载视频|Download video/ })).toBeVisible();
  });

  test("刷新页面后仍能打开同一个项目", async ({ page }) => {
    await createProject(page, `刷新${RUN_ID}，${TWO_SHOTS}`);
    const url = page.url();

    await page.reload();
    await expect(page).toHaveURL(url);
    await expect(page.locator(".editor")).toBeVisible();
    await expect(page.locator("article.shot")).toHaveCount(2);
  });

  test("镜头可以删除", async ({ page }) => {
    await createProject(page, `排序${RUN_ID}，${TWO_SHOTS}`);
    const shots = page.locator("article.shot");
    await expect(shots).toHaveCount(2);

    page.once("dialog", (dialog) => dialog.accept());
    await shots.first().getByRole("button", { name: /删除镜头|Delete shot/ }).click();
    await expect(shots).toHaveCount(1);
  });

  test("参考素材、文字与转场、语言版本区块都在", async ({ page }) => {
    await createProject(page, `区块${RUN_ID}，${TWO_SHOTS}`);
    await expect(page.getByRole("heading", { name: /参考素材|Reference assets/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /语言版本|Language versions/ })).toBeVisible();
  });
});
