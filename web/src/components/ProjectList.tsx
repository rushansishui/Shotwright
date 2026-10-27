import { useMemo, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import type { MessageKey } from "../messages";
import type { ProjectSummary } from "../types";
import { Icon } from "./Controls";

interface ProjectListProps {
  projects: ProjectSummary[];
  className?: string;
  /** 紧凑列表：一行一个项目（小封面 + 标题 + 状态），用在首页侧栏。 */
  compact?: boolean;
  loading?: boolean;
  onChanged: () => void;
  onError: (message: string) => void;
}

const PAGE = 12;
const COMPACT_PAGE = 30;

function duration(seconds: number | null | undefined): string {
  if (!seconds && seconds !== 0) return "";
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** 「刚刚 / 5 分钟前 / 昨天 14:30 / 10月21日」这样的时间。 */
function useRelativeTime() {
  const { t, lang } = useI18n();
  return (seconds: number) => {
    const date = new Date(seconds * 1000);
    const now = new Date();
    const diff = (now.getTime() - date.getTime()) / 1000;
    const locale = lang === "zh" ? "zh-CN" : "en";
    const clock = date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
    if (diff < 60) return t("projects.justNow");
    if (diff < 3600) return t("projects.minutesAgo", { n: Math.floor(diff / 60) });
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (date.getTime() >= startOfToday) return t("projects.today", { time: clock });
    if (date.getTime() >= startOfToday - 86400000) return t("projects.yesterday", { time: clock });
    const sameYear = date.getFullYear() === now.getFullYear();
    return date.toLocaleDateString(locale, sameYear ? { month: "short", day: "numeric" } : { year: "numeric", month: "short", day: "numeric" });
  };
}

/** 首页的项目列表：带封面的卡片网格，可以搜索、复制、删除。 */
export function ProjectList({ projects, onChanged, onError, className = "", compact = false, loading = false }: ProjectListProps) {
  const { t } = useI18n();
  const when = useRelativeTime();
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [broken, setBroken] = useState<Set<string>>(new Set());

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? projects.filter((p) => p.title.toLowerCase().includes(q)) : projects;
  }, [projects, query]);
  const page = compact ? COMPACT_PAGE : PAGE;
  const shown = showAll || query.trim() ? filtered : filtered.slice(0, page);

  async function duplicate(p: ProjectSummary) {
    setBusy(p.id);
    try {
      const copy = await api.duplicateProject(p.id);
      window.location.hash = `#/p/${copy.id}`;
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function remove(p: ProjectSummary) {
    if (!window.confirm(t("projects.deleteConfirm", { title: p.title }))) return;
    setBusy(p.id);
    try {
      await api.deleteProject(p.id);
      onChanged();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className={`card projects ${compact ? "compact" : ""} ${className}`}>
      <div className="projects-head">
        <h2>
          {t("home.recent")}
          {projects.length > 0 && <span className="projects-count">{projects.length}</span>}
        </h2>
        {projects.length > (compact ? 6 : 4) && (
          <label className="projects-search">
            <Icon name="search" />
            <input
              type="search"
              value={query}
              placeholder={t("projects.search")}
              aria-label={t("projects.search")}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
        )}
      </div>

      {loading ? (
        <p className="muted">{t("projects.loading")}</p>
      ) : projects.length === 0 ? (
        <p className="muted">{t("home.empty")}</p>
      ) : filtered.length === 0 ? (
        <p className="muted">{t("projects.noMatch")}</p>
      ) : (
        <ul className="project-grid">
          {shown.map((p) => {
            const render = p.render;
            const status = render ? (render.stale ? "edited" : "done") : "draft";
            const cover = p.cover && !broken.has(p.id) ? p.cover : null;
            return (
              <li key={p.id} className={`project-card ${busy === p.id ? "busy" : ""}`}>
                <a href={`#/p/${p.id}`} className="project-link">
                  <div className={`project-cover a${p.aspect.replace(":", "-")}`}>
                    {cover ? (
                      <>
                        <img className="cover-blur" src={cover} alt="" aria-hidden="true" loading="lazy" />
                        <img
                          className="cover-img"
                          src={cover}
                          alt=""
                          loading="lazy"
                          onError={() => setBroken((current) => new Set(current).add(p.id))}
                        />
                      </>
                    ) : (
                      <div className="cover-empty">
                        <Icon name="film" />
                      </div>
                    )}
                    {!compact && (
                      <>
                        <span className={`project-status ${status}`}>
                          {t(`projects.status.${status}` as MessageKey)}
                          {render?.duration ? ` · ${duration(render.duration)}` : ""}
                        </span>
                        <span className="project-aspect">{p.aspect}</span>
                      </>
                    )}
                    {compact && render?.duration ? <span className="project-duration">{duration(render.duration)}</span> : null}
                  </div>
                  <div className="project-info">
                    <span className="project-title" title={p.title}>
                      {p.title}
                    </span>
                    <span className="project-meta">
                      {compact && <span className={`status-dot ${status}`}>{t(`projects.status.${status}` as MessageKey)}</span>}
                      {compact && <span className="project-meta-aspect">{p.aspect}</span>}
                      {!p.language.startsWith("zh") && <span className="tag">{p.language}</span>}
                      {t("projects.shots", { n: p.shots })}
                      <span aria-hidden="true"> · </span>
                      <time dateTime={new Date(p.updated_at * 1000).toISOString()} title={new Date(p.updated_at * 1000).toLocaleString()}>
                        {when(p.updated_at)}
                      </time>
                    </span>
                  </div>
                </a>
                <div className="project-actions">
                  <button
                    type="button"
                    className="project-action"
                    disabled={busy !== null}
                    title={t("editor.duplicateHint")}
                    aria-label={`${t("editor.duplicate")}：${p.title}`}
                    onClick={() => duplicate(p)}
                  >
                    <Icon name="copy" />
                  </button>
                  <button
                    type="button"
                    className="project-action danger"
                    disabled={busy !== null}
                    title={t("projects.delete")}
                    aria-label={`${t("projects.delete")}：${p.title}`}
                    onClick={() => remove(p)}
                  >
                    <Icon name="trash" />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {!showAll && !query.trim() && filtered.length > page && (
        <button type="button" className="btn projects-more" onClick={() => setShowAll(true)}>
          {t("projects.showAll", { n: filtered.length })}
        </button>
      )}
    </section>
  );
}
