import { factLabels, type ProductFact } from "./types";

export function emptyFacts(): ProductFact[] {
  return Object.keys(factLabels).map(key => ({ key, value: "", unit: key === "pack_quantity" ? "pieces" : "", confirmed: false }));
}

export function editableFacts(existing: ProductFact[], suggestions: ProductFact[] = []): ProductFact[] {
  const candidates = suggestions.filter(fact => !existing.some(item => item.key === fact.key && item.value.trim()));
  const merged = [...existing.filter(fact => fact.value.trim() || !candidates.some(item => item.key === fact.key)), ...candidates.map(({ key, value, unit }) => ({ key, value, unit, confirmed: false }))];
  return [...merged.map(fact => ({ ...fact })), ...emptyFacts().filter(fact => !merged.some(item => item.key === fact.key))];
}

export function FactEditor({ facts, onChange, disabled = false }: { facts: ProductFact[]; onChange: (facts: ProductFact[]) => void; disabled?: boolean }) {
  function update(index: number, patch: Partial<ProductFact>) {
    onChange(facts.map((fact, current) => current === index ? { ...fact, ...patch } : fact));
  }
  return <div className="fact-editor">{facts.map((fact, index) => <div className="fact-edit-row" key={`${fact.key}-${index}`}><div className="fact-value-field"><label htmlFor={`fact-${fact.key}-${index}`}>{factLabels[fact.key] ?? fact.key}<span>可稍后补充</span></label><input id={`fact-${fact.key}-${index}`} value={fact.value} maxLength={2000} disabled={disabled} onChange={event => update(index, { value: event.target.value, confirmed: false })} placeholder={{ material: "例如：硅胶", dimensions: "例如：4 × 3 × 2", pack_quantity: "例如：3", use_cases: "例如：桌面线缆整理", dimension_scope: "单件 / 整包，请明确", cable_diameter: "例如：6", color: "例如：黑色" }[fact.key] ?? "填写商品事实"} /></div><div className="fact-unit-field"><label htmlFor={`unit-${fact.key}-${index}`}>单位</label><input id={`unit-${fact.key}-${index}`} value={fact.unit ?? ""} maxLength={100} disabled={disabled} onChange={event => update(index, { unit: event.target.value, confirmed: false })} placeholder="如适用" /></div><label className="checkbox-field fact-confirm"><input type="checkbox" checked={fact.confirmed} disabled={disabled || !fact.value.trim()} onChange={event => update(index, { confirmed: event.target.checked })} /><span>我已核实</span></label><button className="fact-remove text-button" type="button" disabled={disabled} aria-label={`移除${factLabels[fact.key] ?? fact.key}第${index + 1}项`} onClick={() => onChange(facts.filter((_, current) => current !== index))}>移除</button></div>)}{Object.keys(factLabels).some(key => !facts.some(fact => fact.key === key)) && <button className="text-button add-facts" type="button" disabled={disabled} onClick={() => onChange(editableFacts(facts))}>添加标准资料项 ＋</button>}</div>;
}

export function normalizedFacts(facts: ProductFact[]): ProductFact[] {
  return facts.filter(fact => fact.value.trim()).map(fact => ({ ...fact, value: fact.value.trim(), unit: fact.unit?.trim() || null }));
}
