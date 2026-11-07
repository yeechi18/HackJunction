"use client";

import Image from "next/image";
import { useState } from "react";
import styles from "./demo-listing.module.css";

export function DemoListingImage({ src, alt, width, height }: { src: string; alt: string; width: number; height: number }) {
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  if (failed) return <div className={styles.imageError}><p>图片暂时无法读取。</p><button className="text-button" type="button" onClick={() => { setFailed(false); setRetry(value => value + 1); }}>重新读取图片 ↻</button></div>;
  return <Image src={retry ? `${src}?preview=${retry}` : src} alt={alt} width={width} height={height} unoptimized sizes="(max-width: 800px) 100vw, 50vw" onError={() => setFailed(true)} />;
}
