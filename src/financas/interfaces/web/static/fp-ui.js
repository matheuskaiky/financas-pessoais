/**
 * fp-ui.js — pequenos comportamentos de interface, sem build e sem dependências.
 *
 * Hoje: marca as bordas das áreas de rolagem horizontal (`.sx`) que ainda têm conteúdo além do limite.
 *
 *   <div class="sx sx-col" role="region" aria-label="Lançamentos" tabindex="0"> …tabela larga… </div>
 *
 * Onde o navegador tem scroll-driven animations (Chromium, Safari 26), o tokens.css faz o esmaecimento das bordas
 * sozinho, só com CSS, e este arquivo é redundante (inofensivo). Nos demais (Firefox), ele escreve
 * data-sx-l="1" (há conteúdo escondido à esquerda) e data-sx-r="1" (à direita), e o mesmo CSS usa esses atributos.
 * Carregue como módulo:  <script type="module" src="/static/fp-ui.js"></script>   (HTMX: o MutationObserver cuida do que chega depois).
 */

const SEEN = new WeakSet();
let ro = null;
let mo = null;

/** Recalcula as duas bordas de uma área `.sx`. */
export function updateEdges(el) {
  // Com barra de rolagem vertical (extrato com cabeçalho fixo) o Chromium deixa o fim da rolagem horizontal até a largura da barra
  // antes de scrollWidth - clientWidth; descontar a barra faz a borda direita apagar no fim real em qualquer navegador.
  const bar = Math.max(0, (el.offsetWidth || 0) - el.clientWidth - 2 * (el.clientLeft || 0));
  const max = el.scrollWidth - el.clientWidth;
  const x = Math.abs(el.scrollLeft);
  const l = x > 1 ? '1' : '0';
  const r = max > 1 && x < max - bar - 1 ? '1' : '0';
  if (el.getAttribute('data-sx-l') !== l) el.setAttribute('data-sx-l', l);
  if (el.getAttribute('data-sx-r') !== r) el.setAttribute('data-sx-r', r);
}

/** Liga o acompanhamento em toda `.sx` dentro de `root` (idempotente). */
export function scrollEdges(root) {
  const scope = root || (typeof document !== 'undefined' ? document : null);
  if (!scope || !scope.querySelectorAll) return 0;
  if (!ro && typeof ResizeObserver !== 'undefined') ro = new ResizeObserver((entries) => { for (const e of entries) updateEdges(e.target.closest ? (e.target.closest('.sx') || e.target) : e.target); });
  let n = 0;
  for (const el of scope.querySelectorAll('.sx')) {
    if (SEEN.has(el)) continue;
    SEEN.add(el);
    el.addEventListener('scroll', () => updateEdges(el), { passive: true });
    if (ro) { ro.observe(el); for (const c of el.children) ro.observe(c); }
    updateEdges(el);
    n++;
  }
  return n;
}

/** scrollEdges agora e para tudo que o DOM ganhar depois (HTMX, re-render). Chamar duas vezes não duplica. */
export function observe() {
  if (typeof document === 'undefined' || mo) return;
  scrollEdges(document);
  let queued = false;
  mo = new MutationObserver(() => { if (queued) return; queued = true; requestAnimationFrame(() => { queued = false; scrollEdges(document); }); });
  mo.observe(document.documentElement, { childList: true, subtree: true });
}

export const ui = Object.freeze({ updateEdges, scrollEdges, observe });

if (typeof window !== 'undefined') {
  window.FP = window.FP || {};
  window.FP.ui = ui;
}
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => observe(), { once: true });
  else observe();
}
