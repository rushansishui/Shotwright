import type { Translate } from "./i18n";
import type { MessageKey } from "./messages";
import type { PrecheckRisk } from "./types";

/** 生成前检查的一条风险：（问题，建议），按界面语言显示。 */
export function riskText(t: Translate, risk: Pick<PrecheckRisk, "code" | "params">): { what: string; fix: string } {
  const params = risk.params ?? {};
  return {
    what: t(`risk.${risk.code}` as MessageKey, params),
    fix: t(`risk.${risk.code}.fix` as MessageKey, params),
  };
}

/** 生成前的确认框里附上检查结果；没有风险时返回空字符串。 */
export function riskConfirmText(t: Translate, risks: PrecheckRisk[]): string {
  if (!risks.length) return "";
  const lines = risks.map((risk) => {
    const { what, fix } = riskText(t, risk);
    return `· ${what}\n  ${t("risk.fixLabel")}${fix}`;
  });
  return `\n\n${t("risk.confirm")}\n${lines.join("\n")}\n\n${t("risk.confirmTail")}`;
}
