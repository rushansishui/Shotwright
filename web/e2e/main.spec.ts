import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

/**
 * 主流程端到端测试。用真实浏览器跑，不模拟网络。
 *
 * 前提：本地已装 FFmpeg（渲染需要）。测试只走完全本地的路径
 * （按标点拆镜头 → 纯色画面渲染），不会调用任何收费服务。
 */

const RUN_ID = Date.now().toString(36);

/** 两句足够长的文案：按句拆镜头时会合并过短的句子，短句会被并成一个镜头。 */
const TWO_SHOTS = "镜头从城市的清晨慢慢推进到街角的路口和天桥。第二句，阳光照在刚刚开门的咖啡店橱窗上面闪闪发亮。";

/** 直接调用接口按句拆镜头新建项目：不调用大模型，离线可用。 */
async function createProject(page: Page, script: string) {
  const response = await page.request.post("/api/v2/projects", { data: { mode: "split", script } });
  expect(response.ok()).toBeTruthy();
  const project = await response.json();
  await page.goto(`/#/p/${project.id}`);
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

  test("全片设定与主体库、文字与转场、语言版本区块都在", async ({ page }) => {
    await createProject(page, `区块${RUN_ID}，${TWO_SHOTS}`);
    await expect(page.getByRole("heading", { name: /全片设定与主体库|Look and references/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /文字与转场|Text & transitions/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /语言版本|Language versions/ })).toBeVisible();
  });

  test("草稿/成片两档可以切换", async ({ page }) => {
    await createProject(page, `画质${RUN_ID}，${TWO_SHOTS}`);
    const draft = page.getByRole("radio", { name: /草稿|Draft/ });
    await expect(draft).toBeVisible();
    await draft.click();
    await expect(draft).toHaveAttribute("aria-checked", "true");
  });

  test("原片导入后可以拆分、合并镜头", async ({ page }) => {
    // 两段画面完全不同的测试视频拼成一段"原片"。用 VP9 编码，无头浏览器也能播放和跳转。
    const dir = mkdtempSync(join(tmpdir(), "shotwright-e2e-"));
    const footage = join(dir, "footage.mp4");
    execFileSync("ffmpeg", [
      "-loglevel", "error", "-y",
      "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=3",
      "-f", "lavfi", "-i", "smptebars=size=640x360:rate=30:duration=3",
      "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
      "-map", "[v]", "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "500k",
      footage,
    ]);

    await createProject(page, `原片${RUN_ID}。`);
    const shots = page.locator("article.shot");
    await page.locator('input[type="file"][accept="video/*"]').setInputFiles(footage);
    await expect(shots).toHaveCount(2, { timeout: 60_000 });

    // 打开第一个镜头的剪辑面板，播放位置跳到 1 秒，在这里拆分。
    const first = shots.first();
    await first.locator("details.shot-edit summary").click();
    const video = first.locator("video.edit-video");
    await expect(video).toBeVisible();
    await video.evaluate(async (el: HTMLVideoElement) => {
      if (el.readyState < 1) await new Promise((resolve) => el.addEventListener("loadedmetadata", resolve, { once: true }));
      el.currentTime = 1;
    });
    const split = first.getByRole("button", { name: /处拆分|Split at/ });
    await expect(split).toBeEnabled();
    await split.click();
    await expect(shots).toHaveCount(3);

    // 合并回去。
    page.once("dialog", (dialog) => dialog.accept());
    await shots.first().getByRole("button", { name: /与下一个镜头合并|Merge with the next shot/ }).click();
    await expect(shots).toHaveCount(2);
  });

  test("诉求模板：选类型、填模板，提交前提醒没填的内容", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => localStorage.removeItem("shotwright.newVideoDraft"));
    await page.reload();
    await page.getByRole("radio", { name: /口播|Talking head/ }).click();
    await expect(page.locator("#idea")).toHaveAttribute("placeholder", /视频号|talking-head/);
    // 自己写诉求：直接进入第 2 步，按所选类型填好模板。
    await page.getByRole("button", { name: /自己写诉求|write the brief/ }).click();
    await expect(page.locator("#subject")).toHaveValue(/主讲人|Presenter/);
    await page.getByRole("button", { name: /直接生成分镜|Skip the script/ }).click();
    await expect(page.getByRole("alert")).toContainText(/没有填写|unfilled/);
    await expect(page.getByRole("button", { name: /仍然生成|Create anyway/ })).toBeVisible();
    // 填入例子后不再提醒。
    await page.getByRole("button", { name: /填入例子|Fill in example/ }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
  });

  test("全片设定：添加主体并保存外观", async ({ page }) => {
    await createProject(page, `全片设定${RUN_ID}。${TWO_SHOTS}`);
    const look = page.locator(".look");
    await look.getByRole("button", { name: /添加主体|Add subject/ }).click();
    const appearance = look.getByRole("textbox", { name: /外观|Appearance/ }).first();
    await appearance.fill("哑光白色圆柱形机身");
    await appearance.blur();
    await page.reload();
    await expect(page.locator(".look").getByRole("textbox", { name: /外观|Appearance/ }).first()).toHaveValue(
      "哑光白色圆柱形机身",
    );
    // 给主体上传一张实拍图，刷新后还在，也能删掉。
    const photo = join(mkdtempSync(join(tmpdir(), "shotwright-look-")), "product.png");
    execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=400x400:rate=1", "-frames:v", "1", photo]);
    const chooser = page.waitForEvent("filechooser");
    await page.locator(".look .look-image-add").first().click();
    await (await chooser).setFiles(photo);
    await expect(page.locator(".look .look-image img")).toHaveCount(1);
    await page.reload();
    await expect(page.locator(".look .look-image img")).toHaveCount(1);
    await page.locator(".look .look-image .remove").first().click();
    await expect(page.locator(".look .look-image")).toHaveCount(0);
  });

  test("分镜多选：一个镜头选两段素材，依次播放", async ({ page }) => {
    const dir = mkdtempSync(join(tmpdir(), "shotwright-multi-"));
    const clips = ["testsrc2", "smptebars"].map((pattern, index) => {
      const file = join(dir, `clip${index}.mp4`);
      execFileSync("ffmpeg", [
        "-y", "-loglevel", "error", "-f", "lavfi", "-i", `${pattern}=size=320x568:rate=24`, "-t", "1.5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", file,
      ]);
      return file;
    });
    await createProject(page, `多选${RUN_ID}。${TWO_SHOTS}`);
    const first = page.locator("article.shot").first();
    await first.getByRole("radio", { name: /上传|Upload/ }).click();
    const input = first.locator('input[type="file"][accept="video/*,image/*"]');
    for (const clip of clips) {
      await input.setInputFiles(clip);
      await expect(first.locator(".candidate-wrap")).toHaveCount(clips.indexOf(clip) + 1);
    }
    await expect(first.getByText(/多选几段素材|pick several clips/)).toBeVisible();
    const notPicked = first.locator(".candidate-wrap .pick:not(.on)");
    await notPicked.first().click();
    await expect(first.getByText(/已选 2 段素材|2 clips picked/)).toBeVisible();
    // 后上传的那段是选中的（第 1 段），刚加的排第 2。
    await expect(first.locator(".candidate-wrap .pick.on")).toHaveText(["2", "1"]);
    // 删除选中的那段：多选里的下一段顶上，只剩一段素材。
    page.once("dialog", (dialog) => dialog.accept());
    await first.locator(".candidate-wrap").nth(1).getByRole("button", { name: /删除这个素材|Remove this clip/ }).click();
    await expect(first.locator(".candidate-wrap")).toHaveCount(1);
    await expect(first.locator(".candidate.on")).toHaveCount(1);
  });

  test("复制为新项目：副本可以单独编辑", async ({ page }) => {
    await createProject(page, `复制${RUN_ID}。${TWO_SHOTS}`);
    const originalUrl = page.url();
    const count = await page.locator("article.shot").count();
    await page.getByRole("button", { name: /复制为新项目|Duplicate/ }).click();
    await expect(page).not.toHaveURL(originalUrl);
    await expect(page.locator("#title")).toHaveValue(/（副本）|\(copy\)/);
    await expect(page.locator("article.shot")).toHaveCount(count);
    await page.goto("/");
    await expect(page.locator(".project-grid li").first()).toContainText(/（副本）|\(copy\)/);
  });
});
