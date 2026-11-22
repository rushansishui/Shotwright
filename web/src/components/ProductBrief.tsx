import { useState } from "react";
import { useI18n } from "../i18n";

const STORAGE_KEY = "shotwright.productBrief.v1";
const fields = ["name", "specs", "benefits", "price", "contact"] as const;
type Product = Record<(typeof fields)[number], string>;
const empty: Product = { name: "", specs: "", benefits: "", price: "", contact: "" };

function load(): Product {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    return Object.fromEntries(fields.map((key) => [key, typeof value?.[key] === "string" ? value[key].slice(0, 400) : ""])) as Product;
  } catch {
    return { ...empty };
  }
}

export function ProductBrief({ disabled, onApply }: { disabled: boolean; onApply: (brief: string) => boolean }) {
  const { lang } = useI18n();
  const zh = lang === "zh";
  const [product, setProduct] = useState(load);
  const [template, setTemplate] = useState("launch");
  const [status, setStatus] = useState("");
  const labels = zh
    ? { name: "商品名称与型号", specs: "准确参数", benefits: "卖点与使用场景", price: "价格与优惠（选填）", contact: "联系方式（选填）" }
    : { name: "Product and model", specs: "Verified specifications", benefits: "Benefits and use cases", price: "Price and offer (optional)", contact: "Contact (optional)" };
  const structures: Record<string, string> = zh ? {
    launch: "新品介绍：商品亮相、核心卖点、参数、行动引导。",
    demo: "功能演示：使用需求、实拍操作、功能说明、行动引导。",
    inquiry: "外贸询盘：商品与用途、已提供的规格、联系询价。",
  } : {
    launch: "Product launch: reveal, benefits, specifications, call to action.",
    demo: "Feature demo: use case, real footage, feature explanation, call to action.",
    inquiry: "Trade inquiry: product and uses, supplied specifications, contact for a quote.",
  };

  function apply() {
    if (disabled || !product.name.trim()) return;
    const facts = fields.filter((key) => product[key].trim()).map((key) => `${labels[key]} — ${product[key].trim()}`);
    const rules = zh
      ? "优先使用上传的真实商品照片和实拍。保持外形、接口、Logo、型号准确。参数和价格通过文字图层呈现。仅使用上述事实，缺失信息留待确认，禁止虚构认证、性能、库存或优惠。AI 场景需人工确认商品外观。"
      : "Prefer uploaded product photos and real footage. Preserve shape, ports, logo and model. Present specifications and prices as text overlays. Use only supplied facts. Flag missing information for confirmation. Do not invent certifications, performance, stock or offers. Require human review of product appearance in AI scenes.";
    const applied = onApply([...facts, structures[template], rules].join("\n"));
    setStatus(applied
      ? (zh ? "已填入视频诉求，请确认后继续。" : "Brief filled. Review it before continuing.")
      : (zh ? "已取消填入，原视频诉求已保留。" : "Cancelled. Your existing brief was kept."));
  }

  return <details>
    <summary>{zh ? "用商品资料创建广告" : "Create an ad from product details"}</summary>
    <fieldset disabled={disabled}>
      {fields.map((key) => <label key={key}>{labels[key]}<input maxLength={400} value={product[key]} onChange={(event) => {
        setProduct({ ...product, [key]: event.target.value });
        setStatus("");
      }} /></label>)}
      <label>{zh ? "广告用途" : "Ad purpose"}<select value={template} onChange={(event) => {
        setTemplate(event.target.value);
        setStatus("");
      }}>
        <option value="launch">{zh ? "新品介绍" : "Product launch"}</option>
        <option value="demo">{zh ? "功能演示" : "Feature demo"}</option>
        <option value="inquiry">{zh ? "外贸询盘" : "Trade inquiry"}</option>
      </select></label>
      <button type="button" disabled={!product.name.trim()} onClick={apply}>{zh ? "填入视频诉求" : "Fill video brief"}</button>
      <button type="button" onClick={() => {
        try {
          localStorage.setItem(STORAGE_KEY, JSON.stringify(product));
          setStatus(zh ? "商品资料已保存到此浏览器，可在下次创建时复用。" : "Product details saved in this browser for reuse.");
        } catch { setStatus(zh ? "保存失败，请检查浏览器存储空间。" : "Could not save. Check browser storage."); }
      }}>{zh ? "保存资料供下次使用" : "Save for reuse"}</button>
      <p className="muted small" role="status">{status}</p>
    </fieldset>
  </details>;
}
