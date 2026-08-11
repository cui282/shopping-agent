import { ClipboardCheck } from "lucide-react";
import type { ShoppingPlan } from "../types/api";
import styles from "./RequirementSummary.module.css";

const wholeYuan = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  maximumFractionDigits: 0,
});

interface RequirementSummaryProps {
  plan: ShoppingPlan;
}

export default function RequirementSummary({ plan }: RequirementSummaryProps) {
  const facts = [
    { label: "研究方式", value: plan.mode === "exact_offer_comparison" ? "核对同款报价" : "比较不同商品" },
    { label: "商品", value: plan.category },
    ...(plan.budget_cny == null
      ? []
      : [{ label: "预算", value: `预算不超过 ${wholeYuan.format(plan.budget_cny)}` }]),
    ...(plan.destination ? [{ label: "送达", value: plan.destination }] : []),
  ];
  const preferences = [
    ...plan.style_preferences,
    ...plan.material_preferences,
    ...plan.soft_preferences,
  ];

  return (
    <section className={styles.summary} aria-label="已理解的需求">
      <div className={styles.heading}>
        <ClipboardCheck size={17} aria-hidden="true" />
        <div>
          <strong>已理解的需求</strong>
          <span>本次研究将按这些条件筛选与排序</span>
        </div>
      </div>
      <dl className={styles.facts}>
        {facts.map((fact) => (
          <div key={`${fact.label}-${fact.value}`}>
            <dt>{fact.label}</dt>
            <dd>{fact.value}</dd>
          </div>
        ))}
      </dl>
      {(preferences.length > 0 || plan.hard_constraints.length > 0) && (
        <div className={styles.tags} aria-label="偏好与必要条件">
          {preferences.map((preference) => (
            <span key={`preference-${preference}`} data-kind="preference">
              {preference}
            </span>
          ))}
          {plan.hard_constraints.map((constraint) => (
            <span key={constraint.id} data-kind="required">
              {constraint.label}
            </span>
          ))}
        </div>
      )}
      {plan.working_assumptions.length > 0 && (
        <p className={styles.assumptions}>
          暂按：{plan.working_assumptions.map((assumption) => assumption.value).join("、")}
        </p>
      )}
    </section>
  );
}
