import { useRef, useState } from "react";
import { api } from "../../api";
import type { Project } from "../../types";
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
      const next = await run(() => api.bgmCandidates(id, project.bgm.query || ""));
      // 检索到之后自动选用第一首，省一次点击。
      const first = next?.bgm.candidates?.[0];
      if (next && first && !next.bgm.asset_id) {
        await run(() => api.updateProject(id, { bgm: { ...next.bgm, asset_id: first } }));
      }
    } finally {
      setBgmBusy(false);
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
        {project.bgm.query && <p className="hint">{t("editor.bgmQuery", { query: project.bgm.query })}</p>}
        {/* 自动检索到的背景音乐候选（上传的素材不在其中） */}
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
