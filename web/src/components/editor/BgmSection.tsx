import { useRef, useState } from "react";
import { api } from "../../api";
import type { BgmCandidate, Project } from "../../types";
import { Slider } from "../Controls";
import { useI18n } from "../../i18n";

interface BgmSectionProps {
  project: Project;
  run: (action: () => Promise<Project>, shotId?: string) => Promise<Project | null>;
  setError: (msg: string) => void;
  draft: { bgm?: number };
  setDraft: (d: { bgm?: number }) => void;
}

export function BgmSection({ project, run, setError, draft, setDraft }: BgmSectionProps) {
  const { t } = useI18n();
  const bgmInput = useRef<HTMLInputElement>(null);
  const [bgmBusy, setBgmBusy] = useState(false);
  /** 搜索结果的候选列表（未下载），仅存在于本次检索会话中。 */
  const [previews, setPreviews] = useState<BgmCandidate[]>([]);
  const [searchQuery, setSearchQuery] = useState<string | undefined>(undefined);
  /** 正在下载的 remote_id */
  const [downloading, setDownloading] = useState<string | null>(null);

  const id = project.id;
  const bgmAsset = project.bgm.asset_id ? project.assets[project.bgm.asset_id] : undefined;

  async function uploadBgm(file: File) {
    try {
      const asset = await api.upload(id, file);
      if (asset.kind !== "audio") throw new Error(t("editor.bgmNotAudio"));
      await run(() => api.updateProject(id, { bgm: { ...project.bgm, asset_id: asset.id } }));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function searchBgm() {
    setBgmBusy(true);
    setError("");
    try {
      const result = await api.bgmCandidates(id, project.bgm.query || "");
      setPreviews(result.candidates);
      setSearchQuery(result.query);
      // 有候选时自动下载第一首并选中，与旧行为一致。
      if (result.candidates.length > 0) {
        await selectCandidate(result.candidates[0]);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBgmBusy(false);
    }
  }

  async function selectCandidate(candidate: BgmCandidate) {
    setDownloading(candidate.remote_id);
    try {
      await run(() => api.bgmDownload(id, candidate));
      // 选中后从预览列表移除已下载条目，避免重复下载。
      setPreviews((prev) => prev.filter((c) => c.remote_id !== candidate.remote_id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDownloading(null);
    }
  }

  return (
    <div className="grid-2">
      <div className="field">
        <span className="label">{t("editor.bgm")}</span>
        <input
          ref={bgmInput}
          type="file"
          accept="audio/*"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) uploadBgm(file);
            e.target.value = "";
          }}
        />
        <div className="row">
          <button type="button" className="btn" onClick={() => bgmInput.current?.click()}>
            {bgmAsset ? t("editor.bgmReplace") : t("editor.bgmUpload")}
          </button>
          <button type="button" className="btn" disabled={bgmBusy} onClick={searchBgm}>
            {bgmBusy ? t("editor.bgmSearching") : t("editor.bgmSearch")}
          </button>
          {bgmAsset && (
            <>
              <span className="muted small ellipsis">{bgmAsset.meta.title ?? bgmAsset.meta.filename ?? t("editor.bgmUploaded")}</span>
              <button
                type="button"
                className="btn ghost"
                onClick={() => run(() => api.updateProject(id, { bgm: { ...project.bgm, asset_id: null } }))}
              >
                {t("editor.bgmRemove")}
              </button>
            </>
          )}
        </div>
        {searchQuery && <p className="hint">{t("editor.bgmQuery", { query: searchQuery })}</p>}

        {/* 试听候选列表（搜索结果，未下载） */}
        {previews.length > 0 && (
          <div className="bgm-candidates" role="listbox" aria-label={t("editor.bgmCandidates")}>
            {previews.map((c) => {
              const isLoading = downloading === c.remote_id;
              return (
                <button
                  type="button"
                  key={c.remote_id}
                  role="option"
                  aria-selected={false}
                  className="bgm-candidate"
                  disabled={isLoading}
                  onClick={() => selectCandidate(c)}
                  title={c.author ? t("shot.author", { name: c.author }) : c.title}
                >
                  <span className="bgm-candidate-title">{c.title || c.remote_id}</span>
                  <span className="muted small">
                    {c.duration ? t("editor.bgmDuration", { n: Math.round(c.duration) }) : ""}
                    {c.author ? ` · ${c.author}` : ""}
                    {c.meta.license_kind === "unknown" ? ` · ${t("editor.bgmLicenseUnknown")}` : ""}
                  </span>
                  {c.meta.license && (
                    <span className="muted small ellipsis">{t("editor.bgmLicense", { license: c.meta.license })}</span>
                  )}
                  {/* 试听直接用 preview_url，无需下载整首 */}
                  {c.preview_url ? (
                    <audio src={c.preview_url} controls preload="none" onClick={(e) => e.stopPropagation()} />
                  ) : null}
                  {isLoading && <span className="muted small">{t("editor.bgmSearching")}</span>}
                </button>
              );
            })}
          </div>
        )}

        {/* 已下载并登记为素材的候选（来自 project.bgm.candidates） */}
        {(() => {
          const assetCandidates = (project.bgm.candidates ?? [])
            .map((assetId) => project.assets[assetId])
            .filter((asset): asset is NonNullable<typeof asset> => asset?.kind === "audio");
          if (assetCandidates.length === 0) return null;
          return (
            <div className="bgm-candidates" role="listbox" aria-label={t("editor.bgmCandidates")}>
              {assetCandidates.map((asset) => (
                <button
                  type="button"
                  key={asset.id}
                  role="option"
                  aria-selected={project.bgm.asset_id === asset.id}
                  className={`bgm-candidate ${project.bgm.asset_id === asset.id ? "on" : ""}`}
                  onClick={() => run(() => api.updateProject(id, { bgm: { ...project.bgm, asset_id: asset.id } }))}
                  title={asset.meta.author ? t("shot.author", { name: asset.meta.author }) : asset.meta.title}
                >
                  <span className="bgm-candidate-title">{asset.meta.title ?? asset.id}</span>
                  <span className="muted small">
                    {asset.duration ? t("editor.bgmDuration", { n: Math.round(asset.duration) }) : ""}
                    {asset.meta.author ? ` · ${asset.meta.author}` : ""}
                    {asset.meta.license_kind === "unknown" ? ` · ${t("editor.bgmLicenseUnknown")}` : ""}
                  </span>
                  {asset.meta.license && (
                    <span className="muted small ellipsis">{t("editor.bgmLicense", { license: asset.meta.license })}</span>
                  )}
                  <audio src={`/api/v2/projects/${id}/assets/${asset.id}`} controls preload="none" onClick={(e) => e.stopPropagation()} />
                </button>
              ))}
            </div>
          );
        })()}
      </div>
      <Slider
        id="bgm-volume"
        label={t("editor.bgmVolume")}
        value={draft.bgm ?? project.bgm.volume}
        min={0}
        max={1}
        step={0.05}
        format={(v) => `${Math.round(v * 100)}%`}
        onInput={(bgm) => setDraft({ ...draft, bgm })}
        onCommit={(volume) =>
          run(() => api.updateProject(id, { bgm: { ...project.bgm, volume } })).then(() =>
            setDraft({ ...draft, bgm: undefined })
          )
        }
      />
    </div>
  );
}
