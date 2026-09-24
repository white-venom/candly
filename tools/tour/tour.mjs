// A self-driving tour of the candly dashboard in a visible Edge window.
//   cd tools\tour ; npm run tour            (one pass)
//   npm run tour -- --loop                  (repeat until the window is closed)
//   npm run tour -- --url http://localhost:5173
import { chromium } from "playwright-core";

const args = process.argv.slice(2);
const BASE = args.includes("--url") ? args[args.indexOf("--url") + 1] : "http://localhost:5173";
const LOOP = args.includes("--loop");
const PAUSE = 2200;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function caption(page, text) {
  await page.evaluate((t) => {
    let el = document.getElementById("candly-tour-caption");
    if (!el) {
      el = document.createElement("div");
      el.id = "candly-tour-caption";
      Object.assign(el.style, {
        position: "fixed", top: "10px", left: "50%", transform: "translateX(-50%)", zIndex: 99999,
        background: "rgba(20,110,90,0.95)", color: "#fff", padding: "10px 18px", borderRadius: "10px",
        font: "600 15px Segoe UI, system-ui, sans-serif", boxShadow: "0 6px 24px rgba(0,0,0,0.35)",
        pointerEvents: "none", maxWidth: "80vw", textAlign: "center",
      });
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, text);
}

async function open(page, path, text) {
  await page.goto(BASE + path, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1800);
  await caption(page, text);
  await sleep(PAUSE);
}

async function sweepChart(page) {
  // Move the crosshair across the right side of the chart so the OHLC legend and the expected box show.
  const { width, height } = await page.evaluate(() => ({ width: innerWidth, height: innerHeight }));
  const y = Math.round(height * 0.45);
  for (let x = Math.round(width * 0.45); x <= Math.round(width * 0.7); x += 14) {
    await page.mouse.move(x, y);
    await sleep(45);
  }
  await sleep(900);
}

async function tour(page) {
  await open(page, "/chart/NSE:NIFTY50/5m?theme=dark",
    "1/9  Nifty 50 · 5-minute · the grey box/ghosts on the right = the EXPECTED next candles");
  await sweepChart(page);

  await caption(page, "2/9  Setup panel (right): expected range for the next candle, and whether there is any up/down edge");
  await sleep(PAUSE + 800);

  for (const [key, label] of [["2", "15-minute"], ["3", "1-hour"], ["4", "daily"]]) {
    await page.keyboard.press(key);
    await sleep(1200);
    await caption(page, `3/9  Same instrument, ${label} candles — expected candle updates for this timeframe`);
    await sweepChart(page);
  }

  await page.keyboard.press("1");
  for (let i = 0; i < 3; i += 1) {
    await page.keyboard.press("]");
    await sleep(1400);
    await caption(page, "4/9  Next instrument in the watchlist (the ] key) — live price and today's change on the left");
    await sweepChart(page);
  }

  await open(page, "/chart/MCX:CRUDEOIL/15m?theme=dark",
    "5/9  MCX Crude Oil · 15-minute · commodity session runs until 23:30/23:55 IST");
  await sweepChart(page);

  await open(page, "/accuracy?method=range_v1&theme=dark",
    "6/9  Accuracy: every forecast graded against the real candle — SAME / CLOSE / WRONG");
  await page.mouse.wheel(0, 500);
  await sleep(PAUSE);
  await page.mouse.wheel(0, 700);
  await caption(page, "7/9  Ledger: predicted candle vs actual candle, side by side, per forecast");
  await sleep(PAUSE + 1200);

  await open(page, "/scanner?theme=dark", "8/9  Scanner: every instrument, ranked; expected move and expiry at a glance");
  await open(page, "/news?theme=dark", "9/9  News: tagged to instruments, with sentiment");

  await page.goto(BASE + "/chart/NSE:NIFTY50/5m?theme=light", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  await caption(page, "Light theme too (press t to switch) — tour complete");
  await sweepChart(page);
  await sleep(PAUSE);
}

const HEADLESS = args.includes("--headless");  // for automated checks only
const browser = await chromium.launch({
  channel: "msedge", headless: HEADLESS, slowMo: HEADLESS ? 0 : 60, args: ["--start-maximized"],
});
const context = await browser.newContext({ viewport: HEADLESS ? { width: 1366, height: 768 } : null });
const page = await context.newPage();
try {
  do {
    await tour(page);
  } while (LOOP && browser.isConnected());
} catch (err) {
  if (browser.isConnected()) throw err;  // closing the window ends the tour quietly
} finally {
  if (browser.isConnected()) await browser.close();
}
