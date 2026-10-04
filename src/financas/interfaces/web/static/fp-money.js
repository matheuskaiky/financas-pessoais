/**
 * fp-money.js — camada central de moeda do Finanças Pessoais.
 *
 * ES module, sem build e sem dependências. É a ÚNICA fonte de verdade para símbolo, separadores, casas decimais,
 * sinal e posição da moeda: tudo sai de Intl.NumberFormat + window.APP_CONFIG. Trocar de divisa é trocar o
 * APP_CONFIG (nada de varredura de código, nenhum "R$" escrito à mão em template ou script).
 *
 *   window.APP_CONFIG = { locale: 'pt-BR', currency: 'BRL' };          // padrão (definido aqui se a página não definiu)
 *   <script type="application/json" id="app-config">{"locale":"en-US","currency":"USD"}</script>   // ou assim, sem script inline (CSP estrita)
 *
 *   formatCurrency(1485000)                  →  "R$ 14.850,00"
 *   formatCurrency(-15000)                   →  "−R$ 150,00"            sinal tipográfico (U+2212) antes do símbolo
 *   formatCurrency(35000, { sign: 'always' })→  "+R$ 350,00"
 *   formatCurrency(1485000, 'en-US', 'USD')  →  "$14,850.00"
 *   formatCurrency(1485000, 'de-DE', 'EUR')  →  "14.850,00 €"           símbolo depois: vem do Intl, não de regra nossa
 *   formatCompact(9525000)                   →  "R$ 95,25 mil"          eixos de gráfico
 *   formatCurrency(150000, { trim: true })   →  "R$ 1.500"              marcas de eixo (só nas quantias inteiras)
 *   formatAmount(1485000)                    →  "14.850,00"             só o número (campos, Odômetro, rótulos que já têm símbolo)
 *
 * Valores entram em UNIDADE MENOR INTEIRA (centavos no BRL; 0 casas no JPY). Nunca float: CLAUDE.md, "dinheiro em centavos".
 * Valor ausente (null, undefined, NaN) não vira zero: a função devolve `opts.fallback` ("—" por padrão).
 *
 * No HTML (sem Jinja2): <span data-cents="1485000"></span>  ou  <span data-money="14850.00"></span> (decimal EXATO, em texto);
 * só o símbolo (prefixo de um campo): <span data-symbol></span>.
 * Outra divisa só neste valor (ex.: custo da API, sempre em USD, num app em BRL): <span data-cents="21" data-currency="USD" data-places="4"></span>.
 * O hidratador preenche o elemento e o refaz sozinho quando o APP_CONFIG muda ou quando o HTMX troca o DOM:
 *   <span class="money" data-neg><span class="money-sign">−</span><span class="currency-symbol">R$&nbsp;</span><span class="currency-amount">150,00</span></span>
 */

export const DEFAULT_CONFIG = Object.freeze({ locale: 'pt-BR', currency: 'BRL' });
export const MINUS = '\u2212';
export const NBSP = '\u00a0';

const G = typeof window !== 'undefined' ? window : globalThis;

// Valor padrão no topo do arquivo. A página muda o padrão de duas formas, nesta ordem de prioridade:
//   1. window.APP_CONFIG definido ANTES deste módulo (um script seu);
//   2. um bloco de DADOS no HTML, que não é script executável e por isso vale mesmo com CSP estrita (sem 'unsafe-inline'):
//      <script type="application/json" id="app-config">{{ config.client | tojson }}<\/script>      (Jinja2 + fp_money.install; a barra escapada é só para este comentário caber dentro de um <script>)
// Este arquivo é um módulo (type="module", adiado): quando ele roda, o bloco já foi lido pelo navegador.
function pageConfig() {
  if (typeof document === 'undefined' || typeof document.getElementById !== 'function') return {};
  const el = document.getElementById('app-config');
  if (!el) return {};
  try {
    const raw = JSON.parse(el.textContent || 'null');
    if (!raw || typeof raw !== 'object') throw new TypeError('o bloco precisa ser um objeto { locale, currency }');
    const locale = typeof raw.locale === 'string' ? raw.locale : DEFAULT_CONFIG.locale;
    const currency = typeof raw.currency === 'string' ? raw.currency.toUpperCase() : DEFAULT_CONFIG.currency;
    new Intl.NumberFormat(locale, { style: 'currency', currency }); // locale ou divisa inválidos lançam RangeError
    return { locale, currency };
  } catch {
    if (typeof console !== 'undefined') console.warn('fp-money: o bloco #app-config é inválido; usando o padrão', DEFAULT_CONFIG.locale, DEFAULT_CONFIG.currency);
    return {};
  }
}
if (!G.APP_CONFIG || typeof G.APP_CONFIG !== 'object') G.APP_CONFIG = { ...DEFAULT_CONFIG, ...pageConfig() };

/** Configuração em vigor. Lida a cada chamada: mudar APP_CONFIG vale na próxima formatação. */
export function getConfig() {
  const c = G.APP_CONFIG || {};
  return { locale: String(c.locale || DEFAULT_CONFIG.locale), currency: String(c.currency || DEFAULT_CONFIG.currency).toUpperCase() };
}

/** Atualiza o APP_CONFIG e avisa (evento `fp:config`): quem desenha moeda se refaz. */
export function setConfig(patch) {
  G.APP_CONFIG = { ...getConfig(), ...(patch || {}) };
  if (typeof G.dispatchEvent === 'function' && typeof G.CustomEvent === 'function') G.dispatchEvent(new G.CustomEvent('fp:config', { detail: getConfig() }));
  return getConfig();
}

// ───────────────────────────── Intl (com cache) ─────────────────────────────

const cache = new Map();
function formatter(locale, currency, compact, fraction, whole, places) {
  const key = `${locale}|${currency}|${compact ? 'c' + (fraction == null ? '' : fraction) : whole ? 'w' : places == null ? 'n' : 'p' + places}`;
  let f = cache.get(key);
  if (!f) {
    f = new Intl.NumberFormat(locale, compact
      ? { style: 'currency', currency, notation: 'compact', compactDisplay: 'short', minimumFractionDigits: 0, maximumFractionDigits: fraction == null ? 2 : fraction }
      : whole
        ? { style: 'currency', currency, minimumFractionDigits: 0, maximumFractionDigits: 0 }
        : places == null
          ? { style: 'currency', currency }
          : { style: 'currency', currency, minimumFractionDigits: places, maximumFractionDigits: places });
    cache.set(key, f);
  }
  return f;
}

function resolve(opts) {
  const cfg = getConfig();
  return { locale: (opts && opts.locale) || cfg.locale, currency: String((opts && opts.currency) || cfg.currency).toUpperCase() };
}

/** Casas da unidade menor da divisa: BRL/USD/EUR = 2, JPY = 0, KWD = 3. */
export function minorDigits(opts) {
  const { locale, currency } = resolve(opts);
  return formatter(locale, currency, false).resolvedOptions().maximumFractionDigits;
}

function normalizeMinor(v) {
  if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
  const n = typeof v === 'bigint' ? Number(v) : Number(v);
  if (!Number.isFinite(n)) return null;
  return Math.round(n);
}

// ───────────────────────────── formatação ─────────────────────────────

/**
 * Peças do valor, para quem precisa estilizar cada uma (símbolo menor, número que rola).
 * @returns {{ neg:boolean, sign:string, symbol:string, gap:string, amount:string, position:'before'|'after', text:string, minor:number } | null}
 */
export function formatMoneyParts(minor, opts = {}) {
  const n = normalizeMinor(minor);
  if (n === null) return null;
  const { locale, currency } = resolve(opts);
  // `places`: o inteiro vale 10^-places da unidade (ex.: custo de API em frações de centavo: places 4 → 21 = "US$ 0,0021"). Padrão: casas da divisa.
  const places = opts.places == null || opts.compact ? null : Math.max(0, Math.min(8, Math.trunc(Number(opts.places))));
  const digits = places == null ? minorDigits({ locale, currency }) : places;
  const abs = Math.abs(n);
  // `trim`: valor sem centavos ("R$ 1.500") quando a quantia é inteira — para eixos e linhas de referência. Os não inteiros mantêm as casas.
  const whole = !!opts.trim && !opts.compact && abs % 10 ** digits === 0;
  const parts = formatter(locale, currency, !!opts.compact, opts.fraction, whole, places).formatToParts(abs / 10 ** digits);

  const ci = parts.findIndex((p) => p.type === 'currency');
  const position = ci > 0 ? 'after' : 'before';
  const symbol = ci >= 0 ? parts[ci].value : '';
  // O literal colado ao símbolo (NBSP no pt-BR e no de-DE; nada no en-US) é o "respiro" entre símbolo e número.
  const gapIdx = ci < 0 ? -1 : position === 'before' ? (parts[ci + 1] && parts[ci + 1].type === 'literal' ? ci + 1 : -1) : (parts[ci - 1] && parts[ci - 1].type === 'literal' ? ci - 1 : -1);
  const gap = gapIdx >= 0 ? parts[gapIdx].value : '';
  const amount = parts.filter((_, i) => i !== ci && i !== gapIdx).map((p) => p.value).join('');

  const neg = n < 0;
  const mode = opts.sign || 'auto';
  const sign = mode === 'never' ? '' : neg ? MINUS : mode === 'always' && n > 0 ? '+' : '';
  const body = opts.symbol === false || !symbol ? amount : position === 'before' ? symbol + gap + amount : amount + gap + symbol;
  return { neg, sign, symbol, gap, amount, position, text: sign + body, minor: n };
}

/**
 * Texto completo com símbolo. Aceita `(minor, 'en-US', 'USD', opts)` ou `(minor, opts)`.
 * opts: { sign: 'auto'|'always'|'never', symbol: false (só o número), compact: true, fraction: n (casas máx. do abreviado, padrão 2),
 *         trim: true (sem ",00" nas quantias inteiras: "R$ 1.500"), places: n (o inteiro vale 10^-n da unidade; padrão: casas da divisa),
 *         fallback: '—', locale, currency }
 */
export function formatCurrency(minor, locale, currency, opts) {
  if (locale && typeof locale === 'object') { opts = locale; locale = undefined; currency = undefined; }
  const p = formatMoneyParts(minor, { ...(opts || {}), locale: locale || (opts && opts.locale), currency: currency || (opts && opts.currency) });
  return p ? p.text : (opts && opts.fallback !== undefined ? opts.fallback : '—');
}

/** Só o número ("14.850,00"), com sinal quando negativo. Para campos de formulário e para o Odômetro. */
export function formatAmount(minor, opts) {
  return formatCurrency(minor, { ...(opts || {}), symbol: false });
}

/** Abreviado para eixos e linhas de referência: "R$ 95,25 mil", "R$ 1,5 mi". */
export function formatCompact(minor, opts) {
  return formatCurrency(minor, { ...(opts || {}), compact: true });
}

/** Símbolo da divisa em vigor ("R$", "$", "€"). */
export function currencySymbol(opts) {
  const { locale, currency } = resolve(opts);
  const p = formatter(locale, currency, false).formatToParts(1).find((x) => x.type === 'currency');
  return p ? p.value : currency;
}

/**
 * O que um componente que desenha o símbolo à parte (Odômetro, cabeçalho de gráfico) precisa saber.
 * @returns {{ pre:string, post:string, dec:string, group:string, gap:string, position:'before'|'after', symbol:string, minus:string }}
 */
export function affixes(opts) {
  const { locale, currency } = resolve(opts);
  const f = formatter(locale, currency, false);
  const parts = f.formatToParts(123456.5);      // 6 dígitos: aparece o separador de milhar até nas regiões que só agrupam a partir de 5
  const symbol = (parts.find((p) => p.type === 'currency') || { value: currency }).value;
  const p = formatMoneyParts(100, { locale, currency });
  const digits = minorDigits({ locale, currency });
  return {
    symbol,
    position: p.position,
    pre: p.position === 'before' ? symbol : '',
    post: p.position === 'after' ? symbol : '',
    gap: p.gap,
    dec: digits > 0 ? (parts.find((x) => x.type === 'decimal') || { value: '' }).value : '',
    group: (parts.find((x) => x.type === 'group') || { value: '' }).value,
    minus: MINUS
  };
}

// ───────────────────────────── entrada decimal exata ─────────────────────────────

/**
 * "14850.00" → 1485000. Decimal em TEXTO, convertido sem passar por float (aceita "-12.5", "7", "+3.005").
 * Mais casas que a divisa tem são arredondadas para o mais próximo (meio para cima, em módulo).
 */
export function parseDecimalToMinor(text, opts) {
  const m = /^\s*([+-])?(\d+)(?:\.(\d+))?\s*$/.exec(String(text));
  if (!m) return null;
  const d = minorDigits(opts);
  const frac = (m[3] || '').padEnd(d + 1, '0');
  let v = BigInt(m[2] + frac.slice(0, d));
  if (Number(frac[d] || 0) >= 5) v += 1n;
  if (v > BigInt(Number.MAX_SAFE_INTEGER)) return null;
  return (m[1] === '-' ? -1 : 1) * Number(v);
}

// ───────────────────────────── DOM ─────────────────────────────

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function innerHTML(p, opts) {
  const sym = opts.symbol !== false && p.symbol;
  const bits = [];
  if (p.sign) bits.push(`<span class="money-sign">${esc(p.sign)}</span>`);
  const amtEl = `<span class="currency-amount">${esc(p.amount)}</span>`;
  // O respiro entre símbolo e número fica DENTRO do span do símbolo: quando o símbolo encolhe (números grandes), o respiro encolhe junto.
  if (!sym) bits.push(amtEl);
  else if (p.position === 'before') bits.push(`<span class="currency-symbol">${esc(p.symbol + p.gap)}</span>` + amtEl);
  else bits.push(amtEl + `<span class="currency-symbol">${esc(p.gap + p.symbol)}</span>`);
  return bits.join('');
}

/**
 * HTML de um valor, igual ao que o filtro `money` do Jinja2 produz (fp_money.py). Mesmo `data-cents`: o hidratador refaz se a divisa mudar.
 * opts: as de formatCurrency + { size: 'display' (símbolo reduzido), class: 'extra', private: 'networth' }.
 * `private` (opt-in) escreve data-private="<grupo>" no invólucro, para o modo privacidade (fp-privacy.js + tokens.css); valor ausente não leva.
 */
export function moneyHTML(minor, opts = {}) {
  const p = formatMoneyParts(minor, opts);
  const cls = ['money', opts.size ? `money--${opts.size}` : '', opts.class].filter(Boolean).join(' ');
  if (!p) return `<span class="${esc(cls)} money--empty">${esc(opts.fallback !== undefined ? opts.fallback : '—')}</span>`;
  return `<span class="${esc(cls)}"${p.neg ? ' data-neg' : ''} data-cents="${p.minor}"${dataAttrs(opts)}>${innerHTML(p, opts)}</span>`;
}

// Tudo que não é o padrão vai para atributos data-*: o hidratador lê de volta e refaz IGUAL (o mesmo vale para o filtro `money` do Jinja2).
function dataAttrs(opts) {
  let a = '';
  if (opts.sign && opts.sign !== 'auto') a += ` data-sign="${esc(opts.sign)}"`;
  if (opts.compact) a += ' data-compact';
  if (opts.symbol === false) a += ' data-bare';
  if (opts.currency) a += ` data-currency="${esc(String(opts.currency).toUpperCase())}"`;
  if (opts.locale) a += ` data-locale="${esc(opts.locale)}"`;
  if (opts.places != null && !opts.compact) a += ` data-places="${Math.trunc(Number(opts.places))}"`;
  if (opts.private) a += ` data-private="${esc(String(opts.private))}"`;
  return a;
}

function readOpts(el) {
  const o = {
    sign: el.getAttribute('data-sign') || 'auto',
    compact: el.hasAttribute('data-compact'),
    symbol: !el.hasAttribute('data-bare')
  };
  // Outra divisa só neste valor (ex.: custo da API em USD num app em BRL): <span data-cents="21" data-currency="USD" data-places="4">
  const c = el.getAttribute('data-currency'); if (c) o.currency = c.toUpperCase();
  const l = el.getAttribute('data-locale'); if (l) o.locale = l;
  const p = el.getAttribute('data-places'); if (p !== null && p !== '' && Number.isFinite(Number(p))) o.places = Math.trunc(Number(p));
  return o;
}

/** Preenche todo `[data-cents]` (inteiro), `[data-money]` (decimal em texto) e `[data-symbol]` (só o símbolo) dentro de `root`. Idempotente. */
export function hydrate(root) {
  const scope = root || (typeof document !== 'undefined' ? document : null);
  if (!scope || !scope.querySelectorAll) return 0;
  const cfg = getConfig();
  let n = 0;
  for (const el of scope.querySelectorAll('[data-cents],[data-money]')) {
    const raw = el.hasAttribute('data-cents') ? el.getAttribute('data-cents') : el.getAttribute('data-money');
    const opts = readOpts(el);
    const eff = { ...cfg, ...(opts.locale ? { locale: opts.locale } : {}), ...(opts.currency ? { currency: opts.currency } : {}) };
    const minor = el.hasAttribute('data-cents') ? normalizeMinor(raw) : parseDecimalToMinor(raw, eff);
    const key = `${eff.locale}|${eff.currency}|${minor}|${opts.sign}|${opts.compact}|${opts.symbol}|${opts.places == null ? '' : opts.places}`;
    if (el.getAttribute('data-fp') === key) continue;
    const p = formatMoneyParts(minor, { ...opts, ...eff });
    el.classList.add('money');
    if (p) { el.innerHTML = innerHTML(p, opts); el.toggleAttribute('data-neg', p.neg); el.classList.remove('money--empty'); }
    else { el.textContent = '—'; el.classList.add('money--empty'); el.removeAttribute('data-neg'); }
    el.setAttribute('data-fp', key);
    n++;
  }
  // Símbolo avulso (prefixo de campo de formulário, legenda): <span data-symbol></span>  →  "R$" / "$" / "€"
  for (const el of scope.querySelectorAll('[data-symbol]')) {
    const o = readOpts(el);
    const eff = { ...cfg, ...(o.locale ? { locale: o.locale } : {}), ...(o.currency ? { currency: o.currency } : {}) };
    const key = `${eff.locale}|${eff.currency}|symbol`;
    if (el.getAttribute('data-fp') === key) continue;
    el.textContent = currencySymbol(eff);
    el.setAttribute('data-fp', key);
    n++;
  }
  return n;
}

// Uma vigilância por escopo, mesmo que a página carregue duas cópias deste módulo (ex.: um quadro e o componente que ele importa):
// cópias que vigiam o mesmo nó se desfariam uma à outra sem fim. O registro fica no global, não no módulo.
const WATCHED = (G.__fpMoneyWatched = G.__fpMoneyWatched || new WeakSet());
/** Hidrata agora e mantém tudo hidratado: HTMX, re-render do framework de quadros e `fp:config`. Chamar duas vezes não duplica. */
export function observe(root) {
  if (typeof document === 'undefined') return;
  const scope = root || document;
  if (WATCHED.has(scope)) return;
  WATCHED.add(scope);
  const run = () => { hydrate(scope); };
  run();
  if (typeof MutationObserver === 'undefined') return; // fora de um navegador (testes, SSR): hidratou uma vez e pronto
  let queued = false;
  const observer = new MutationObserver(() => { if (queued) return; queued = true; Promise.resolve().then(() => { queued = false; run(); }); });
  observer.observe(scope === document ? document.documentElement : scope, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-cents', 'data-money', 'data-sign', 'data-compact', 'data-bare', 'data-currency', 'data-locale', 'data-places'] });
  if (typeof G.addEventListener === 'function') G.addEventListener('fp:config', run);
}

export const money = Object.freeze({
  DEFAULT_CONFIG, MINUS, NBSP,
  getConfig, setConfig, minorDigits,
  formatCurrency, formatAmount, formatCompact, formatMoneyParts, currencySymbol, affixes,
  parseDecimalToMinor, moneyHTML, hydrate, observe
});

// Globais pensadas para templates e scripts soltos: window.formatCurrency(...) e window.FP.money.
G.FP = G.FP || {};
G.FP.money = money;
G.formatCurrency = formatCurrency;

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => observe(), { once: true });
  else observe();
}
