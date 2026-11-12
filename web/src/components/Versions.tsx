import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import type { LanguageInfo, Project, VersionInfo } from "../types";

/**
 * 同一个视频的各语言版本：放在编辑页顶部，点开后列出已有版本、让大模型翻译出新的版本。
 * 翻译会生成一个新项目，和「标题与文字」那一步没有关系，所以不放在步骤里。
 */
export function Versions({
  project,
  languages,
  canTranslate,
}: {
  project: Project;
  languages: LanguageInfo[];
  /** 配好了大模型：翻译文案要用。 */
  canTranslate: boolean;
}) {
  const { t, lang } = useI18n();
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.versions(project.id).then(setVersions).catch(() => setVersions([]));
  }, [project.id, project.language]);

  // 点面板外面或按 Esc 关闭。
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  /** 中文界面：「繁体中文（台湾）· 繁體中文」，中文名在前，原文名不同时跟在后面。 */
  const name = (code: string) => {
    const info = languages.find((l) => l.code === code);
    if (!info) return code;
    if (lang !== "zh") return info.name;
    return info.name_zh !== info.name ? `${info.name_zh} · ${info.name}` : info.name_zh;
  };
  const others = versions.filter((v) => v.id !== project.id);
  const taken = new Set(versions.map((v) => v.language));
  const available = languages.filter((l) => !taken.has(l.code) && l.code !== project.language);
  const chosen = available.some((l) => l.code === target) ? target : (available[0]?.code ?? "");

  async function create() {
    if (!chosen) return;
    setBusy(true);
    setError("");
    try {
      const created = await api.createVersion(project.id, chosen);
      setOpen(false);
      window.location.hash = `#/p/${created.id}`;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="versions-menu" ref={box}>
      <button
        type="button"
        className={`btn ${open ? "on" : ""}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen(!open)}
      >
        {others.length > 0 ? t("versions.openCount", { n: others.length + 1 }) : t("versions.open")}
      </button>
      {open && (
        <div className="versions-panel" role="dialog" aria-label={t("versions.title")}>
          <p className="versions-current">{t("versions.currentIs", { name: name(project.language) })}</p>
          {others.length > 0 && (
            <div className="field">
              <span className="label">{t("versions.others")}</span>
              <ul className="versions">
                {others.map((v) => (
                  <li key={v.id}>
                    <a className="version" href={`#/p/${v.id}`} onClick={() => setOpen(false)}>
                      <strong>{name(v.language)}</strong>
                      <span className="muted small ellipsis">{v.title}</span>
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {available.length === 0 ? (
            <p className="muted small">{t("versions.none")}</p>
          ) : (
            <div className="field">
              <label className="label" htmlFor="version-language">
                {t("versions.make")}
              </label>
              <div className="row">
                <select
                  id="version-language"
                  className="input"
                  value={chosen}
                  disabled={busy || !canTranslate}
                  onChange={(e) => setTarget(e.target.value)}
                >
                  {available.map((l) => (
                    <option key={l.code} value={l.code}>
                      {name(l.code)}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn primary"
                  disabled={busy || !chosen || !canTranslate}
                  onClick={create}
                >
                  {busy && <span className="spinner" aria-hidden="true" />}
                  {busy ? t("versions.creating") : t("versions.create")}
                </button>
              </div>
              {canTranslate ? (
                <p className="muted small">{t("versions.hint")}</p>
              ) : (
                <p className="hint warn small">
                  {t("versions.needLlm")}{" "}
                  <a href="#/settings" onClick={() => setOpen(false)}>
                    {t("versions.gotoSettings")}
                  </a>
                </p>
              )}
            </div>
          )}
          {error && <p className="hint error">{error}</p>}
        </div>
      )}
    </div>
  );
}
