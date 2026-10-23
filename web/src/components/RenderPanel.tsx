import { useEffect, useRef, useState } from "react";
import { api, assetUrl, estimateSeconds, renderUrl } from "../api";
import { useI18n } from "../i18n";
import { Segmented } from "./Controls";
import type { CostEstimate, Job, Project, RenderResult } from "../types";

function Preview({ project, shotIndex }: { project: Project; shotIndex: number }) {
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
  const scale = height / (project.canvas.aspect === "16:9" ? 1080 : project.canvas.aspect === "1:1" ? 1080 : 1920);
  const asset = shot?.visual.selected && shot.visual.mode !== "color" ? project.assets[shot.visual.selected] : undefined;
  const position = { top: "flex-start", middle: "center", bottom: "flex-end" }[style.position];
  const fit = shot?.visual.fit === "contain" ? "contain" : "cover";

  return (
    <div ref={frame} className={`preview a${project.canvas.aspect.replace(":", "-")}`} style={{ background: shot?.visual.color ?? "#1F2937" }}>
      {asset?.kind === "video" && (
        <video key={asset.id} src={assetUrl(project.id, asset.id)} muted loop autoPlay playsInline style={{ objectFit: fit }} />
      )}
      {asset?.kind === "image" && <img src={assetUrl(project.id, asset.id)} alt="" style={{ objectFit: fit }} />}
      {style.enabled && shot?.text && (
        <div className="preview-subs" style={{ justifyContent: position, padding: `${style.margin * scale}px 7%` }}>
          <span
            style={{
              fontSize: Math.max(10, style.size * scale),
              color: style.color,
              WebkitTextStroke: `${Math.max(0.5, style.outline * scale * 1.4)}px ${style.outline_color}`,
              paintOrder: "stroke fill",
            }}
          >
            {shot.text.replace(/[，。,]$/u, "").slice(0, 34)}
          </span>
        </div>
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
}) {
  const { project } = props;
  const { t } = useI18n();
  const [job, setJob] = useState<Job | null>(null);
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

  // 有待生成的 AI 镜头时，自动拉取费用预估。
  useEffect(() => {
    if (!pendingAi || running) { setCostEstimate(null); return; }
    api.costEstimate(project.id, quality).then(setCostEstimate).catch(() => setCostEstimate(null));
  }, [project.id, project.revision, pendingAi, running, quality]);

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

  return (
    <aside className="side">
      <section className="card">
        <div className="card-head">
          <h2>{t("render.preview")}</h2>
          <span className="card-extra muted small">
            {t("render.shotOf", { i: Math.min(props.shotIndex + 1, project.shots.length), n: project.shots.length })}
          </span>
        </div>
        <Preview project={project} shotIndex={props.shotIndex} />
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
        {missing > 0 && !running && (
          <p className="hint">
            {t("render.missing", { n: missing })}
          </p>
        )}
        {pendingAi > 0 && props.paidAvailable && !running && (
          <label className="check">
            <input type="checkbox" checked={allowPaid} onChange={(e) => setAllowPaid(e.target.checked)} />
            {t("render.allowPaid", { n: pendingAi })}
          </label>
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
          <button type="button" className="btn primary big" disabled={project.shots.length === 0} onClick={start}>
            {result ? t("render.rerender") : t("render.render")}
          </button>
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
