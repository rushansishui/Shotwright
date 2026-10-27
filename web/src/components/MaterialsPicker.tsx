import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import type { MessageKey } from "../messages";
import type { MaterialKind, MaterialRole, VideoType } from "../types";

/** 新建视频时选好、还没上传的素材。文件在创建项目后才上传。 */
export interface PendingMaterial {
  key: string;
  file: File;
  kind: MaterialKind;
  role: MaterialRole;
  note: string;
}

const ROLES: Record<MaterialKind, MaterialRole[]> = {
  image: ["product", "person", "logo", "style", "footage"],
  video: ["footage"],
  audio: ["bgm"],
};

export const MAX_MATERIALS = 20;

export function materialKind(file: File): MaterialKind | null {
  const type = file.type.split("/")[0];
  if (type === "image" || type === "video" || type === "audio") return type;
  const ext = file.name.toLowerCase().split(".").pop() ?? "";
  if (["jpg", "jpeg", "png", "webp", "gif", "bmp", "heic"].includes(ext)) return "image";
  if (["mp4", "mov", "m4v", "webm", "mkv", "avi"].includes(ext)) return "video";
  if (["mp3", "m4a", "wav", "aac", "flac", "ogg"].includes(ext)) return "audio";
  return null;
}

function defaultRole(kind: MaterialKind, videoType: VideoType | null): MaterialRole {
  if (kind === "audio") return "bgm";
  if (kind === "video") return "footage";
  if (videoType === "talking") return "person";
  if (videoType === "explainer") return "footage";
  return "product";
}

function size(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function Preview({ item }: { item: PendingMaterial }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (item.kind === "audio") return;
    const next = URL.createObjectURL(item.file);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [item.file, item.kind]);
  if (item.kind === "image" && url) return <img src={url} alt="" />;
  // #t=0.1：让浏览器加载并显示第一帧，而不是一片空白。
  if (item.kind === "video" && url) return <video src={`${url}#t=0.1`} muted playsInline preload="metadata" />;
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <path d="M9 18V5l12-2v13" />
      <circle cx="6" cy="18" r="3" />
      <circle cx="18" cy="16" r="3" />
    </svg>
  );
}

interface MaterialsPickerProps {
  items: PendingMaterial[];
  onChange: (items: PendingMaterial[]) => void;
  videoType: VideoType | null;
  disabled?: boolean;
}

/** 新建视频时上传素材：图片、视频、音频，每个选好用途，可以写一句说明。 */
export function MaterialsPicker({ items, onChange, videoType, disabled }: MaterialsPickerProps) {
  const { t } = useI18n();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [skipped, setSkipped] = useState(0);

  function add(files: File[]) {
    const next = [...items];
    let rejected = 0;
    for (const file of files) {
      const kind = materialKind(file);
      if (!kind || next.length >= MAX_MATERIALS) {
        rejected += 1;
        continue;
      }
      next.push({ key: `${file.name}-${file.size}-${Math.random().toString(36).slice(2, 8)}`, file, kind, role: defaultRole(kind, videoType), note: "" });
    }
    setSkipped(rejected);
    onChange(next);
  }

  const patch = (key: string, change: Partial<PendingMaterial>) => onChange(items.map((item) => (item.key === key ? { ...item, ...change } : item)));

  return (
    <div className="field materials">
      <div className="field-row">
        <span className="label">{t("materials.title")}</span>
        <span className="muted small">{t("materials.hint")}</span>
      </div>
      <button
        type="button"
        className={`materials-drop ${dragging ? "over" : ""}`}
        disabled={disabled || items.length >= MAX_MATERIALS}
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (!disabled) add(Array.from(e.dataTransfer.files));
        }}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M12 16V4M6 10l6-6 6 6M4 20h16" />
        </svg>
        <span>{t("materials.drop")}</span>
        <span className="muted small">{t("materials.types")}</span>
      </button>
      <input
        ref={input}
        type="file"
        multiple
        hidden
        accept="image/*,video/*,audio/*"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          add(files);
        }}
      />
      {skipped > 0 && <p className="hint warn small">{t("materials.skipped", { n: skipped, max: MAX_MATERIALS })}</p>}
      {items.length > 0 && (
        <ul className="materials-list">
          {items.map((item, index) => (
            <li key={item.key} className="material-row">
              <div className={`material-thumb ${item.kind}`}>
                <Preview item={item} />
              </div>
              <div className="material-body">
                <div className="material-name">
                  <span className="tag">M{index + 1}</span>
                  <span className="material-file" title={item.file.name}>
                    {item.file.name}
                  </span>
                  <span className="muted small">{size(item.file.size)}</span>
                </div>
                <div className="material-fields">
                  <select
                    className="input"
                    aria-label={t("materials.role")}
                    value={item.role}
                    disabled={disabled || ROLES[item.kind].length === 1}
                    onChange={(e) => patch(item.key, { role: e.target.value as MaterialRole })}
                  >
                    {ROLES[item.kind].map((role) => (
                      <option key={role} value={role}>
                        {t(`materials.role.${role}` as MessageKey)}
                      </option>
                    ))}
                  </select>
                  <input
                    className="input"
                    aria-label={t("materials.note")}
                    placeholder={t(`materials.notePlaceholder.${item.role}` as MessageKey)}
                    value={item.note}
                    maxLength={200}
                    disabled={disabled}
                    onChange={(e) => patch(item.key, { note: e.target.value })}
                  />
                </div>
              </div>
              <button
                type="button"
                className="btn icon ghost danger"
                aria-label={t("materials.remove", { name: item.file.name })}
                title={t("materials.remove", { name: item.file.name })}
                disabled={disabled}
                onClick={() => onChange(items.filter((other) => other.key !== item.key))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
