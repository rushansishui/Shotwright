import { useRef, useState } from "react";
import { api } from "../../api";
import { useI18n } from "../../i18n";
import type { Project, SfxSettings } from "../../types";
import { Slider } from "../Controls";

interface SfxSectionProps {
  project: Project;
  run: (action: () => Promise<Project>, shotId?: string) => Promise<Project | null>;
  setError: (msg: string) => void;
}

const DEFAULT_SFX: SfxSettings = {
  transitions: false,
  transition_volume: 0.35,
  ambience: null,
  ambience_volume: 0.12,
  ambience_query: "",
};

/** 音效层：转场音效和环境声。 */
export function SfxSection({ project, run, setError }: SfxSectionProps) {
  const { t } = useI18n();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [volume, setVolume] = useState<number | undefined>(undefined);
  const sfx = { ...DEFAULT_SFX, ...(project.sfx ?? {}) };
  const ambience = sfx.ambience ? project.assets[sfx.ambience] : undefined;
  const update = (patch: Partial<SfxSettings>) => run(() => api.updateProject(project.id, { sfx: { ...sfx, ...patch } }));

  async function upload(file: File) {
    try {
      const asset = await api.upload(project.id, file, true);
      if (asset.kind !== "audio") throw new Error(t("editor.bgmNotAudio"));
      await update({ ambience: asset.id });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function search() {
    setBusy(true);
    setError("");
    try {
      await run(() => api.searchAmbience(project.id, sfx.ambience_query));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="field sfx">
      <span className="label">{t("sfx.title")}</span>
      <label className="check">
        <input type="checkbox" checked={sfx.transitions} onChange={(e) => update({ transitions: e.target.checked })} />
        {t("sfx.transitions")}
      </label>
      <div className="row">
        <span className="muted small">{ambience ? t("sfx.ambienceOn", { name: ambience.meta.title ?? ambience.id }) : t("sfx.ambienceOff")}</span>
        <input
          ref={input}
          type="file"
          accept="audio/*,video/*"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload(file);
            e.target.value = "";
          }}
        />
        <button type="button" className="btn small" disabled={busy} onClick={search} title={t("sfx.searchHint")}>
          {busy ? t("editor.bgmSearching") : t("sfx.search")}
        </button>
        <button type="button" className="btn small" onClick={() => input.current?.click()}>
          {t("sfx.upload")}
        </button>
        {ambience && (
          <button type="button" className="btn small ghost" onClick={() => update({ ambience: null })}>
            {t("editor.bgmRemove")}
          </button>
        )}
      </div>
      {ambience && (
        <Slider
          id="ambience-volume"
          label={t("sfx.ambienceVolume")}
          value={volume ?? sfx.ambience_volume}
          min={0}
          max={0.5}
          step={0.01}
          format={(v) => `${Math.round(v * 100)}%`}
          onInput={setVolume}
          onCommit={(value) => update({ ambience_volume: value }).then(() => setVolume(undefined))}
        />
      )}
    </div>
  );
}
