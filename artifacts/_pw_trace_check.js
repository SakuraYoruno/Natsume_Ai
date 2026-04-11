const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const base = 'http://127.0.0.1:18080';
  const req = await fetch(`${base}/obs/sessions?page=1&page_size=1`);
  const sess = await req.json();
  const rid = sess?.items?.[0]?.request_id || '';

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1720, height: 1100 } });

  const out = {
    request_id: rid,
    trace_home: {},
    trace_detail: {}
  };

  await page.goto(`${base}/monitor/index.html#/trace`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(6000);
  const homeText = await page.textContent('body');
  out.trace_home.hasChainConsole = /链路控制台/.test(homeText || '');
  out.trace_home.hasTraceTitle = /链路可视化|同轴时间线|活跃链路/.test(homeText || '');
  out.trace_home.hasRequestIdSnippet = rid ? (homeText || '').includes(rid.slice(0, 8)) : false;
  await page.screenshot({ path: 'D:/Natsume_Ai/artifacts/frontend_trace_home_realdata_pw.png', fullPage: true });

  if (rid) {
    await page.goto(`${base}/monitor/index.html#/trace/${rid}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(7000);
    const detailText = await page.textContent('body');
    out.trace_detail.hasRequestDetail = /请求级 Trace 深入查看|请求链路详情/.test(detailText || '');
    out.trace_detail.hasNodeTimeline = /节点时间线|同轴时间线/.test(detailText || '');
    out.trace_detail.hasRequestId = (detailText || '').includes(rid);
    await page.screenshot({ path: 'D:/Natsume_Ai/artifacts/frontend_trace_detail_realdata_pw.png', fullPage: true });
  }

  await browser.close();
  fs.writeFileSync('D:/Natsume_Ai/artifacts/frontend_trace_realdata_pw_check.json', JSON.stringify(out, null, 2), 'utf-8');
  console.log(JSON.stringify(out));
})();
