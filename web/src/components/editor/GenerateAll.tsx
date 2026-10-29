import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { useI18n } from "../../i18n";
import type { GenerateAllStatus, GenerateJob, Project } from "../../types";

/** 结束超过一小时的一键生成结果不再提示。 */
const RESULT_TTL = 3600;

/**
 * 一键生成：列出还没有画面的 AI 镜头（或审片标记重做的镜头），确认总费用后在后台依次生成。
 * 生成过程中显示进度，可以随时停止；每做完一个镜头刷新一次项目。
 */
export function GenerateAll({
  project,
  locked,
  onlyRedo = false,
  onChanged,
  onCurrent,
  setError,
}: {
  project: Project;
  locked: boolean;
  onlyRedo?: boolean;
  onChanged: () => void;
  onCurrent?: (shotId: string | null) => void;
  setError: (message: string) => void;
}) {
  const { t } = useI18n();
  const id = project.id;
  const [status, setStatus] = useState<GenerateAllStatus | null>(null);
  const [draft, setDraft] = useState(false);
  const [starting, setStarting] = useState(false);
  const [dismissed, setDismissed] = useState<number | null>(null);
  const progress = useRef(-1);
  const quality = draft ? "draft" : "final";

  const refresh = useCallback(async () => {
    try {
      const next = await api.generateAllStatus(id, quality, onlyRedo);
      setStatus(next);
      return next;
    } catch {
      return null;
    }
  }, [id, quality, onlyRedo]);

  // 项目有变化（手动生成、换来源、审片标记）就重新算一次要生成的镜头。
  useEffect(() => {
    refresh();
  }, [refresh, project]);

  const job = status?.job ?? null;
  const running = job?.state === "running";
  const current = running ? job.current : null;
  useEffect(() => {
    onCurrent?.(current);
  }, [current, onCurrent]);
  useEffect(() => () => onCurrent?.(null), [onCurrent]);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(async () => {
      const next = await refresh();
      const latest = next?.job;
      if (!latest) return;
      const count = latest.done.length + latest.failed.length + latest.skipped.length;
      if (count !== progress.current || latest.state !== "running") {
        progress.current = count;
        onChanged();
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [running, refresh, onChanged]);

  const shotNumber = (shotId: string | null) => {
    const index = project.shots.findIndex((s) => s.id === shotId);
    return index < 0 ? "?" : String(index + 1);
  };

  async function start() {
    const plan = status?.plan;
    if (!plan || plan.items.length === 0) return;
    const lines = plan.items.map((item) => {
      const what = t(item.mode === "ai" ? "batch.what.ai" : "batch.what.image");
      const n = item.shot_index + 1;
      const line =
        item.cost > 0 ? t("batch.confirmLine", { n, what, cost: item.cost.toFixed(2) }) : t("batch.confirmFree", { n, what });
      return item.review_note ? line + t("batch.confirmNote", { note: item.review_note }) : line;
    });
    const shown = lines.length > 15 ? [...lines.slice(0, 15), "…"] : lines;
    const message = [
      t("batch.confirmTitle", { n: plan.items.length }),
      ...shown,
      "",
      plan.total > 0 ? t("batch.confirmTotal", { cost: plan.total.toFixed(2) }) : "",
      plan.paid ? t("batch.confirmPaid") : "",
      t("batch.confirmQuestion"),
    ]
      .filter((line, index, all) => line !== "" || (index > 0 && all[index - 1] !== ""))
      .join("\n");
    if (!window.confirm(message)) return;
    setStarting(true);
    setError("");
    try {
      await api.generateAll(id, { confirm_charge: true, quality, only_redo: onlyRedo });
      progress.current = 0;
      const next = await refresh();
      // 很快就做完时轮询不会启动，这里直接刷新一次项目。
      if (next?.job?.state !== "running") onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  }

  async function stop() {
    try {
      await api.cancelGenerateAll(id);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (running) {
    const done = job.done.length + job.failed.length + job.skipped.length;
    return (
      <div className="batch-bar running" role="status">
        <div className="batch-text">
          <span>
            {job.cancel_requested
              ? t("batch.stopping")
              : job.current
                ? t("batch.running", { done, total: job.total, n: shotNumber(job.current) })
                : t("batch.runningIdle", { done, total: job.total })}
          </span>
          <progress value={done} max={job.total} />
        </div>
        <button type="button" className="btn small" disabled={job.cancel_requested} onClick={stop}>
          {t("batch.stop")}
        </button>
      </div>
    );
  }

  const result = job && job.finished_at && dismissed !== job.finished_at && Date.now() / 1000 - job.finished_at < RESULT_TTL
    ? job
    : null;
  const plan = status?.plan;
  const items = plan?.items ?? [];
  const redo = items.filter((item) => item.reason === "redo").length;
  const missing = items.length - redo;
  const anyVideo = items.some((item) => item.mode === "ai");

  return (
    <>
      {result && <BatchResult job={result} shotNumber={shotNumber} onDismiss={() => setDismissed(result.finished_at)} />}
      {!locked && items.length > 0 && (
        <div className="batch-bar">
          <div className="batch-text">
            <span>
              {[
                !onlyRedo && missing > 0 ? t("batch.pending", { n: missing }) : "",
                redo > 0 && !onlyRedo ? t("batch.redoCount", { n: redo }) : "",
                plan && plan.total > 0 ? t("batch.estimate", { cost: plan.total.toFixed(2) }) : "",
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
            {anyVideo && (
              <label className="check">
                <input type="checkbox" checked={draft} onChange={(e) => setDraft(e.target.checked)} />
                {t("batch.draft")}
              </label>
            )}
          </div>
          <button type="button" className="btn primary small" disabled={starting} onClick={start}>
            {onlyRedo ? t("batch.startRedo", { n: items.length }) : t("batch.start")}
          </button>
        </div>
      )}
    </>
  );
}

function BatchResult({
  job,
  shotNumber,
  onDismiss,
}: {
  job: GenerateJob;
  shotNumber: (shotId: string | null) => string;
  onDismiss: () => void;
}) {
  const { t } = useI18n();
  const done = job.done.length;
  const headline =
    job.state === "stopped"
      ? t("batch.stopped", { reason: job.stopped_reason })
      : t(job.state === "cancelled" ? "batch.cancelled" : "batch.finished", { done });
  const failed = job.failed.length ? t("batch.failed", { n: job.failed.length }) : "";
  return (
    <div className={`batch-bar result ${job.failed.length ? "warn" : "ok"}`} role="status">
      <div className="batch-text">
        <span>{headline + (job.state === "stopped" ? "" : failed)}</span>
        {job.failed.length > 0 && job.state !== "stopped" && (
          <ul className="batch-failures">
            {job.failed.map((item) => (
              <li key={item.shot_id}>{t("batch.failedLine", { n: shotNumber(item.shot_id), error: item.error })}</li>
            ))}
          </ul>
        )}
      </div>
      <button type="button" className="btn small ghost" onClick={onDismiss}>
        {t("batch.dismiss")}
      </button>
    </div>
  );
}
