import { useEffect, useRef, useState } from "react";
import { api, assetUrl, estimateSeconds, plainText, renderUrl } from "../api";
import { clipRange, nextPosition } from "../clip";
import { useI18n } from "../i18n";
import { Segmented } from "./Controls";
import type { MessageKey } from "../messages";
import { subtitleMargin } from "../types";
import { textCss, useFontReady, useFonts, weightFile } from "../textStyle";
import { highlightKeywords } from "./editor/KeywordField";
import type { TextStyle } from "../types";
import type { CheckIssue, CostEstimate, Job, PlatformInfo, PrecheckRisk, Project, RenderResult, SpendMetrics } from "../types";
import { riskText } from "../risks";

const alignFlex = (align: string | undefined) =>
  ({ left: "flex-start", center: "center", right: "flex-end" })[align ?? "center"] ?? "center";

function Preview({ project, shotIndex, platform }: { project: Project; shotIndex: number; platform?: PlatformInfo }) {
  const { t } = useI18n();
  const shot = project.shots[shotIndex];
  const frame = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(400);

  useEffect(() => {
    if (!frame.current) return;
    const observer = new ResizeObserver(([entry]) => setHeight(entry.contentRect.height));
    observer.observe(frame.current);
    return () => observer.disconnect();
  }, []);

  const style = project.subtitle;
  const families = useFonts();
  const headlineStyle = project.titles?.headline;
  const shotTitleStyle = project.titles?.shot;
  const fileFor = (item: TextStyle | undefined) =>
    item ? weightFile(families, item.font ?? style.font, item.bold) : null;
  const subtitleFile = fileFor(style);
  const headlineFile = fileFor(headlineStyle);
  const shotTitleFile = fileFor(shotTitleStyle);
  useFontReady([subtitleFile, headlineFile, shotTitleFile]);
  /** 入场效果在预览里循环播放；样式一变就从头播放。 */
  const animClass = (item: TextStyle) => `preview-text anim-${item.animation}`;
  const animKey = (item: TextStyle) => JSON.stringify(item);
  /** 循环效果套在外层，和入场效果的动画互不覆盖。 */
  const loopClass = (item: TextStyle) => `preview-loop loop-${item.loop ?? "none"}`;
  const scale = height / (project.canvas.aspect === "16:9" ? 1080 : project.canvas.aspect === "1:1" ? 1080 : 1920);
  const asset = shot?.visual.selected && shot.visual.mode !== "color" ? project.assets[shot.visual.selected] : undefined;
  const position = { top: "flex-start", middle: "center", bottom: "flex-end" }[style.position];
  const fit = shot?.visual.fit === "contain" ? "contain" : "cover";
  const range = shot ? clipRange(shot.visual, asset) : { start: 0, end: Infinity, skips: [] };
  const canvasHeight = project.canvas.aspect === "9:16" ? 1920 : 1080;
  const safe = platform && platform.id !== "general" ? platform.safe : null;
  // 和后端 render.safe_margin 一致：字幕挪进安全区，再留 24 像素。
  const baseMargin = subtitleMargin(style, project.canvas.aspect);
  const margin =
    safe && style.position !== "middle"
      ? Math.max(baseMargin, (style.position === "top" ? safe.top : safe.bottom) * canvasHeight + 24)
      : baseMargin;

  return (
    <div ref={frame} className={`preview a${project.canvas.aspect.replace(":", "-")}`} style={{ background: shot?.visual.color ?? "#1F2937" }}>
      {asset?.kind === "video" && (
        <video
          key={`${asset.id}:${range.start}:${range.end}`}
          src={assetUrl(project.id, asset.id)}
          muted
          autoPlay
          playsInline
          style={{ objectFit: fit }}
          onLoadedMetadata={(e) => {
            e.currentTarget.currentTime = range.start;
          }}
          onTimeUpdate={(e) => {
            // 预览和成片一致：只播入出点之间，跳过剪辑页剪掉的口误和停顿，播完回到入点。
            const el = e.currentTarget;
            const next = nextPosition(el.currentTime, range, true);
            if (next !== null) el.currentTime = next;
          }}
          onEnded={(e) => {
            e.currentTarget.currentTime = range.start;
            void e.currentTarget.play().catch(() => undefined);
          }}
        />
      )}
      {asset?.kind === "image" && <img src={assetUrl(project.id, asset.id)} alt="" style={{ objectFit: fit }} />}
      {style.enabled && shot?.text && (
        <div
          className="preview-subs"
          style={{
            justifyContent: position,
            alignItems: alignFlex(style.align),
            padding: `${margin * scale}px ${Math.max(7, (safe?.right ?? 0) * 100 + 1)}% ${margin * scale}px ${Math.max(7, (safe?.left ?? 0) * 100 + 1)}%`,
          }}
        >
          <span className={loopClass(style)}>
            <span key={animKey(style)} className={animClass(style)} style={textCss(style, style.size, scale, subtitleFile)}>
              {highlightKeywords(plainText(shot.text).replace(/[，。,]$/u, "").slice(0, 34), style).map((part, index) =>
                typeof part === "string" ? (
                  part
                ) : (
                  <span key={index} style={{ color: style.keyword_color, fontSize: `${style.keyword_scale}em` }}>
                    {part.word}
                  </span>
                ),
              )}
            </span>
          </span>
        </div>
      )}
      {(() => {
        const headline = project.titles?.headline;
        const shotStyle = project.titles?.shot;
        const showHeadline = !!headline?.enabled && !!headline.text.trim();
        const top = (m: number) => (safe ? Math.max(m, safe.top * canvasHeight + 24) : m);
        const headlineTop = headline ? top(headline.margin) : 0;
        const lines = headline ? headline.text.trim().split("\n").length : 0;
        let shotMargin = shotStyle ? (shotStyle.position === "top" ? top(shotStyle.margin) : shotStyle.margin) : 0;
        if (shotStyle?.position === "top" && showHeadline && headline) {
          shotMargin = Math.max(shotMargin, headlineTop + headline.size * 1.35 * lines + 24);
        }
        return (
          <>
            {showHeadline && headline && (
              <div className="preview-title" style={{ top: headlineTop * scale, justifyContent: alignFlex(headline.align) }}>
                <span className={loopClass(headline)}>
                  <span key={animKey(headline)} className={animClass(headline)} style={textCss(headline, headline.size, scale, headlineFile)}>
                    {headline.text}
                  </span>
                </span>
              </div>
            )}
            {shot?.title?.trim() && shotStyle && (
              <div
                className="preview-title"
                style={{
                  justifyContent: alignFlex(shotStyle.align),
                  ...(shotStyle.position === "top"
                    ? { top: shotMargin * scale }
                    : shotStyle.position === "bottom"
                      ? { bottom: shotMargin * scale }
                      : { top: "50%", transform: "translateY(-50%)" }),
                }}
              >
                <span className={loopClass(shotStyle)}>
                  <span key={animKey(shotStyle)} className={animClass(shotStyle)} style={textCss(shotStyle, shotStyle.size, scale, shotTitleFile)}>
                    {shot.title}
                  </span>
                </span>
              </div>
            )}
          </>
        );
      })()}
      {safe && (
        <div
          className="safe-area"
          title={t("render.safeArea")}
          style={{
            top: `${safe.top * 100}%`,
            bottom: `${safe.bottom * 100}%`,
            left: `${safe.left * 100}%`,
            right: `${safe.right * 100}%`,
          }}
        />
      )}
      {!shot && <span className="preview-empty">{t("render.noShots")}</span>}
    </div>
  );
}

export function RenderPanel(props: {
  project: Project;
  paidAvailable: boolean;
  shotIndex: number;
  onShotIndex: (index: number) => void;
  onRendered: () => void;
  onBudget: (budget: number | null) => void;
  onDelivery: (delivery: string) => void;
}) {
  const { project } = props;
  const { t, lang } = useI18n();
  /** 体检问题按 code 本地化；没有对应文案时用后端的中文说明。 */
  const checkupText = (issue: CheckIssue) => {
    if (issue.code.startsWith("risk_")) {
      // 生成前检查的风险：问题和建议都按界面语言显示。
      const { what, fix } = riskText(t, { code: issue.code.slice(5) as PrecheckRisk["code"], params: issue.params ?? {} });
      return t("checkup.risk", { n: issue.shot ?? "", what, fix });
    }
    const key = `checkup.${issue.code}` as MessageKey;
    const params = { ...(issue.params ?? {}) };
    if (typeof params.position === "string") params.position = t(`checkup.position.${params.position}` as MessageKey);
    const text = t(key, params);
    return text === key ? issue.message : text;
  };
  const [job, setJob] = useState<Job | null>(null);
  const [platforms, setPlatforms] = useState<PlatformInfo[]>([]);
  useEffect(() => {
    api.platforms().then(setPlatforms).catch(() => setPlatforms([]));
  }, []);
  const [latest, setLatest] = useState<(RenderResult & { created_at: number }) | null>(null);
  const [error, setError] = useState("");
  const onRendered = useRef(props.onRendered);
  onRendered.current = props.onRendered;

  useEffect(() => {
    api
      .renders(project.id)
      .then((data) => {
        setLatest(data.renders[0] ?? null);
        const running = data.jobs.find((j) => j.state === "queued" || j.state === "running");
        if (running) setJob(running);
      })
      .catch(() => undefined);
  }, [project.id]);

  useEffect(() => {
    if (!job || (job.state !== "queued" && job.state !== "running")) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api.job(job.id);
        setJob(next);
        if (next.state === "done" && next.result) {
          setLatest({ ...next.result, created_at: Date.now() / 1000 });
          onRendered.current();
        }
      } catch (e) {
        setError((e as Error).message);
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job]);

  const total = project.shots.reduce((sum, s) => sum + estimateSeconds(s, (s.voice ?? project.voice).rate), 0);
  const missing = project.shots.filter((s) => !s.visual.selected && s.visual.mode !== "color").length;
  const pendingAi = project.shots.filter(
    (s) => (s.visual.mode === "ai" || s.visual.mode === "image") && !s.visual.locked && !s.visual.selected,
  ).length;
  const [allowPaid, setAllowPaid] = useState(false);
  const [quality, setQuality] = useState<"draft" | "final">("final");
  const [costEstimate, setCostEstimate] = useState<CostEstimate | null>(null);
  const [budget, setBudget] = useState(project.budget == null ? "" : String(project.budget));
  useEffect(() => setBudget(project.budget == null ? "" : String(project.budget)), [project.budget]);
  const running = job?.state === "queued" || job?.state === "running";
  const result = job?.state === "done" ? job.result : latest;
  const [issues, setIssues] = useState<CheckIssue[]>([]);
  useEffect(() => {
    if (running) return;
    api
      .checkup(project.id, quality)
      .then((data) => setIssues(data.issues))
      .catch(() => setIssues([]));
  }, [project.id, project.revision, quality, running]);

  // 有待生成的 AI 镜头时，自动拉取费用预估。
  useEffect(() => {
    if (!pendingAi || running) { setCostEstimate(null); return; }
    api.costEstimate(project.id, quality).then(setCostEstimate).catch(() => setCostEstimate(null));
  }, [project.id, project.revision, pendingAi, running, quality]);

  // 花费指标：只在花过钱、且不在渲染时拉取。
  const [spend, setSpend] = useState<SpendMetrics | null>(null);
  useEffect(() => {
    if (running || project.cost_credits <= 0) { setSpend(null); return; }
    api.spend(project.id).then(setSpend).catch(() => setSpend(null));
  }, [project.id, project.revision, project.cost_credits, running]);

  async function start() {
    setError("");
    const paid = allowPaid && pendingAi > 0 && props.paidAvailable;
    if (paid && !window.confirm(t("render.confirmPaid", { n: pendingAi }))) return;
    try {
      setJob(await api.render(project.id, paid, quality));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  /** 整片样片：不花钱、草稿档，先把节奏和文案确认下来。 */
  async function startSample() {
    setError("");
    try {
      setJob(await api.render(project.id, false, "draft", true));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <aside className="side">
      <section className="card">
        <div className="card-head">
          <h2>{t("render.preview")}</h2>
          <span className="card-extra muted small">
            {t("render.shotOf", { i: Math.min(props.shotIndex + 1, project.shots.length), n: project.shots.length })}
          </span>
        </div>
        <Preview
          project={project}
          shotIndex={props.shotIndex}
          platform={platforms.find((item) => item.id === (project.delivery ?? "general"))}
        />
        <div className="preview-nav">
          <button type="button" className="btn" disabled={props.shotIndex <= 0} onClick={() => props.onShotIndex(props.shotIndex - 1)}>
            {t("render.prev")}
          </button>
          <button
            type="button"
            className="btn"
            disabled={props.shotIndex >= project.shots.length - 1}
            onClick={() => props.onShotIndex(props.shotIndex + 1)}
          >
            {t("render.next")}
          </button>
        </div>
        <dl className="stats">
          <div>
            <dt>{t("render.estimated")}</dt>
            <dd>{t("common.seconds", { n: Math.round(total) })}</dd>
          </div>
          <div>
            <dt>{t("render.shotCount")}</dt>
            <dd>{t("render.shotCountValue", { n: project.shots.length })}</dd>
          </div>
        </dl>
      </section>

      <section className="card">
        {issues.length > 0 && !running ? (
          <details className="hint warn checkup" open={issues.length <= 3}>
            <summary>{t("checkup.title", { n: issues.length })}</summary>
            <ul>
              {issues.map((issue, index) => (
                <li key={index}>
                  <span className="checkup-what">
                    {issue.shot ? (
                      <button type="button" className="linkish" onClick={() => props.onShotIndex(issue.shot! - 1)}>
                        {checkupText(issue)}
                      </button>
                    ) : (
                      checkupText(issue)
                    )}
                  </span>
                  {issue.remedies && issue.remedies.length > 0 && (
                    <span className="checkup-remedies">
                      {t("checkup.remedies")}
                      {issue.remedies.map((item, i) => (
                        <span key={i} className={`remedy ${item.cost}`}>
                          {t(`checkup.remedy.${item.cost}` as MessageKey)}：{item.message}
                        </span>
                      ))}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </details>
        ) : (
          missing > 0 &&
          !running && <p className="hint">{t("render.missing", { n: missing })}</p>
        )}
        {pendingAi > 0 && props.paidAvailable && !running && (
          <label className="check">
            <input type="checkbox" checked={allowPaid} onChange={(e) => setAllowPaid(e.target.checked)} />
            {t("render.allowPaid", { n: pendingAi })}
          </label>
        )}
        {!running && platforms.length > 0 && (
          <div className="field">
            <label className="label" htmlFor="delivery">
              {t("render.delivery")}
            </label>
            <select
              id="delivery"
              className="input"
              value={project.delivery ?? "general"}
              onChange={(e) => props.onDelivery(e.target.value)}
            >
              {platforms.map((item) => (
                <option key={item.id} value={item.id}>
                  {lang === "zh" ? item.name : item.name_en}
                </option>
              ))}
            </select>
            <p className="muted small">{t("render.deliveryHint")}</p>
          </div>
        )}
        {!running && (
          <div className="field">
            <span className="label">{t("render.quality")}</span>
            <Segmented
              label={t("render.quality")}
              value={quality}
              options={[
                { value: "draft", label: t("render.quality.draft"), title: t("render.quality.draftHint") },
                { value: "final", label: t("render.quality.final") },
              ]}
              onChange={setQuality}
            />
          </div>
        )}
        {costEstimate && costEstimate.total > 0 && !running && (
          <p className="hint">
            {t("render.costEstimate", { total: costEstimate.total.toFixed(3), currency: costEstimate.currency })}
            {costEstimate.total_visual > 0 && ` (${t("render.costVisual")}: ¥${costEstimate.total_visual.toFixed(3)})`}
            {costEstimate.total_tts > 0 && ` (${t("render.costTts")}: ¥${costEstimate.total_tts.toFixed(3)})`}
          </p>
        )}
        {project.cost_credits > 0 && (
          <p className="muted small">{t("render.spent", { total: project.cost_credits.toFixed(2) })}</p>
        )}
        {spend && spend.generated_shots > 0 && (
          <details className="spend-metrics">
            <summary>{t("spend.title")}</summary>
            <ul>
              <li>
                {t("spend.perSecond", {
                  value: spend.per_second.toFixed(3),
                  duration: spend.duration.toFixed(1),
                })}
              </li>
              {spend.first_pass_rate != null && (
                <li>{t("spend.firstPass", { rate: Math.round(spend.first_pass_rate * 100) })}</li>
              )}
              {spend.average_takes != null && (
                <li>{t("spend.takes", { value: spend.average_takes.toFixed(2) })}</li>
              )}
              <li className={spend.wasted > 0 ? "hint warn" : undefined}>
                {spend.wasted > 0
                  ? t("spend.wasted", { value: spend.wasted.toFixed(2), total: spend.total.toFixed(2) })
                  : t("spend.noWaste")}
              </li>
            </ul>
            {spend.duration_source === "estimate" && <p className="muted small">{t("spend.estimated")}</p>}
          </details>
        )}
        {!running && (
          <div className="inline-field budget">
            <label htmlFor="budget">{t("render.budget")}</label>
            <input
              id="budget"
              className="input compact"
              type="number"
              min={0}
              step={1}
              placeholder={t("render.budgetNone")}
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              onBlur={() => {
                const value = budget.trim() === "" ? null : Math.max(0, Number(budget));
                if (value !== (project.budget ?? null) && (value === null || Number.isFinite(value))) {
                  props.onBudget(value);
                }
              }}
            />
          </div>
        )}
        {running ? (
          <div className="progress-block">
            <div className="field-row">
              <span>{job?.message}</span>
              <span className="muted">{Math.round((job?.progress ?? 0) * 100)}%</span>
            </div>
            <div className="progress" role="progressbar" aria-valuenow={Math.round((job?.progress ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100}>
              <div style={{ width: `${(job?.progress ?? 0) * 100}%` }} />
            </div>
            <button type="button" className="btn" onClick={() => job && api.cancel(job.id).catch(() => undefined)}>
              {t("render.cancel")}
            </button>
          </div>
        ) : (
          <>
            <button type="button" className="btn primary big" disabled={project.shots.length === 0} onClick={start}>
              {result ? t("render.rerender") : t("render.render")}
            </button>
            <button
              type="button"
              className="btn sample-render"
              disabled={project.shots.length === 0}
              title={t("render.sampleHint")}
              onClick={startSample}
            >
              {t("render.sample")}
            </button>
            {job?.sample && job?.state === "done" && <p className="hint small">{t("render.sampleDone")}</p>}
          </>
        )}
        {job?.state === "failed" && <p className="hint error">{job.error}</p>}
        {error && <p className="hint error">{error}</p>}
        <p className="muted small center">{t("render.incremental")}</p>
      </section>

      {result && !running && (
        <section className="card">
          <div className="card-head">
            <h2>{t("render.result")}</h2>
            <span className="card-extra muted small">{t("common.seconds", { n: result.duration.toFixed(1) })}</span>
          </div>
          <video
            key={result.render_id}
            className={`result a${project.canvas.aspect.replace(":", "-")}`}
            src={renderUrl(project.id, result.render_id, result.video)}
            controls
            playsInline
          />
          <div className="result-links">
            <a className="btn" href={renderUrl(project.id, result.render_id, result.video)} download>
              {t("render.downloadVideo")}
            </a>
            {result.subtitles && (
              <a className="btn" href={renderUrl(project.id, result.render_id, result.subtitles)} download>
                {t("render.downloadSubs")}
              </a>
            )}
          </div>
          <p className="muted small">
            {t("render.reused", { a: result.stages.filter((s) => s.reused).length, b: result.stages.length })}
          </p>
          {result.warnings.map((w) => (
            <p key={w} className="hint warn">
              {w}
            </p>
          ))}
        </section>
      )}
    </aside>
  );
}
