import { useState } from "react";
import { useI18n } from "../i18n";
import { MAX_SUBJECT_BYTES, utf8Bytes } from "../briefs";

const STORAGE_KEY = "shotwright.productBrief.v1";
const fields = ["name", "specs", "benefits", "price", "contact"] as const;
type Product = Record<(typeof fields)[number], string>;
const templates = ["launch", "demo", "inquiry"] as const;
type Template = (typeof templates)[number];
type ProductForm = Product & { template: Template };
const empty: Product = { name: "", specs: "", benefits: "", price: "", contact: "" };

function load(): ProductForm {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    return {
      ...Object.fromEntries(fields.map((key) => [key, typeof value?.[key] === "string" ? value[key].slice(0, 400) : ""])) as Product,
      template: templates.includes(value?.template) ? value.template : "launch",
    };
  } catch {
    return { ...empty, template: "launch" };
  }
}

export function ProductBrief({ disabled, onApply }: { disabled: boolean; onApply: (brief: string) => boolean }) {
  const { t } = useI18n();
  const [product, setProduct] = useState(load);
  const [status, setStatus] = useState("");

  const facts = fields.filter((key) => product[key].trim()).map((key) => `${t(`productBrief.field.${key}`)} — ${product[key].trim()}`);
  const rules = t("productBrief.rules");
  const brief = [...facts, t(`productBrief.structure.${product.template}`), rules].join("\n");
  const briefBytes = utf8Bytes(brief);
  const tooLong = briefBytes > MAX_SUBJECT_BYTES;

  function apply() {
    if (disabled || !product.name.trim() || tooLong) return;
    const applied = onApply(brief);
    setStatus(applied
      ? t("productBrief.applied")
      : t("productBrief.cancelled"));
  }

  return <details className="product-brief">
    <summary>{t("productBrief.title")}</summary>
    <fieldset disabled={disabled}>
      {fields.map((key) => {
        const props = {
          className: "input",
          maxLength: 400,
          value: product[key],
          onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
            setProduct({ ...product, [key]: event.target.value });
            setStatus("");
          },
        };
        return <label className="field" key={key}>{t(`productBrief.field.${key}`)}
          {key === "specs" || key === "benefits"
            ? <textarea {...props} rows={3} />
            : <input {...props} />}
        </label>;
      })}
      <label>{t("productBrief.purpose")}<select className="input" value={product.template} onChange={(event) => {
        setProduct({ ...product, template: event.target.value as Template });
        setStatus("");
      }}>
        <option value="launch">{t("productBrief.purpose.launch")}</option>
        <option value="demo">{t("productBrief.purpose.demo")}</option>
        <option value="inquiry">{t("productBrief.purpose.inquiry")}</option>
      </select></label>
      <p className={`muted small ${tooLong ? "bad" : ""}`} role="status">
        {t("productBrief.briefLabel")} {briefBytes} / {MAX_SUBJECT_BYTES} {t("productBrief.bytes")}
        {tooLong && t("productBrief.tooLong")}
      </p>
      <div className="row">
        <button className="btn" type="button" disabled={!product.name.trim() || tooLong} onClick={apply}>{t("productBrief.apply")}</button>
        <button className="btn ghost" type="button" onClick={() => {
          try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(product));
            setStatus(t("productBrief.saved"));
          } catch { setStatus(t("productBrief.saveFailed")); }
        }}>{t("productBrief.save")}</button>
      </div>
      <p className="muted small" role="status">{status}</p>
    </fieldset>
  </details>;
}
