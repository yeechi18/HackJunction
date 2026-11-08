"use client";

import { IntakeAnalysisPanel } from "@/features/orders/intake-analysis";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { ApiError, apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import { formatTime, type InternalOrder, type DecisionCommand } from "@/features/orders/types";
import { pollIntervalFor } from "@/features/orders/workflow-progress";
import type { components } from "@/lib/generated/api";
import styles from "./production-panel.module.css";

type Schema = components["schemas"];
type Production = Schema["ProductionView"];
type Evidence = Schema["EvidenceView"];
type Version = Schema["VersionView"];
type Research = Schema["ResearchView"];
type Report = Schema["ReportView"];
type Asset = Schema["AssetView"];
type Publication = Schema["PublicationView"];
type Props = { order: InternalOrder; csrfToken: string; refresh: () => void };

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function text(value: unknown): string { return typeof value === "string" ? value : ""; }
function strings(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []; }
function records(value: unknown): Record<string, unknown>[] { return Array.isArray(value) ? value.filter(item => item !== null && typeof item === "object" && !Array.isArray(item)).map(record) : []; }
function priceText(value: unknown): string { return typeof value === "string" || typeof value === "number" ? String(value) : "—"; }

function EvidenceList({ items }: { items: Evidence[] }) {
  return <div className={styles.evidenceList}>{items.length ? items.map(item => <details key={item.id}><summary><strong>{item.source}</strong><span>{item.is_demo && <i className="demo-tag">演示</i>}{({ success: "成功", partial: "部分信息", failed: "失败" } as Record<string, string>)[item.status] ?? item.status}</span></summary><p>{item.content}</p><div className={styles.metadata}><span>{formatTime(item.observed_at)}</span><span>{item.scope}</span>{item.data.price != null && <span>观察价 {priceText(item.data.price)} {text(item.data.currency)}</span>}</div>{item.source_url && <a href={item.source_url} target="_blank" rel="noreferrer">复核来源 ↗</a>}<code>{item.id}</code></details>) : <p className={styles.empty}>尚无外部竞品证据。研究 Agent 会分析已确认商品资料，价格依据不足时不提供定价建议。</p>}</div>;
}

function ResearchCard({ research }: { research: Research }) {
  const result = record(research.result);
  const range = record(result.suggested_listing_price_range);
  const questions = records(result.buyer_questions);
  return <article className={styles.researchCard}><div className={styles.subheading}><h3>研究包 · 资料版本 {research.input_revision}</h3><time>{formatTime(research.created_at)}</time></div><div className={styles.pricing}><span>商品售价参考区间</span><strong>{Object.keys(range).length ? `${priceText(range.low)} – ${priceText(range.high)} ${text(range.currency)}` : "暂无定价建议"}</strong>{range.is_demo === true && <i className="demo-tag">演示观察</i>}<p>{text(result.pricing_notes) || "未记录价格依据。"}</p>{strings(range.evidence_ids).length > 0 && <p>依据：{strings(range.evidence_ids).map(id => id.slice(0, 8)).join("、")}</p>}</div>{records(result.selling_point_plan).length > 0 && <details className={styles.disclosure}><summary>商家卖点需求与采用策略 <span>＋</span></summary>{records(result.selling_point_plan).map((item, index) => <div className={styles.question} key={index}><strong>{text(item.request)}</strong><p>{({ use_confirmed_fact: "采用已确认事实", style_only: "表达与排版偏好", needs_confirmation: "需补充依据" } as Record<string, string>)[text(item.strategy)] ?? text(item.strategy)} · {text(item.explanation)}</p><small>事实依据：{strings(item.fact_ids).join("、") || "无"}</small></div>)}</details>}{strings(result.market_notes).length > 0 && <ul className={styles.notes}>{strings(result.market_notes).map((item, index) => <li key={index}>{item}</li>)}</ul>}{questions.length > 0 && <details className={styles.disclosure}><summary>买家问题与证据 <span>＋</span></summary>{questions.map((item, index) => <div className={styles.question} key={index}><strong>{text(item.question)}</strong><p>{item.applicable === false ? "本商品不适用" : "适用"} · 证据：{strings(item.evidence_ids).join("、") || "尚无"}</p>{strings(item.missing_facts).length > 0 && <p>待确认：{strings(item.missing_facts).join("、")}</p>}</div>)}</details>}</article>;
}

function DraftContent({ version, assets }: { version: Version; assets: Asset[] }) {
  const draft = record(version.draft);
  const images = assets.filter(asset => asset.version_id === version.id && version.assets.includes(asset.id));
  return <div className={styles.draftContent}><div className={styles.draftField}><span>PRODUCT TITLE</span><h4>{text(draft.title) || "尚无标题"}</h4></div><div className={styles.draftField}><span>FIVE BULLET POINTS</span><ol>{strings(draft.bullets).map((item, index) => <li key={index}>{item}</li>)}</ol></div><div className={styles.draftField}><span>DESCRIPTION</span><p>{text(draft.description) || "尚无详情"}</p></div><details className={styles.disclosure}><summary>买家 FAQ <span>＋</span></summary>{records(draft.faqs).map((item, index) => <div className={styles.question} key={index}><strong>{text(item.question)}</strong><p>{text(item.answer)}</p></div>)}</details><details className={styles.disclosure}><summary>声明与事实依据 <span>＋</span></summary>{records(draft.claims).length ? records(draft.claims).map((item, index) => <div className={styles.claim} key={index}><code>{text(item.field_path)}</code><p>{text(item.text)}</p><small>{strings(item.fact_ids).join("、")}</small></div>) : <p className={styles.empty}>暂无声明依据记录。</p>}</details><div className={styles.versionImages}><h4>本版图片 · v{version.version_number}</h4>{images.length ? <div>{images.map(image => <a href={image.url} target="_blank" rel="noreferrer" key={image.id}><Image src={image.url} alt={`版本${version.version_number}商品图片`} width={image.width} height={image.height} unoptimized /><span>{image.width} × {image.height}px · {image.is_demo ? "演示素材" : "商品素材"}</span></a>)}</div> : <p className={styles.note}>本版暂无可查看的图片。</p>}</div></div>;
}

function ReviewCard({ report }: { report: Report }) {
  const result = record(report.result);
  const issues = records(result.issues);
  return <article className={styles.reviewCard}><div className={styles.subheading}><strong>{report.scope === "text_and_images" ? "图文审校" : "文案审校"}</strong><span>文案{report.text_passed ? "通过" : "未通过"} / 成品{report.full_passed ? "通过" : "未通过"}</span></div>{!report.full_passed && report.text_passed && <p className={styles.warning}>文案已通过，完整成品仍未通过。</p>}{issues.length ? <ul className={styles.issues}>{issues.map((issue, index) => <li key={index}><div><code>{text(issue.field_path)}</code><span>{issue.severity === "blocking" ? "阻断项" : "提示"}</span></div><p>{text(issue.reason)}</p><small>建议：{text(issue.suggested_fix)}</small></li>)}</ul> : <p className={styles.note}>本次审校未记录问题。</p>}<time className={styles.note}>{formatTime(report.created_at)}</time></article>;
}

function VersionPanel({ production, selected, onSelect, assets }: { production: Production; selected: string; onSelect: (id: string) => void; assets: Asset[] }) {
  const [compare, setCompare] = useState("");
  const ordered = [...production.versions].sort((left, right) => right.version_number - left.version_number);
  const current = ordered.find(item => item.id === selected) ?? ordered[0];
  const previous = ordered.find(item => item.id === compare);
  if (!current) return <p className={styles.empty}>尚未生成文案版本。已有研究或运行记录会显示在对应面板。</p>;
  const reports = production.reviews.filter(item => item.version_id === current.id);
  return <><div className={styles.versionSelectors}><label>查看版本<select value={current.id} onChange={event => onSelect(event.target.value)}>{ordered.map(item => <option value={item.id} key={item.id}>v{item.version_number} · {item.full_passed ? "成品通过" : item.text_passed ? "文案通过" : "待修改"}</option>)}</select></label><label>对比版本<select value={compare} onChange={event => setCompare(event.target.value)}><option value="">不对比</option>{ordered.filter(item => item.id !== current.id).map(item => <option value={item.id} key={item.id}>v{item.version_number}</option>)}</select></label></div><div className={styles.versionState}><strong>v{current.version_number}</strong><span>文案：{current.text_passed ? "通过" : "未通过"}</span><span>完整成品：{current.full_passed ? "通过" : "未通过"}</span><span>状态：{current.status}</span></div><div className={`${styles.versionColumns} ${previous && previous.id !== current.id ? styles.comparing : ""}`}><article><h3>当前查看 v{current.version_number}</h3><DraftContent version={current} assets={assets} /></article>{previous && previous.id !== current.id && <article><h3>对比 v{previous.version_number}</h3><DraftContent version={previous} assets={assets} /></article>}</div>{reports.map(report => <ReviewCard report={report} key={report.id} />)}</>;
}

function ReleaseGate({ order, version, latest, csrfToken, refresh, showLatest }: Props & { version: Version; latest: Version; showLatest: () => void }) {
  const [reason, setReason] = useState("");
  const [reasonVersion, setReasonVersion] = useState(version.id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [submittedId, setSubmittedId] = useState("");
  const pending = useRef<{ body: string; key: string } | null>(null);
  const command = order.commands.find(item => item.id === submittedId);
  const submittedPending = Boolean(submittedId && (!command || ["queued", "running"].includes(command.status)));
  const currentSnapshot = order.input_snapshots.find(item => item.revision === order.revision);
  const currentInputs = currentSnapshot?.id === version.input_snapshot_id;
  const alreadyDecided = order.decisions.some(item => item.version_id === version.id && item.status === "applied" && ["release", "veto"].includes(item.action));
  const viewingLatest = version.id === latest.id;
  const candidate = viewingLatest && currentInputs && !alreadyDecided && !["vetoed", "delivered"].includes(version.status);
  const canRelease = candidate && order.allowed_actions.includes("release") && version.status === "review_passed" && version.full_passed && version.text_passed;
  const canVeto = candidate && order.allowed_actions.includes("veto");
  const staleReason = Boolean(reason.trim() && reasonVersion !== version.id);
  async function decide(action: "release" | "veto") {
    if (busy || submittedPending || !order.allowed_actions.includes(action)) return;
    if (action === "veto" && (!reason.trim() || staleReason)) { setError("请填写并核对当前版本的否决意见。"); return; }
    if (action === "release" && !canRelease) return;
    if (action === "veto" && !canVeto) return;
    const body = JSON.stringify({ action, version_id: version.id, expected_revision: order.revision, ...(action === "veto" ? { reason: reason.trim() } : {}) });
    if (pending.current?.body !== body) pending.current = { body, key: crypto.randomUUID() };
    setBusy(true); setError("");
    try {
      const result = await apiFetch<DecisionCommand>(`/internal/orders/${order.id}/decisions`, { method: "POST", body, headers: csrfHeaders(csrfToken), idempotencyKey: pending.current.key });
      setSubmittedId(result.command_id); refresh();
    } catch (caught) { setError(errorMessage(caught)); if (caught instanceof ApiError && caught.status === 409) refresh(); }
    finally { setBusy(false); }
  }
  return <div className={styles.releaseGate}><div className={styles.subheading}><h3>{viewingLatest ? "当前最新候选" : "当前查看历史版本"} · v{version.version_number}</h3><span>绑定本版文案与图片</span></div>{!viewingLatest && <div className={styles.warning}>当前查看 v{version.version_number}，最新候选为 v{latest.version_number}。请核对最新版正文与图片后再决定。<button className="text-button" type="button" onClick={showLatest}>切回最新版 v{latest.version_number}</button></div>}<p className={styles.note}>文案审校：{version.text_passed ? "通过" : "未通过"}；完整图文审校：{version.full_passed ? "通过" : "未通过"}。</p>{!version.full_passed && <p className={styles.warning}>完整图文审校尚未通过，当前不能放行交付。</p>}<div className="field"><label htmlFor="veto-reason">否决原因</label><textarea id="veto-reason" rows={3} value={reason} maxLength={2000} disabled={busy || submittedPending} onChange={event => { if (!reason) setReasonVersion(version.id); setReason(event.target.value); }} placeholder="否决时必填，说明这一版需要修改的具体问题。" /></div>{staleReason && <p className={styles.warning}>候选版本已更新，意见草稿仍保留。<button className="text-button" onClick={() => setReasonVersion(version.id)} type="button">已核对，将意见用于 v{version.version_number}</button></p>}<div className={styles.gateActions}>{order.allowed_actions.includes("release") && <button type="button" className="button button-dark button-small" disabled={busy || submittedPending || !canRelease} onClick={() => decide("release")}>本版放行并交付</button>}{order.allowed_actions.includes("veto") && <button type="button" className="button button-outline button-small danger-text" disabled={busy || submittedPending || !canVeto || !reason.trim() || staleReason} onClick={() => decide("veto")}>否决本版</button>}</div>{submittedId && <p className={styles.success} role="status">{!command || ["queued", "running"].includes(command.status) ? "决定已入队，等待实际处理；交付尚未确认。" : command.status === "applied" ? "本次决定已生效，请查看订单动态与交付记录。" : `本次命令状态：${command.status}，请核对最新记录。`}</p>}{!canRelease && !canVeto && !submittedId && <p className={styles.note}>当前没有可执行的放行或否决动作。</p>}{error && <p className={styles.error} role="alert">{error}</p>}</div>;
}

export function ProductionPanel({ order, csrfToken, refresh }: Props) {
  const [production, setProduction] = useState<Production | null>(null);
  const [publication, setPublication] = useState<Publication | null>(null);
  const [selectedVersion, setSelectedVersion] = useState("");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const delay = pollIntervalFor(order);
  const latestVersion = production?.versions.reduce<Version | null>((latest, item) => !latest || item.version_number > latest.version_number ? item : latest, null);
  const viewedVersion = production?.versions.find(item => item.id === selectedVersion) ?? latestVersion;
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    async function load() {
      let denied = false;
      try {
        const [artifacts, observations, images, publishing] = await Promise.all([
          apiFetch<Production>(`/internal/orders/${order.id}/production`, { signal: controller.signal }),
          apiFetch<{ items: Evidence[] }>(`/internal/orders/${order.id}/evidence`, { signal: controller.signal }),
          apiFetch<{ items: Asset[] }>(`/orders/${order.id}/assets`, { signal: controller.signal }),
          apiFetch<Publication>(`/orders/${order.id}/publication`, { signal: controller.signal }),
        ]);
        if (active) { setProduction(artifacts); setPublication(publishing); setEvidence(observations.items); setAssets(images.items); setError(""); }
      } catch (caught) { if (active) { setError(errorMessage(caught)); denied = caught instanceof ApiError && [401, 403].includes(caught.status ?? 0); if (denied) { setProduction(null); setPublication(null); setEvidence([]); setAssets([]); } } }
      finally { if (active && !denied) timer = setTimeout(() => void load(), delay); }
    }
    void load();
    return () => { active = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [order.id, delay, revision]);

  return <section className={`surface workspace-panel ${styles.panel}`} aria-labelledby="production-title"><div className="panel-heading"><h2 id="production-title">Agent 研究与内容产物</h2><button className="text-button" type="button" onClick={() => setRevision(value => value + 1)}>刷新 ↻</button></div>{error && <p className={styles.error} role="alert">{error}</p>}
    <IntakeAnalysisPanel order={order} /><p className={styles.note}>研究、生成和审校由对应 Agent 自动完成。此处查看产物与依据；负责人可以放行或否决，修改由 Agent 执行。</p><details className={styles.disclosure}><summary>研究依据（只读） <span>{evidence.length} 条 ＋</span></summary><EvidenceList items={evidence} /></details>
    <div className={styles.sectionHeading}><h3>研究包</h3><span>{production?.research.length ?? 0} 份</span></div>{production?.research.length ? production.research.map(item => <ResearchCard research={item} key={item.id} />) : <p className={styles.empty}>负责人批准接单后，研究Agent会自动生成研究包；初审与文案方向已在上方展示。</p>}
    <div className={styles.sectionHeading}><h3>版本、声明与审校</h3><span>{production?.versions.length ?? 0} 个版本</span></div>{production ? <VersionPanel production={production} selected={selectedVersion} onSelect={setSelectedVersion} assets={assets} /> : <p className={styles.empty}>正在读取内部产物…</p>}
    {latestVersion && viewedVersion && <ReleaseGate order={order} version={viewedVersion} latest={latestVersion} showLatest={() => setSelectedVersion("")} csrfToken={csrfToken} refresh={() => { setRevision(value => value + 1); refresh(); }} />}
    {publication && <div className={styles.run}>
      <div><strong>定价与上架</strong><span>{publication.status === "SIMULATED" ? "模拟上架完成" : publication.attempt ? publication.status : "等待上架"}</span></div>
      {publication.confirmation && <p>卖家确认 {publication.confirmation.currency} {publication.confirmation.listing_price} · SKU {publication.confirmation.seller_sku}</p>}
      {publication.notice && <p>{publication.notice}</p>}
      {publication.listing_url && <a className="text-link" href={publication.listing_url}>查看商品页 ↗</a>}
    </div>}
    <details className={styles.disclosure}><summary>生产运行记录 <span>{production?.runs.length ?? 0} 次 ＋</span></summary>{production?.runs.map(run => <div className={styles.run} key={run.id}><div><strong>{({ research: "研究", generation: "生成", review: "审校", images: "图片" } as Record<string, string>)[run.role] ?? run.role}</strong><span>{run.status} · 尝试 {run.attempts} 次</span></div><p>{run.model ?? "模型待记录"} · 资料版本 {run.input_revision} · Token {priceText(run.usage.total_tokens)} · {run.elapsed_seconds == null ? "耗时未记录" : `${run.elapsed_seconds.toFixed(2)}s`}</p>{run.error_summary && <p className={styles.error}>{run.error_summary}</p>}</div>)}</details>
  </section>;
}
