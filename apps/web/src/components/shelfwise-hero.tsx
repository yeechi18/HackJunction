"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";

const screenshots = [
  { file: "home", title: "销售概览", label: "MY BUSINESS", width: 1440, height: 784 },
  { file: "orders", title: "订单管理", label: "ORDERS", width: 1440, height: 879 },
  { file: "customers", title: "买家反馈", label: "CUSTOMERS", width: 2160, height: 1260 },
  { file: "marketing", title: "营销活动", label: "MARKETING", width: 2160, height: 1224 },
];

const deck = [...screenshots, ...screenshots];
const sellerCentralGuide = "https://sell.amazon.com/tools/seller-central";
const fullTurn = 2 * Math.PI;

export function ShelfwiseHero() {
  const hero = useRef<HTMLElement>(null);
  const orbit = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: number; x: number } | null>(null);
  const dragRotation = useRef(0);
  const autoRotate = useRef(true);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    const section = hero.current;
    const scene = orbit.current;
    if (!section || !scene) return;

    const cards = Array.from(scene.querySelectorAll<HTMLElement>(".sw-orbit-card"));
    const stage = scene;
    const heroSection = section;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;
    let previousTime = 0;
    let idleRotation = 0;
    let radius = 0;
    let scrollRotation = 0;
    let inView = false;

    function render(time: number) {
      const seconds = previousTime ? Math.min((time - previousTime) / 1000, 0.05) : 0;
      previousTime = time;
      if (autoRotate.current && !drag.current) idleRotation = (idleRotation + seconds * 0.13) % fullTurn;
      const rotation = Math.PI / 8 + idleRotation + dragRotation.current + scrollRotation;
      cards.forEach((card, index) => {
        const angle = rotation + index * fullTurn / cards.length;
        const depth = Math.cos(angle);
        const x = Math.sin(angle) * radius;
        const y = depth * 56 - Math.sin(angle) * 77 + (index % 2 ? -32 : 32);
        const z = (depth - 0.45) * Math.min(radius * 0.68, 270);
        card.style.transform = `translate3d(${x}px, ${y}px, ${z}px) rotateY(${angle}rad)`;
        card.style.setProperty("--face-light", String(0.47 + 0.53 * Math.max(depth, 0)));
      });
      frame = requestAnimationFrame(render);
    }

    function updateScroll() {
      const bounds = heroSection.getBoundingClientRect();
      scrollRotation = Math.min(1, Math.max(0, -bounds.top / bounds.height)) * 2.8;
    }

    function updateSize() {
      radius = stage.clientWidth * (stage.clientWidth < 650 ? 0.37 : 0.345);
      updateScroll();
    }

    function syncMotion() {
      cancelAnimationFrame(frame);
      previousTime = 0;
      if (reducedMotion.matches) {
        drag.current = null;
        delete stage.dataset.orbit;
        return;
      }
      updateSize();
      stage.dataset.orbit = "active";
      if (inView) render(performance.now());
    }

    const visibility = new IntersectionObserver(([entry]) => {
      inView = entry.isIntersecting;
      syncMotion();
    });
    visibility.observe(section);
    const size = new ResizeObserver(updateSize);
    size.observe(scene);
    window.addEventListener("scroll", updateScroll, { passive: true });
    reducedMotion.addEventListener("change", syncMotion);
    syncMotion();

    return () => {
      cancelAnimationFrame(frame);
      visibility.disconnect();
      size.disconnect();
      window.removeEventListener("scroll", updateScroll);
      reducedMotion.removeEventListener("change", syncMotion);
      delete scene.dataset.orbit;
    };
  }, []);

  return (
    <section className="sw-hero" ref={hero} aria-labelledby="sw-title">
      <div className="sw-hero-top">
        <span>GOOD PRODUCTS. GLOBAL POSSIBILITIES.</span>
        <span>AMAZON US · 从这里出发</span>
      </div>
      <h1 id="sw-title">让好商品，<em>走向世界。</em></h1>
      <div
        className="sw-orbit-stage"
        ref={orbit}
        onPointerDown={event => {
          if (!event.isPrimary || event.button !== 0 || !event.currentTarget.dataset.orbit) return;
          drag.current = { id: event.pointerId, x: event.clientX };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={event => {
          if (drag.current?.id !== event.pointerId) return;
          dragRotation.current = (dragRotation.current + (event.clientX - drag.current.x) * 0.009) % fullTurn;
          drag.current.x = event.clientX;
        }}
        onLostPointerCapture={event => {
          if (drag.current?.id === event.pointerId) drag.current = null;
        }}
      >
        <div className="sw-orbit-deck">
          {deck.map(({ file, title, label, width, height }, index) => (
            <figure className="sw-orbit-card" key={`${file}-${index}`} aria-hidden={index >= screenshots.length || undefined}>
              <div className="sw-orbit-front">
                <div className="sw-screenshot">
                  <Image
                    src={`/seller-central/${file}.webp`}
                    alt={`Amazon Seller Central ${title}界面截图`}
                    width={width}
                    height={height}
                    unoptimized
                    loading="eager"
                    draggable={false}
                  />
                </div>
                <figcaption>
                  <span>SELLER CENTRAL / {label}</span>
                  <strong>{title}</strong>
                </figcaption>
              </div>
              <div className={`sw-orbit-back sw-orbit-back-${index % 4}`} aria-hidden="true" />
            </figure>
          ))}
        </div>
      </div>
      <div className="sw-hero-bottom">
        <p>从中文商品资料，到英文上架页面。<br /><span>把繁琐交给我们，把机会留给好产品。</span></p>
        <a className="sw-round-link" href="#about" aria-label="向下了解上架有方">↓</a>
        <div className="sw-hero-meta">
          <div className="sw-orbit-controls">
            <span className="sw-orbit-hint">拖动或滚动浏览</span>
            <button type="button" className="sw-motion-button" onClick={() => {
              autoRotate.current = paused;
              setPaused(!paused);
            }}>{paused ? "继续旋转" : "暂停旋转"}</button>
          </div>
          <a href={sellerCentralGuide}>界面截图 · Amazon 官方指南 ↗</a>
        </div>
      </div>
    </section>
  );
}
