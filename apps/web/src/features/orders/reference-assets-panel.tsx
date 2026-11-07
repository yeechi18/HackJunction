"use client";

import Image from "next/image";
import { useEffect, useId, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { ApiError, apiFetch, csrfHeaders, errorMessage } from "@/lib/api";
import styles from "./reference-assets-panel.module.css";

import type { components } from "@/lib/generated/api";
type AssetView = components["schemas"]["AssetView"];

type Props = { orderId: string; csrfToken: string; internal?: boolean };
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
const kindLabels: Record<string, string> = {
  reference: "商品参考图",
  main_candidate: "主图候选",
  infographic: "商品信息附图",
};

function formatBytes(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(2)} MiB` : `${Math.max(1, Math.ceil(bytes / 1024))} KiB`;
}

function validateFile(file: File): string | null {
  if (!file.size) return "这张图片是空文件，请重新选择。";
  if (file.size > MAX_IMAGE_BYTES) return "单张图片不能超过 10 MiB，请缩小图片后再上传。";
  const knownType = IMAGE_TYPES.has(file.type);
  const unknownTypeWithImageExtension = !file.type && /\.(png|jpe?g|webp)$/i.test(file.name);
  return knownType || unknownTypeWithImageExtension ? null : "请选择 PNG、JPEG 或 WebP 格式的图片。";
}

function uploadErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 413) return "上传图片超过大小限制，请选择不超过 10 MiB 的图片。";
    if (error.status === 415) return "图片格式不支持，请选择 PNG、JPEG 或 WebP 图片。";
    if (error.status === 422) return "图片未通过校验，请检查图片是否完整，并重新选择后上传。";
  }
  return errorMessage(error);
}

function sourceLabel(asset: AssetView): string {
  if (asset.method === "upload_normalized_png") return "来源：上传的商品参考图";
  if (asset.method === "reference_template_v1") return "来源：以参考图制作的排版素材";
  return asset.kind === "reference" ? "来源：商品参考素材" : "来源：商品图片制作";
}

function AssetCard({ asset, internal }: { asset: AssetView; internal: boolean }) {
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const label = kindLabels[asset.kind] ?? "商品图片";
  const source = retry ? `${asset.url}${asset.url.includes("?") ? "&" : "?"}preview=${retry}` : asset.url;

  return <article className={styles.assetCard}>
    <div className={styles.preview}>
      {failed ? <div className={styles.imageFailure}><span aria-hidden="true">↗</span><p>图片暂时无法显示</p><button type="button" onClick={() => { setFailed(false); setRetry(current => current + 1); }}>重试读取</button></div>
        : <Image src={source} alt={`${label}，${asset.width} × ${asset.height} 像素`} fill unoptimized sizes="(max-width: 600px) 100vw, (max-width: 900px) 50vw, 33vw" onError={() => setFailed(true)} />}
      {asset.is_demo && <span className={styles.demoBadge}>演示素材</span>}
    </div>
    <div className={styles.assetBody}>
      <div className={styles.assetHeading}><h3>{label}</h3>{asset.kind !== "reference" && <span className={styles.deliveryState}>{asset.delivered_at ? "已交付" : "未交付"}</span>}</div>
      <p className={styles.dimensions}>{asset.width} × {asset.height} px <span>·</span> {formatBytes(asset.byte_size)}</p>
      <p className={styles.source}>{sourceLabel(asset)}</p>
      <div className={styles.assetFooter}><span>{asset.media_type.replace("image/", "").toUpperCase()}{internal && asset.version_id ? ` · 版本 ${asset.version_id.slice(0, 8)}` : ""}</span><a href={asset.url} target="_blank" rel="noreferrer">查看图片 ↗</a></div>
    </div>
  </article>;
}

function AssetsWorkspace({ orderId, csrfToken, internal = false }: Props) {
  const panelId = useId();
  const [assets, setAssets] = useState<AssetView[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [revision, setRevision] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [uploadNotice, setUploadNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const assetPath = `/orders/${encodeURIComponent(orderId)}/assets`;

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    async function load() {
      let blocked = false;
      try {
        const response = await apiFetch<{ items: AssetView[] }>(assetPath, { signal: controller.signal });
        if (active) { setAssets(internal ? response.items : response.items.filter(asset => asset.kind === "reference" || Boolean(asset.delivered_at))); setLoadError(""); }
      } catch (error) {
        if (active) {
          setLoadError(errorMessage(error));
          blocked = error instanceof ApiError && [401, 403].includes(error.status ?? 0);
          if (blocked) setAssets(null);
        }
      } finally {
        if (active && !blocked) timer = setTimeout(() => void load(), 5000);
      }
    }
    void load();
    return () => { active = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [assetPath, revision, internal]);

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0] ?? null;
    setFile(selected);
    setUploadNotice("");
    setUploadError(selected ? validateFile(selected) ?? "" : "");
  }

  function clearFile() {
    setFile(null); setUploadError(""); setUploadNotice("");
    if (fileInput.current) fileInput.current.value = "";
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !file) return;
    const invalid = validateFile(file);
    if (invalid) { setUploadError(invalid); return; }
    const body = new FormData();
    body.append("file", file);
    setBusy(true); setUploadError(""); setUploadNotice("");
    try {
      await apiFetch<AssetView>(assetPath, { method: "POST", body, headers: csrfHeaders(csrfToken), timeoutMs: 30000 });
      setUploadNotice("参考图已保存，可在下方查看。");
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      setRevision(current => current + 1);
    } catch (error) { setUploadError(uploadErrorMessage(error)); }
    finally { setBusy(false); }
  }

  return <section className={`surface workspace-panel ${styles.panel}`} aria-labelledby={`${panelId}-title`}>
    <div className="panel-heading"><h2 id={`${panelId}-title`}>{internal ? "商品图片与参考素材" : "商品参考图"}</h2><span>{assets ? `${assets.length} 张可见图片` : "实际图片文件"}</span></div>
    <p className={styles.intro}>上传能够准确呈现商品的现有图片，作为后续图片制作的参考。</p>
    <form className={styles.uploadForm} onSubmit={upload}>
      <label className={styles.fileLabel} htmlFor={`${panelId}-file`}>上传商品参考图</label>
      <input ref={fileInput} id={`${panelId}-file`} className={styles.fileInput} type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={chooseFile} aria-describedby={`${panelId}-limits`} />
      <p id={`${panelId}-limits`} className={styles.limits}>支持 PNG、JPEG、WebP，单张不超过 10 MiB。</p>
      {file && <p className={styles.selectedFile}><strong>{file.name}</strong><span>{formatBytes(file.size)}</span></p>}
      <div className={styles.uploadActions}><button className="button button-dark button-small" type="submit" disabled={busy || !file || Boolean(validateFile(file))}>{busy ? "正在上传…" : "上传参考图"}<span aria-hidden="true">↗</span></button>{file && <button className="text-button" type="button" disabled={busy} onClick={clearFile}>清除选择</button>}</div>
      {uploadError && <p className={styles.error} role="alert">{uploadError}</p>}
      {uploadNotice && <p className={styles.notice} role="status">{uploadNotice}</p>}
    </form>
    <div className={styles.listHeading}><span>{internal ? "本单已保存素材" : "本单可见图片"}</span><button className="text-button" type="button" onClick={() => setRevision(current => current + 1)}>刷新 ↻</button></div>
    {loadError && <p className={styles.error} role="alert">{loadError}{assets ? " 当前仍显示最近一次成功读取的图片。" : ""}</p>}
    {!assets && !loadError ? <p className={styles.empty} role="status">正在读取图片…</p> : assets?.length ? <div className={styles.assetGrid}>{assets.map(asset => <AssetCard key={asset.id} asset={asset} internal={internal} />)}</div> : !loadError && <p className={styles.empty}>还没有可见图片，先上传一张商品参考图。</p>}
  </section>;
}

export function ReferenceAssetsPanel(props: Props) {
  return <AssetsWorkspace key={props.orderId} {...props} />;
}
