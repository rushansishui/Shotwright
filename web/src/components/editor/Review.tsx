import { useEffect, useRef, useState } from "react";
import { api, assetUrl } from "../../api";
import { useI18n } from "../../i18n";
import type { MessageKey } from "../../messages";
import type { Project, Shot } from "../../types";
import { Segmented } from "../Controls";
import { TakeBadge } from "../ShotCard";

type Filter = "all" | "todo" | "redo";
type Run = (action: () => Promise<Project>, shotId?: string) => Promise<Project | null>;

/**
 * 这个镜头最该先看的原因：0 最急。检查出问题的最急，其次是得分低的，
 * 没看图检查过的排最后。同一档里保持原顺序。
 */
function urgency(shot: Shot): number {
  const visual = shot.visual;
  const take = (visual.takes ?? []).find((item) => item.asset_id === visual.selected);
  if (take?.issue) return 0;
  if (visual.review === "redo") return 1;
  const score = take?.score;
  // 得分低于 0.6 的算「不太对」，排在没问题但也没检查过的前面。
  if (typeof score === "number" && score < 0.6) return 2;
  if (typeof score === "number") return 4;
  return 3;
}

/**
 * 审片：所有镜头的候选画面排在一页里对比。点一下选用某个版本，逐个标记通过或重做，
 * 重做可以写一句修改意见；标记好以后用上方的按钮一次性重新生成。
 */
export function ReviewBoard({
  project,
  run,
  busy,
}: {
  project: Project;
  run: Run;
  busy: (shotId: string) => boolean;
}) {
  const { t } = useI18n();
  const [filter, setFilter] = useState<Filter>("all");
  const marks = project.shots.map((s) => s.visual.review ?? "");
  const ok = marks.filter((m) => m === "ok").length;
  const redo = marks.filter((m) => m === "redo").length;
  const shown = project.shots
    .map((shot, index) => ({ shot, index }))
    .filter(({ shot }) => {
      const mark = shot.visual.review ?? "";
      return filter === "all" || (filter === "todo" ? mark === "" : mark === "redo");
    })
    // 检查出问题和得分低的镜头排在前面，先看最可能有问题的。
    .sort((a, b) => urgency(a.shot) - urgency(b.shot));

  const aspect = project.canvas.aspect.replace(":", " / ");
  const width = project.canvas.aspect === "9:16" ? 108 : project.canvas.aspect === "1:1" ? 140 : 180;
  return (
    <div className="review" style={{ ["--take-aspect" as string]: aspect, ["--take-width" as string]: `${width}px` }}>
      <p className="muted review-intro">{t("review.intro")}</p>
      <div className="review-toolbar">
        <span className="review-summary">{t("review.summary", { ok, redo, todo: marks.length - ok - redo })}</span>
        <Segmented
          label={t("review.filter")}
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: t("review.filter.all") },
            { value: "todo", label: t("review.filter.todo") },
            { value: "redo", label: t("review.filter.redo") },
          ]}
        />
      </div>
      {shown.length === 0 && <p className="muted">{t("review.empty")}</p>}
      {shown.map(({ shot, index }) => (
        <ReviewRow key={shot.id} project={project} shot={shot} index={index} run={run} busy={busy(shot.id)} />
      ))}
    </div>
  );
}

function ReviewRow({
  project,
  shot,
  index,
  run,
  busy,
}: {
  project: Project;
  shot: Shot;
  index: number;
  run: Run;
  busy: boolean;
}) {
  const { t } = useI18n();
  const visual = shot.visual;
  const mark = visual.review ?? "";
  const [note, setNote] = useState(visual.review_note ?? "");
  useEffect(() => setNote(visual.review_note ?? ""), [visual.review_note]);
  const generated = visual.mode === "ai" || visual.mode === "image";
  const candidates = visual.candidates.filter((id) => project.assets[id]);
  const takes = visual.takes ?? [];
  const issue = visual.selected ? takes.find((item) => item.asset_id === visual.selected)?.issue : "";
  const update = (patch: { review?: "" | "ok" | "redo"; review_note?: string; selected?: string }) =>
    run(() => api.updateShot(project.id, shot.id, { visual: patch }));

  return (
    <section className={`review-row ${mark}`} aria-label={t("review.shot", { n: index + 1 })}>
      <div className="review-info">
        <div className="review-head">
          <strong>{t("review.shot", { n: index + 1 })}</strong>
          <span className="muted">{t(`shot.mode.${visual.mode}` as MessageKey)}</span>
          {busy && <span className="spinner" aria-hidden="true" />}
        </div>
        {shot.title && <div className="review-title">{shot.title}</div>}
        <p className="review-text">{shot.text}</p>
      </div>

      <div className="review-takes">
        {candidates.length === 0 &&
          (visual.mode === "color" ? (
            <div className="review-take swatch" style={{ background: visual.color }} />
          ) : (
            <p className="muted review-none">{t("review.noCandidates")}</p>
          ))}
        {candidates.map((assetId) => {
          const on = assetId === visual.selected;
          return (
            <button
              type="button"
              key={assetId}
              className={`review-take ${on ? "on" : ""}`}
              aria-pressed={on}
              title={on ? t("review.selected") : t("review.choose")}
              disabled={visual.locked && !on}
              onClick={() => !on && update({ selected: assetId })}
            >
              <ReviewMedia project={project} assetId={assetId} />
              <TakeBadge takes={takes} assetId={assetId} asset={project.assets[assetId]} />
              {on && <span className="review-picked">{t("review.selected")}</span>}
            </button>
          );
        })}
      </div>

      <div className="review-actions">
        <div className="review-marks" role="group">
          <button
            type="button"
            className={`btn small ${mark === "ok" ? "mark-ok" : ""}`}
            aria-pressed={mark === "ok"}
            onClick={() => update({ review: mark === "ok" ? "" : "ok" })}
          >
            ✓ {t("review.ok")}
          </button>
          <button
            type="button"
            className={`btn small ${mark === "redo" ? "mark-redo" : ""}`}
            aria-pressed={mark === "redo"}
            onClick={() => update({ review: mark === "redo" ? "" : "redo" })}
          >
            ↻ {t("review.redo")}
          </button>
        </div>
        {issue && <p className="hint warn review-issue">{issue}</p>}
        {mark === "redo" && (
          <>
            <textarea
              className="input review-note"
              rows={2}
              maxLength={300}
              value={note}
              placeholder={t("review.notePlaceholder")}
              aria-label={t("review.notePlaceholder")}
              onChange={(e) => setNote(e.target.value)}
              onBlur={() => note.trim() !== (visual.review_note ?? "") && update({ review_note: note })}
            />
            <p className="muted review-note-hint">{generated ? t("review.noteHint") : t("review.notAi")}</p>
          </>
        )}
      </div>
    </section>
  );
}

/** 候选画面：图片直接显示；视频鼠标移上去静音播放，移开回到开头。 */
function ReviewMedia({ project, assetId }: { project: Project; assetId: string }) {
  const asset = project.assets[assetId];
  const video = useRef<HTMLVideoElement>(null);
  if (asset?.kind === "image") return <img src={assetUrl(project.id, assetId)} alt="" loading="lazy" />;
  if (asset?.kind !== "video") return null;
  return (
    <video
      ref={video}
      src={`${assetUrl(project.id, assetId)}#t=0.5`}
      muted
      loop
      playsInline
      preload="metadata"
      onMouseEnter={() => video.current?.play().catch(() => undefined)}
      onMouseLeave={() => {
        const element = video.current;
        if (!element) return;
        element.pause();
        element.currentTime = 0.5;
      }}
      onFocus={() => video.current?.play().catch(() => undefined)}
    />
  );
}
