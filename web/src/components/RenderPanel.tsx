import { useEffect, useRef, useState } from "react";
import { api, assetUrl, estimateSeconds, renderUrl } from "../api";
import type { Job, Project, RenderResult } from "../types";

function Preview({ project, shotIndex }: { project: Project; shotIndex: number }) {
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
  const asset = shot?.visual.selected ? project.assets[shot.visual.selected] : undefined;
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
      {!shot && <span className="preview-empty">还没有镜头</span>}
    </div>
  );
}

export function RenderPanel(props: {
  project: Project;
  shotIndex: number;
  onShotIndex: (index: number) => void;
  onRendered: () => void;
}) {
  const { project } = props;
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
  const running = job?.state === "queued" || job?.state === "running";
  const result = job?.state === "done" ? job.result : latest;

  async function start() {
    setError("");
    try {
      setJob(await api.render(project.id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <aside className="side">
      <section className="card">
        <div className="card-head">
          <h2>预览</h2>
          <span className="card-extra muted small">
            镜头 {Math.min(props.shotIndex + 1, project.shots.length)} / {project.shots.length}
          </span>
        </div>
        <Preview project={project} shotIndex={props.shotIndex} />
        <div className="preview-nav">
          <button type="button" className="btn" disabled={props.shotIndex <= 0} onClick={() => props.onShotIndex(props.shotIndex - 1)}>
            上一个
          </button>
          <button
            type="button"
            className="btn"
            disabled={props.shotIndex >= project.shots.length - 1}
            onClick={() => props.onShotIndex(props.shotIndex + 1)}
          >
            下一个
          </button>
        </div>
        <dl className="stats">
          <div>
            <dt>预计时长</dt>
            <dd>{Math.round(total)} 秒</dd>
          </div>
          <div>
            <dt>镜头</dt>
            <dd>{project.shots.length} 个</dd>
          </div>
        </dl>
      </section>

      <section className="card">
        {missing > 0 && !running && (
          <p className="hint">
            {missing} 个镜头还没有画面。渲染时会自动检索一次素材；检索不到时使用纯色画面。
          </p>
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
              取消
            </button>
          </div>
        ) : (
          <button type="button" className="btn primary big" disabled={project.shots.length === 0} onClick={start}>
            {result ? "重新渲染" : "生成视频"}
          </button>
        )}
        {job?.state === "failed" && <p className="hint error">{job.error}</p>}
        {error && <p className="hint error">{error}</p>}
        <p className="muted small center">只会重做有改动的镜头</p>
      </section>

      {result && !running && (
        <section className="card">
          <div className="card-head">
            <h2>成片</h2>
            <span className="card-extra muted small">{result.duration.toFixed(1)} 秒</span>
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
              下载视频
            </a>
            {result.subtitles && (
              <a className="btn" href={renderUrl(project.id, result.render_id, result.subtitles)} download>
                下载字幕
              </a>
            )}
          </div>
          <p className="muted small">
            本次复用了 {result.stages.filter((s) => s.reused).length} / {result.stages.length} 个阶段的缓存
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
