import { useEffect, useState } from "react";
import { api, assetUrl, estimateSeconds } from "../../api";
import { useI18n } from "../../i18n";
import type { MessageKey } from "../../messages";
import { riskText } from "../../risks";
import type { CameraMove, GeneratePlan, PrecheckRisk, Project, Shot, ShotUpdate } from "../../types";

type Run = (action: () => Promise<Project>, shotId?: string) => Promise<Project | null>;

const CAMERAS: CameraMove[] = ["auto", "static", "push_in", "pull_out", "pan", "truck", "orbit", "crane", "handheld"];

/** 镜头用文字描述画面的方式：AI 画面写画面描述，素材库写检索词，上传和纯色不用写。 */
function describes(shot: Shot): "prompt" | "terms" | null {
  if (shot.visual.mode === "ai" || shot.visual.mode === "image") return "prompt";
  if (shot.visual.mode === "stock") return "terms";
  return null;
}

/** 已经花在这个镜头上的钱（所有版本，含关键帧）。 */
function spentOn(shot: Shot): number {
  return (shot.visual.takes ?? []).reduce((sum, item) => sum + (item.cost ?? 0), 0);
}

/**
 * 分镜表：所有镜头排成一张表，一眼看完整条片子拍成什么样——口播、画面描述、运镜、衔接、
 * 画面来源、预计费用和生成前检查。表里可以直接改，失焦保存；可以导出成 CSV 或复制到表格软件，
 * 拿给别人确认后再花钱生成。
 */
export function BoardTable({ project, run, onOpenShot }: { project: Project; run: Run; onOpenShot: (index: number) => void }) {
  const { t } = useI18n();
  const [plan, setPlan] = useState<GeneratePlan | null>(null);
  const [risks, setRisks] = useState<Record<string, PrecheckRisk[]>>({});
  const [notice, setNotice] = useState("");

  // 预计费用：和「一键生成」用同一份计划（只算还没有画面、要生成的镜头）。
  const planKey = project.shots.map((s) => `${s.id}:${s.visual.mode}:${s.visual.provider}:${s.visual.selected}`).join("|");
  useEffect(() => {
    let stopped = false;
    api
      .generateAllStatus(project.id, "final")
      .then((status) => !stopped && setPlan(status.plan ?? null))
      .catch(() => !stopped && setPlan(null));
    return () => {
      stopped = true;
    };
  }, [project.id, planKey]);

  // 生成前检查：逐个 AI 镜头查一遍，画面描述改了就重新查。
  const promptKey = project.shots.map((s) => `${s.id}:${describes(s) === "prompt" ? s.visual.prompt : ""}`).join("|");
  useEffect(() => {
    let stopped = false;
    (async () => {
      const found: Record<string, PrecheckRisk[]> = {};
      for (const shot of project.shots) {
        if (describes(shot) !== "prompt" || !shot.visual.prompt.trim()) continue;
        try {
          found[shot.id] = (await api.precheck(project.id, shot.id)).risks;
        } catch {
          found[shot.id] = [];
        }
        if (stopped) return;
      }
      if (!stopped) setRisks(found);
    })();
    return () => {
      stopped = true;
    };
  }, [project.id, promptKey]);

  const costs = new Map((plan?.items ?? []).map((item) => [item.shot_id, item.cost]));
  const currency = plan?.currency === "USD" ? "$" : "¥";
  const rate = project.voice.rate;
  const seconds = (shot: Shot) => estimateSeconds(shot, (shot.voice ?? project.voice).rate ?? rate);
  const totalSeconds = project.shots.reduce((sum, shot) => sum + seconds(shot), 0);
  const totalSpent = project.shots.reduce((sum, shot) => sum + spentOn(shot), 0);
  const update = (shot: Shot, body: ShotUpdate) => run(() => api.updateShot(project.id, shot.id, body), shot.id);

  function rows(): string[][] {
    const head = [
      t("board.col.index"),
      t("board.col.seconds"),
      t("board.col.text"),
      t("board.col.visual"),
      t("board.col.camera"),
      t("board.col.link"),
      t("board.col.sourceOnly"),
      t("board.col.cost"),
      t("board.col.check"),
    ];
    const body = project.shots.map((shot, index) => {
      const kind = describes(shot);
      const visual = kind === "prompt" ? shot.visual.prompt : kind === "terms" ? shot.visual.terms.join(", ") : "";
      const cost = costs.get(shot.id);
      return [
        String(index + 1),
        seconds(shot).toFixed(1),
        shot.text,
        visual,
        kind === "prompt" ? t(`shot.camera.${shot.visual.camera ?? "auto"}` as MessageKey) : "",
        shot.visual.link ?? "",
        t(`shot.mode.${shot.visual.mode}` as MessageKey) + (shot.visual.provider ? ` · ${shot.visual.provider}` : ""),
        cost !== undefined ? `${currency}${cost.toFixed(2)}` : "",
        (risks[shot.id] ?? []).map((risk) => riskText(t, risk).what).join("；"),
      ];
    });
    return [head, ...body];
  }

  function exportCsv() {
    const quote = (cell: string) => `"${cell.replace(/"/g, '""')}"`;
    const csv = rows()
      .map((row) => row.map(quote).join(","))
      .join("\r\n");
    // 带 BOM，Excel 打开中文不乱码。
    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${(project.title || "storyboard").replace(/[\\/:*?"<>|]/g, "_")}-${t("board.file")}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function copyTable() {
    const tsv = rows()
      .map((row) => row.map((cell) => cell.replace(/[\t\r\n]+/g, " ")).join("\t"))
      .join("\n");
    try {
      await navigator.clipboard.writeText(tsv);
      setNotice(t("board.copied"));
    } catch {
      setNotice(t("board.copyFailed"));
    }
  }

  return (
    <div className="board">
      <div className="board-toolbar">
        <p className="muted small">{t("board.intro")}</p>
        <div className="row">
          <button type="button" className="btn small" onClick={copyTable}>
            {t("board.copy")}
          </button>
          <button type="button" className="btn small" onClick={exportCsv}>
            {t("board.export")}
          </button>
        </div>
      </div>
      {notice && <p className="hint small">{notice}</p>}
      <div className="board-scroll">
        <table className="board-table">
          <thead>
            <tr>
              <th className="col-index">{t("board.col.index")}</th>
              <th className="col-thumb">{t("board.col.thumb")}</th>
              <th className="col-text">{t("board.col.text")}</th>
              <th className="col-visual">{t("board.col.visual")}</th>
              <th className="col-camera">{t("board.col.camera")}</th>
              <th className="col-source">{t("board.col.source")}</th>
              <th className="col-check">{t("board.col.check")}</th>
            </tr>
          </thead>
          <tbody>
            {project.shots.map((shot, index) => (
              <BoardRow
                key={shot.id}
                project={project}
                shot={shot}
                index={index}
                seconds={seconds(shot)}
                cost={costs.get(shot.id)}
                currency={currency}
                risks={risks[shot.id] ?? []}
                onUpdate={(body) => update(shot, body)}
                onOpen={() => onOpenShot(index)}
              />
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={7}>
                {t("board.total", { n: project.shots.length, seconds: totalSeconds.toFixed(1) })}
                {plan && plan.total > 0 && ` · ${t("board.totalCost", { cost: `${currency}${plan.total.toFixed(2)}` })}`}
                {totalSpent > 0 && ` · ${t("board.totalSpent", { cost: `${currency}${totalSpent.toFixed(2)}` })}`}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

function BoardRow(props: {
  project: Project;
  shot: Shot;
  index: number;
  seconds: number;
  cost: number | undefined;
  currency: string;
  risks: PrecheckRisk[];
  onUpdate: (body: ShotUpdate) => void;
  onOpen: () => void;
}) {
  const { project, shot, index } = props;
  const { t } = useI18n();
  const visual = shot.visual;
  const kind = describes(shot);
  const [text, setText] = useState(shot.text);
  const [prompt, setPrompt] = useState(visual.prompt);
  const [terms, setTerms] = useState(visual.terms.join(", "));
  const [link, setLink] = useState(visual.link ?? "");
  useEffect(() => setText(shot.text), [shot.text]);
  useEffect(() => setPrompt(visual.prompt), [visual.prompt]);
  useEffect(() => setTerms(visual.terms.join(", ")), [visual.terms]);
  useEffect(() => setLink(visual.link ?? ""), [visual.link]);
  const spent = spentOn(shot);
  const locked = visual.locked;

  return (
    <tr className={props.risks.length ? "has-risk" : ""}>
      <td className="col-index">
        <button type="button" className="board-index" title={t("board.openShot")} onClick={props.onOpen}>
          {index + 1}
        </button>
        <div className="muted small">{t("board.seconds", { n: props.seconds.toFixed(1) })}</div>
        {shot.title && <div className="board-shot-title small">{shot.title}</div>}
      </td>
      <td className="col-thumb">
        <Thumb project={project} shot={shot} />
      </td>
      <td className="col-text">
        <textarea
          className="input"
          rows={3}
          maxLength={2000}
          value={text}
          aria-label={t("board.textLabel", { n: index + 1 })}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => text.trim() && text !== shot.text && props.onUpdate({ text })}
        />
        {shot.speech === "original" && <div className="muted small">{t("board.originalSpeech")}</div>}
      </td>
      <td className="col-visual">
        {kind === "prompt" && (
          <>
            <textarea
              className="input"
              rows={4}
              maxLength={2000}
              value={prompt}
              disabled={locked}
              placeholder={t("board.promptEmpty")}
              aria-label={t("board.promptLabel", { n: index + 1 })}
              onChange={(e) => setPrompt(e.target.value)}
              onBlur={() => prompt !== visual.prompt && props.onUpdate({ visual: { prompt } })}
            />
            <input
              className="input board-link"
              maxLength={400}
              value={link}
              disabled={locked}
              placeholder={t("board.linkPlaceholder")}
              aria-label={t("board.col.link")}
              title={t("board.col.link")}
              onChange={(e) => setLink(e.target.value)}
              onBlur={() => link !== (visual.link ?? "") && props.onUpdate({ visual: { link } })}
            />
          </>
        )}
        {kind === "terms" && (
          <>
            <input
              className="input"
              value={terms}
              disabled={locked}
              placeholder={t("board.termsPlaceholder")}
              aria-label={t("board.termsLabel", { n: index + 1 })}
              onChange={(e) => setTerms(e.target.value)}
              onBlur={() => {
                const next = terms
                  .split(/[,，]/)
                  .map((item) => item.trim())
                  .filter(Boolean)
                  .slice(0, 10);
                if (next.join(",") !== visual.terms.join(",")) props.onUpdate({ visual: { terms: next } });
              }}
            />
            <div className="muted small">{t("board.termsHint")}</div>
          </>
        )}
        {kind === null && <span className="muted small">{t(`board.noPrompt.${visual.mode}` as MessageKey)}</span>}
      </td>
      <td className="col-camera">
        {kind === "prompt" ? (
          <select
            className="input"
            value={visual.camera ?? "auto"}
            disabled={locked}
            aria-label={t("board.col.camera")}
            onChange={(e) => props.onUpdate({ visual: { camera: e.target.value as CameraMove } })}
          >
            {CAMERAS.map((camera) => (
              <option key={camera} value={camera}>
                {t(`shot.camera.${camera}` as MessageKey)}
              </option>
            ))}
          </select>
        ) : (
          <span className="muted small">—</span>
        )}
      </td>
      <td className="col-source">
        <div>{t(`shot.mode.${visual.mode}` as MessageKey)}</div>
        {visual.provider && <div className="muted small">{visual.provider}</div>}
        {props.cost !== undefined && (
          <div className="small">
            {props.cost > 0 ? t("board.estimate", { cost: `${props.currency}${props.cost.toFixed(2)}` }) : t("board.free")}
          </div>
        )}
        {spent > 0 && <div className="muted small">{t("board.spent", { cost: `${props.currency}${spent.toFixed(2)}` })}</div>}
        {locked && <div className="muted small">{t("board.locked")}</div>}
      </td>
      <td className="col-check">
        {props.risks.length === 0 ? (
          <span className="muted small">{kind === "prompt" ? t("board.checkOk") : "—"}</span>
        ) : (
          <ul className="board-risks">
            {props.risks.map((risk, i) => {
              const { what, fix } = riskText(t, risk);
              return (
                <li key={i} title={fix}>
                  {what}
                </li>
              );
            })}
          </ul>
        )}
      </td>
    </tr>
  );
}

/** 当前选用的画面；还没有时用最新的关键帧，再没有就显示纯色或空框。 */
function Thumb({ project, shot }: { project: Project; shot: Shot }) {
  const visual = shot.visual;
  const keyframe = [...(visual.takes ?? [])].reverse().find((item) => item.kind === "keyframe" && project.assets[item.asset_id]);
  const assetId = visual.selected && project.assets[visual.selected] ? visual.selected : keyframe?.asset_id;
  const asset = assetId ? project.assets[assetId] : undefined;
  const aspect = project.canvas.aspect.replace(":", " / ");
  if (visual.mode === "color" && !asset) return <div className="board-thumb" style={{ aspectRatio: aspect, background: visual.color }} />;
  if (!asset || !assetId) return <div className="board-thumb empty" style={{ aspectRatio: aspect }} />;
  if (asset.kind === "image") return <img className="board-thumb" style={{ aspectRatio: aspect }} src={assetUrl(project.id, assetId)} alt="" loading="lazy" />;
  if (asset.kind === "video")
    return <video className="board-thumb" style={{ aspectRatio: aspect }} src={`${assetUrl(project.id, assetId)}#t=0.5`} muted playsInline preload="metadata" />;
  return <div className="board-thumb empty" style={{ aspectRatio: aspect }} />;
}
