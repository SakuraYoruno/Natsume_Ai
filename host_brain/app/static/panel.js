/**
 * Nastume AI — 运维监控面板脚本
 * panel.js
 */

// ═══════════════════════════════════════════════════════
//  全局状态
// ═══════════════════════════════════════════════════════
const API_BASE = window.location.origin;
let currentTimeline = [];
let activeFilters    = new Set();
let autoRefreshOn    = true;
let countdown        = 30;
let countdownTimer   = null;
let activeReqId      = null;

const TREND_MAX = 10;
const trendData = { labels: [], total: [], chat: [], reject: [] };

// ═══════════════════════════════════════════════════════
//  工具函数
// ═══════════════════════════════════════════════════════
function fmtTs(tsMs) {
    if (!tsMs) return '—';
    const d = new Date(tsMs);
    return d.toLocaleTimeString('zh-CN', { hour12: false })
        + '.' + String(d.getMilliseconds()).padStart(3, '0');
}

function fmtUptime(s) {
    if (s == null || isNaN(s)) return '—';
    s = Math.floor(s);
    if (s < 60)   return `${s}s`;
    if (s < 3600) return `${Math.floor(s/60)}m ${s%60}s`;
    return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`;
}

function fmtTimeShort(tsMs) {
    if (!tsMs) return '—';
    return new Date(tsMs).toLocaleTimeString('zh-CN', { hour12: false });
}

function nowIso()       { return new Date().toISOString(); }
function hoursAgoIso(h) { return new Date(Date.now() - h * 3600000).toISOString(); }

function srcClass(src) {
    if (!src) return 'src-default';
    if (src.includes('intent'))                              return 'src-intent';
    if (src.includes('governor'))                            return 'src-governor';
    if (src.includes('skill') || src.includes('exec'))      return 'src-skill';
    if (src.includes('error') || src.includes('violation')) return 'src-error';
    if (src.includes('channel') || src.includes('voice'))   return 'src-channel';
    return 'src-default';
}

function statusClass(st) {
    if (!st) return 'muted';
    const u = st.toUpperCase();
    if (u === 'APPROVE' || u === 'OK' || u === 'SUCCESS') return 'ok';
    if (u === 'REJECT'  || u === 'FAILED' || u === 'ERROR') return 'reject';
    return 'muted';
}

function reqBadge(st) {
    const u = (st || '').toUpperCase();
    if (u === 'APPROVE' || u === 'OK' || u === 'SUCCESS')
        return `<span class="badge badge-ok">OK</span>`;
    if (u === 'REJECT')
        return `<span class="badge badge-reject">拒绝</span>`;
    if (u === 'ERROR' || u === 'FAILED')
        return `<span class="badge badge-error">错误</span>`;
    return `<span class="badge badge-unknown">${st || '—'}</span>`;
}

// ═══════════════════════════════════════════════════════
//  A. 自动刷新 + 倒计时
// ═══════════════════════════════════════════════════════
function startCountdown() {
    stopCountdown();
    countdown = 30;
    renderCountdown();
    countdownTimer = setInterval(() => {
        countdown--;
        renderCountdown();
        if (countdown <= 0) {
            countdown = 30;
            if (autoRefreshOn) doRefresh();
        }
    }, 1000);
}

function stopCountdown() {
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
}

function renderCountdown() {
    const el = document.getElementById('countdown');
    if (!el) return;
    el.textContent = autoRefreshOn ? `${countdown}s` : '—';
    el.className = 'countdown' + (countdown <= 5 && autoRefreshOn ? ' urgent' : '');
}

function toggleAutoRefresh() {
    autoRefreshOn = document.getElementById('toggleInput').checked;
    if (autoRefreshOn) startCountdown();
    else { stopCountdown(); renderCountdown(); }
}

function manualRefresh() {
    countdown = 30;
    renderCountdown();
    doRefresh();
}

async function doRefresh() {
    await Promise.all([loadMetrics(), loadRecentRequests()]);
    const el = document.getElementById('lastRefresh');
    if (el) el.textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

// ═══════════════════════════════════════════════════════
//  加载 /metrics
// ═══════════════════════════════════════════════════════
async function loadMetrics() {
    try {
        const resp = await fetch(`${API_BASE}/metrics`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const d = await resp.json();

        const total  = d.total_requests      ?? 0;
        const chat   = d.total_chat_requests ?? 0;
        const reject = d.total_rejections    ?? 0;
        const uptime = d.uptime_seconds      ?? 0;
        const rate   = total > 0 ? (reject / total * 100).toFixed(1) : '0.0';

        setText('statTotal',  total);
        setText('statChat',   chat);
        setText('statReject', reject);
        setText('statRate',   rate + '%');
        setText('statUptime', fmtUptime(uptime));

        setText('statTotalSub',  '');
        setText('statChatSub',   total > 0 ? `占比 ${(chat/total*100).toFixed(1)}%` : '');
        setText('statRejectSub', reject > 0 ? '有拒绝事件' : '暂无拒绝');
        setText('statRateSub',   parseFloat(rate) > 10 ? '⚠ 偏高' : '正常');
        setText('statUptimeSub', `${uptime}s`);

        renderBarChart('reasonChart', d.reason_code_distribution || {}, [
            '#f2495c','#ff9830','#fade2a','#73bf69','#4a9eff','#b877d9'
        ]);

        const label = new Date().toLocaleTimeString('zh-CN', { hour12: false });
        pushTrend(label, total, chat, reject);

    } catch (e) {
        console.error('[metrics]', e);
    }
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

// ═══════════════════════════════════════════════════════
//  B. 最近请求列表
// ═══════════════════════════════════════════════════════
async function loadRecentRequests() {
    const container = document.getElementById('recentList');
    if (!container) return;
    try {
        const url = `${API_BASE}/ui/search-requests?query=&start_time=${encodeURIComponent(hoursAgoIso(24))}&end_time=${encodeURIComponent(nowIso())}&page=1&page_size=20`;
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const results = data.results || [];

        setText('recentBadge', `${data.total ?? results.length}`);

        if (results.length === 0) {
            container.innerHTML = `<div class="empty"><i class="bi bi-inbox"></i><p>暂无请求记录</p></div>`;
            return;
        }

        container.innerHTML = results.map(item => {
            const rid    = item.request_id || item.id || '—';
            const ts     = item.timestamp ? fmtTimeShort(item.timestamp) : '—';
            const active = rid === activeReqId ? ' active' : '';
            return `
            <div class="req-item${active}" onclick="clickReq('${rid}')" title="${rid}">
                <span class="req-id">${rid}</span>
                <span class="req-time">${ts}</span>
                ${reqBadge(item.status)}
            </div>`;
        }).join('');

    } catch (e) {
        container.innerHTML = `<div class="empty"><i class="bi bi-exclamation-triangle"></i><p>加载失败: ${e.message}</p></div>`;
    }
}

function clickReq(rid) {
    activeReqId = rid;
    document.getElementById('searchInput').value = rid;
    document.querySelectorAll('.req-item').forEach(el => {
        el.classList.toggle('active', el.querySelector('.req-id')?.textContent === rid);
    });
    searchRequest();
}

// ═══════════════════════════════════════════════════════
//  查询链路
// ═══════════════════════════════════════════════════════
async function searchRequest() {
    const rid = document.getElementById('searchInput').value.trim();
    if (!rid) return;

    hideError();
    const wrap = document.getElementById('timelineWrap');
    wrap.innerHTML = `<div class="loading-row"><div class="spinner"></div>查询中…</div>`;
    setText('timelineBadge', '…');

    try {
        const resp = await fetch(`${API_BASE}/observability/trace`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ request_id: rid })
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        const data = await resp.json();
        currentTimeline = data.timeline || [];

        setText('timelineBadge', `${currentTimeline.length} 条`);
        renderFilterBar(data.sources || {});
        renderTimeline(currentTimeline);
        renderBarChart('sourceChart', data.sources || {}, [
            '#4a9eff','#73bf69','#fade2a','#f2495c','#b877d9','#ff9830'
        ]);

    } catch (e) {
        showError(e.message);
        wrap.innerHTML = `<div class="empty"><i class="bi bi-exclamation-circle"></i><p>${e.message}</p></div>`;
        setText('timelineBadge', '失败');
    }
}

function clearSearch() {
    currentTimeline = [];
    activeFilters.clear();
    activeReqId = null;
    document.getElementById('searchInput').value = '';
    document.getElementById('filterBar').innerHTML = '';
    document.getElementById('timelineWrap').innerHTML =
        `<div class="empty"><i class="bi bi-search"></i><p>从左侧点击请求，或输入 request_id 查询</p></div>`;
    document.getElementById('sourceChart').innerHTML =
        `<div class="empty"><i class="bi bi-bar-chart"></i><p>查询后显示来源分布</p></div>`;
    setText('timelineBadge', '—');
    hideError();
    document.querySelectorAll('.req-item').forEach(el => el.classList.remove('active'));
}

// ═══════════════════════════════════════════════════════
//  渲染时间线
// ═══════════════════════════════════════════════════════
function renderTimeline(items) {
    const wrap = document.getElementById('timelineWrap');
    if (!items || items.length === 0) {
        wrap.innerHTML = `<div class="empty"><i class="bi bi-inbox"></i><p>无链路事件</p></div>`;
        return;
    }
    const filtered = activeFilters.size > 0
        ? items.filter(it => activeFilters.has(it.source))
        : items;

    if (filtered.length === 0) {
        wrap.innerHTML = `<div class="empty"><i class="bi bi-funnel"></i><p>当前过滤条件无匹配</p></div>`;
        return;
    }

    wrap.innerHTML = filtered.map(it => {
        const stCls = statusClass(it.status);
        const srcCls = srcClass(it.source || '');
        const srcLabel = (it.source || '').replace(/_log$/, '');
        return `
        <div class="tl-item">
            <div class="tl-ts">${fmtTs(it.ts_ms)}</div>
            <div class="tl-source ${srcCls}">${srcLabel}</div>
            <div class="tl-detail">
                <span class="tl-status ${stCls}">${it.status || '—'}</span>
                ${it.reason_code ? `<span class="tl-code">${it.reason_code}</span>` : ''}
                ${it.detail ? `<span class="tl-desc">${it.detail}</span>` : ''}
            </div>
        </div>`;
    }).join('');
}

// ═══════════════════════════════════════════════════════
//  过滤器
// ═══════════════════════════════════════════════════════
function renderFilterBar(sources) {
    const bar = document.getElementById('filterBar');
    if (!sources || Object.keys(sources).length === 0) {
        bar.innerHTML = '';
        return;
    }
    activeFilters.clear();
    bar.innerHTML = `<button class="filter-chip active" onclick="clearFilters()">全部</button>`
        + Object.entries(sources).map(([src, cnt]) =>
            `<button class="filter-chip" data-src="${src}" onclick="toggleFilter(this,'${src}')">${src.replace(/_log$/,'')} <span class="text-muted">${cnt}</span></button>`
        ).join('');
}

function toggleFilter(chip, src) {
    chip.classList.toggle('active');
    if (activeFilters.has(src)) activeFilters.delete(src);
    else activeFilters.add(src);
    const allBtn = document.querySelector('#filterBar .filter-chip');
    if (allBtn) allBtn.classList.toggle('active', activeFilters.size === 0);
    renderTimeline(currentTimeline);
}

function clearFilters() {
    activeFilters.clear();
    document.querySelectorAll('#filterBar .filter-chip').forEach(c => c.classList.remove('active'));
    const allBtn = document.querySelector('#filterBar .filter-chip');
    if (allBtn) allBtn.classList.add('active');
    renderTimeline(currentTimeline);
}

// ═══════════════════════════════════════════════════════
//  条形图（通用）
// ═══════════════════════════════════════════════════════
function renderBarChart(containerId, data, colors) {
    const el = document.getElementById(containerId);
    if (!el) return;
    if (!data || Object.keys(data).length === 0) {
        el.innerHTML = `<div class="empty"><i class="bi bi-check-circle"></i><p>暂无数据</p></div>`;
        return;
    }
    const total = Object.values(data).reduce((a, b) => a + b, 0);
    el.innerHTML = `<div class="bar-list">`
        + Object.entries(data)
            .sort((a, b) => b[1] - a[1])
            .map(([key, cnt], i) => {
                const pct = total > 0 ? (cnt / total * 100) : 0;
                const color = colors[i % colors.length];
                return `
                <div class="bar-row">
                    <div class="bar-key">${key}</div>
                    <div class="bar-track">
                        <div class="bar-fill" style="width:${pct}%;background:${color};"></div>
                    </div>
                    <div class="bar-val">${cnt}</div>
                </div>`;
            }).join('')
        + `</div>`;
}

// ═══════════════════════════════════════════════════════
//  错误提示
// ═══════════════════════════════════════════════════════
function showError(msg) {
    const el = document.getElementById('errorAlert');
    if (!el) return;
    el.innerHTML = `<i class="bi bi-exclamation-triangle-fill"></i> ${msg}`;
    el.style.display = 'flex';
}
function hideError() {
    const el = document.getElementById('errorAlert');
    if (el) el.style.display = 'none';
}

// ═══════════════════════════════════════════════════════
//  趋势折线图 (Canvas)
// ═══════════════════════════════════════════════════════
function pushTrend(label, total, chat, reject) {
    trendData.labels.push(label);
    trendData.total.push(total);
    trendData.chat.push(chat);
    trendData.reject.push(reject);
    if (trendData.labels.length > TREND_MAX) {
        ['labels','total','chat','reject'].forEach(k => trendData[k].shift());
    }
    setText('trendSamples', `${trendData.labels.length} 个采样点`);
    drawTrend();
}

function drawTrend() {
    const canvas = document.getElementById('trendCanvas');
    if (!canvas) return;
    const wrap = canvas.parentElement;
    const dpr  = window.devicePixelRatio || 1;
    const W    = wrap.clientWidth;
    const H    = wrap.clientHeight;
    canvas.width  = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width  = W + 'px';
    canvas.style.height = H + 'px';

    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    const n = trendData.labels.length;
    if (n < 2) {
        ctx.fillStyle = '#5a5c66';
        ctx.font = '12px Inter, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('等待更多采样点…', W / 2, H / 2);
        return;
    }

    const PAD = { top: 8, right: 16, bottom: 24, left: 36 };
    const cW  = W - PAD.left - PAD.right;
    const cH  = H - PAD.top  - PAD.bottom;

    const allVals = [...trendData.total, ...trendData.chat, ...trendData.reject];
    const maxVal  = Math.max(...allVals, 1);

    const xPos = i => PAD.left + (i / (n - 1)) * cW;
    const yPos = v => PAD.top  + cH - (v / maxVal) * cH;

    // 网格
    for (let g = 0; g <= 4; g++) {
        const y = PAD.top + (g / 4) * cH;
        ctx.beginPath();
        ctx.moveTo(PAD.left, y);
        ctx.lineTo(PAD.left + cW, y);
        ctx.strokeStyle = g === 4 ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.04)';
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.fillStyle = '#5a5c66';
        ctx.font = '10px JetBrains Mono, Consolas, monospace';
        ctx.textAlign = 'right';
        ctx.fillText(Math.round(maxVal - (g / 4) * maxVal), PAD.left - 4, y + 3);
    }

    // 折线
    function drawLine(series, color, alpha) {
        // 填充
        const grad = ctx.createLinearGradient(0, PAD.top, 0, PAD.top + cH);
        grad.addColorStop(0, color.replace(')', `,${alpha})`).replace('rgb', 'rgba'));
        grad.addColorStop(1, color.replace(')', ',0)').replace('rgb', 'rgba'));
        ctx.beginPath();
        ctx.moveTo(xPos(0), yPos(series[0]));
        for (let i = 1; i < n; i++) {
            const cx = (xPos(i-1) + xPos(i)) / 2;
            ctx.bezierCurveTo(cx, yPos(series[i-1]), cx, yPos(series[i]), xPos(i), yPos(series[i]));
        }
        ctx.lineTo(xPos(n-1), PAD.top + cH);
        ctx.lineTo(xPos(0),   PAD.top + cH);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();

        // 线
        ctx.beginPath();
        ctx.moveTo(xPos(0), yPos(series[0]));
        for (let i = 1; i < n; i++) {
            const cx = (xPos(i-1) + xPos(i)) / 2;
            ctx.bezierCurveTo(cx, yPos(series[i-1]), cx, yPos(series[i]), xPos(i), yPos(series[i]));
        }
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // 点
        for (let i = 0; i < n; i++) {
            ctx.beginPath();
            ctx.arc(xPos(i), yPos(series[i]), 3, 0, Math.PI * 2);
            ctx.fillStyle = color;
            ctx.fill();
        }
    }

    drawLine(trendData.reject, '#f2495c', 0.15);
    drawLine(trendData.chat,   '#73bf69', 0.12);
    drawLine(trendData.total,  '#4a9eff', 0.15);

    // X 轴
    ctx.fillStyle = '#5a5c66';
    ctx.font = '10px JetBrains Mono, Consolas, monospace';
    ctx.textAlign = 'center';
    [0, Math.floor((n-1)/2), n-1].filter((v,i,a) => a.indexOf(v) === i).forEach(i => {
        ctx.fillText(trendData.labels[i], xPos(i), H - PAD.bottom + 14);
    });
}

// ═══════════════════════════════════════════════════════
//  事件绑定 & 初始化
// ═══════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('searchInput')?.addEventListener('keydown', e => {
        if (e.key === 'Enter') searchRequest();
    });
    window.addEventListener('resize', () => {
        if (trendData.labels.length > 0) drawTrend();
    });

    // 初始化
    (async () => {
        await doRefresh();
        startCountdown();
    })();
});
