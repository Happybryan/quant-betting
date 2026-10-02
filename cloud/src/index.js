// DATA_v1.2 cloud collector: Kalshi + Pinnacle -> D1. Cron every minute; each run executes the most overdue jobs
// within a subrequest budget (free plan ~50 fetches/invocation). Adaptive cadence per league, heartbeat per job.
const KALSHI = "https://api.elections.kalshi.com/trade-api/v2";
const PIN = "https://guest.api.arcadia.pinnacle.com/0.1";
// ponytail: public key Pinnacle's own website ships to browsers; unofficial, may be rotated.
const PIN_H = { "X-API-Key": "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R", "User-Agent": "Mozilla/5.0" };
const LEAGUES = { NFL: ["KXNFLGAME", 889], NCAAF: ["KXNCAAFGAME", 880], MLB: ["KXMLBGAME", 246],
  NHL: ["KXNHLGAME", 1456], NBA: ["KXNBAGAME", 487], WNBA: ["KXWNBAGAME", 578] };
const KALSHI_PROPS = ["KXNFLPASSTDS", "KXNFLPASSYDS", "KXNFLREC", "KXNFLRECYDS", "KXNFLRSHYDS",
  "KXNFLPASSCOMP", "KXNFLPASSATT", "KXNFLRSHATT", "KXNFLPASSINT"];

const iso = (d = new Date()) => d.toISOString().slice(0, 19) + "+00:00";
const num = (v) => (v === null || v === undefined || v === "" ? null : Number(v));

const BUDGET = 40;          // fetches per invocation, under the free-plan subrequest limit
let spent = 0;
const sleep = (ms) => new Promise((s) => setTimeout(s, ms));

async function get(url, headers) {
  for (let i = 0; i < 2; i++) {
    if (spent >= BUDGET) throw new Error("subrequest budget exhausted");
    spent++;
    if (url.startsWith(KALSHI)) await sleep(300);  // pace Kalshi; it 429s bursts from shared Cloudflare IPs
    const r = await fetch(url, { headers: headers || { "User-Agent": "curl/8.1.2" } });
    if (r.ok) return r.json();
    await r.body?.cancel();
    if (i === 1 || r.status !== 429) throw new Error(`${r.status} ${url.slice(0, 90)}`);
    await sleep(Math.min(Number(r.headers.get("retry-after") || 2), 5) * 1000);
  }
}

async function kalshiSeries(series, source, league) {
  const rows = [], mult = source === "kalshi" ? (await get(`${KALSHI}/series/${series}`)).series.fee_multiplier : null;
  let cursor = "";
  do {
    const d = await get(`${KALSHI}/markets?series_ticker=${series}&status=open&limit=1000&cursor=${cursor}`);
    for (const m of d.markets) {
      const extra = source === "kalshi"
        ? { ticker: m.ticker, fee_multiplier: mult, volume: num(m.volume_fp), open_interest: num(m.open_interest_fp), last: num(m.last_price_dollars) }
        : { ticker: m.ticker, series, volume: num(m.volume_fp) };
      rows.push([source, league, m.event_ticker, m.occurrence_datetime, source === "kalshi" ? m.yes_sub_title : m.title,
        num(m.yes_bid_dollars), num(m.yes_ask_dollars), null, num(m.yes_bid_size_fp), num(m.yes_ask_size_fp), extra]);
    }
    cursor = d.markets.length ? d.cursor : "";
  } while (cursor);
  return rows;
}

// Player props: league-level (props only exist for these US leagues).
async function pinnacleProps(league, lid) {
  const all = new Map((await get(`${PIN}/leagues/${lid}/matchups`, PIN_H)).filter((m) => !m.isLive).map((m) => [m.id, m]));
  const rows = [];
  for (const k of await get(`${PIN}/leagues/${lid}/markets/straight`, PIN_H)) {
    const sp = all.get(k.matchupId);
    if (!sp || sp.special?.category !== "Player Props" || !(k.status == null || k.status === "open")) continue;
    const limit = (k.limits || [{}])[0].amount ?? null;
    const names = new Map(sp.participants.map((p) => [p.id, p.name]));
    for (const pr of k.prices) {
      const side = names.get(pr.participantId);
      if ((side === "Over" || side === "Under") && pr.points != null)
        rows.push(["pinnacle_prop", league, String(sp.id), sp.startTime, `${sp.special.description}|${side}`, null, null,
          pr.price, null, null, { points: pr.points, game: sp.parent?.id ?? null, limit }]);
    }
  }
  const nowIso = new Date().toISOString();
  const next = rows.map((r) => r[3]).filter((t) => t && t > nowIso).sort()[0] || null;
  return { rows, next };
}

// Moneylines for EVERY league of one Pinnacle sport (2- and 3-way). League label: short name for the six legacy
// US leagues (so existing readers keep working), otherwise Pinnacle's league name.
const SHORT = { 889: "NFL", 880: "NCAAF", 246: "MLB", 1456: "NHL", 487: "NBA", 578: "WNBA" };
async function pinnacleSport(sid, sname) {
  const ms = new Map((await get(`${PIN}/sports/${sid}/matchups`, PIN_H))
    .filter((m) => m.type === "matchup" && !m.parentId && !m.isLive).map((m) => [m.id, m]));
  const rows = [];
  let next = null;
  for (const k of await get(`${PIN}/sports/${sid}/markets/straight?primaryOnly=true`, PIN_H)) {
    const m = ms.get(k.matchupId);
    if (!m || k.type !== "moneyline" || k.period !== 0 || k.isAlternate || (k.status && k.status !== "open")) continue;
    const names = Object.fromEntries(m.participants.map((p) => [p.alignment, p.name]));
    const league = SHORT[m.league.id] || m.league.name;
    const limit = (k.limits || [{}])[0].amount ?? null;
    for (const pr of k.prices) {
      if (!pr.designation) continue;
      rows.push(["pinnacle", league, String(m.id), m.startTime, names[pr.designation] || (pr.designation === "draw" ? "Draw" : pr.designation),
        null, null, pr.price, null, null, { side: pr.designation, limit, sport: sname, league_id: m.league.id }]);
    }
    if (m.startTime > new Date().toISOString() && (!next || m.startTime < next)) next = m.startTime;
  }
  return { rows, next };
}

// Store only changes; mark vanished markets "gone". Returns rows written.
async function store(env, ts, sources, rows, covered) {
  // covered: leagues this job fully observed; markets there that vanished are marked gone
  const key = (r) => JSON.stringify([r[5], r[6], r[7], r[10].points ?? null]);
  const stmts = [], seen = new Set();
  for (const src of sources) {
    const leagues = new Set([...covered, ...rows.filter((r) => r[0] === src).map((r) => r[1])]);
    const res = [];
    for (const lg of leagues)  // per league (indexed): never parse the whole table (CPU limit)
      res.push(...(await env.DB.prepare("SELECT league, event_key, selection, k FROM latest WHERE source=? AND league=?").bind(src, lg).all()).results);
    const last = new Map(res.map((x) => [x.event_key + "\u0000" + x.selection, x]));
    for (const r of rows.filter((r) => r[0] === src)) {
      const id = r[2] + "\u0000" + r[4], k = key(r);
      seen.add(id);
      if (last.get(id)?.k === k) continue;
      stmts.push(env.DB.prepare("INSERT INTO snapshots (ts,source,league,event_key,start_time,selection,bid,ask,price,bid_size,ask_size,extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
        .bind(ts, r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], JSON.stringify(r[10])));
      stmts.push(env.DB.prepare("INSERT OR REPLACE INTO latest VALUES (?,?,?,?,?,?,?)").bind(r[0], r[1], r[2], r[4], k, r[3], ts));
    }
    for (const [id, x] of last) {
      if (x.k === "gone" || seen.has(id) || !covered.has(x.league)) continue;
      stmts.push(env.DB.prepare("INSERT INTO snapshots (ts,source,league,event_key,start_time,selection,extra) VALUES (?,?,?,?,NULL,?,?)")
        .bind(ts, src, x.league, x.event_key, x.selection, JSON.stringify({ gone: true })));
      // delete, not mark: ended markets were re-read every run forever (rows-read limit); a reappearing market is re-logged as new
      stmts.push(env.DB.prepare("DELETE FROM latest WHERE source=? AND event_key=? AND selection=?").bind(src, x.event_key, x.selection));
    }
  }
  for (let i = 0; i < stmts.length; i += 100) await env.DB.batch(stmts.slice(i, i + 100));
  return stmts.length / 2;
}

// One Pinnacle league's moneylines (used for big sports like Soccer, where the sport-level payload exceeds the CPU limit).
async function pinnacleLeagueML(lid, name, sport) {
  const ms = new Map((await get(`${PIN}/leagues/${lid}/matchups`, PIN_H))
    .filter((m) => m.type === "matchup" && !m.parentId && !m.isLive).map((m) => [m.id, m]));
  const rows = [];
  let next = null;
  for (const k of await get(`${PIN}/leagues/${lid}/markets/straight?primaryOnly=true`, PIN_H)) {
    const m = ms.get(k.matchupId);
    if (!m || k.type !== "moneyline" || k.period !== 0 || k.isAlternate || (k.status && k.status !== "open")) continue;
    const names = Object.fromEntries(m.participants.map((p) => [p.alignment, p.name]));
    const limit = (k.limits || [{}])[0].amount ?? null;
    for (const pr of k.prices) {
      if (!pr.designation) continue;
      rows.push(["pinnacle", name, String(m.id), m.startTime, names[pr.designation] || (pr.designation === "draw" ? "Draw" : pr.designation),
        null, null, pr.price, null, null, { side: pr.designation, limit, sport, league_id: lid }]);
    }
    if (m.startTime > new Date().toISOString() && (!next || m.startTime < next)) next = m.startTime;
  }
  return { rows, next };
}

const NON_SPORTS = new Set(["Politics", "Entertainment"]);
const PER_LEAGUE_SPORTS = new Set(["Soccer"]);  // sport-level payload (~3.4 MB) exceeds the free-plan 10 ms CPU limit
const cadence = (next, now, floor) => {
  if (!next) return 30;
  const mins = (Date.parse(next) - now) / 60000;
  return Math.max(floor, mins <= 60 ? 2 : mins <= 180 ? 5 : mins <= 720 ? 10 : 30);
};

async function collect(env) {
  spent = 0;
  const now = new Date(), ts = iso(now), due = [];
  const nextOf = new Map((await env.DB.prepare("SELECT job, next_start FROM job_next").all()).results.map((x) => [x.job, x.next_start]));
  // tiny per-job table: scanning collector_runs every minute blew the free 5M rows-read/day D1 limit
  const lastOk = new Map((await env.DB.prepare("SELECT job, ts FROM last_ok").all()).results.map((x) => [x.job, x.ts]));
  const jobs = [];
  for (const [league, [, lid]] of Object.entries(LEAGUES))
    jobs.push({ league, name: "pinnacle_prop", sources: ["pinnacle_prop"], fn: () => pinnacleProps(league, lid),
                covered: () => new Set([league]), floor: 2, next: nextOf.get(league) });
  let sports = [];
  try { sports = (await get(`${PIN}/sports`, PIN_H)).filter((x) => x.matchupCount && !NON_SPORTS.has(x.name)); } catch (e) { console.log(`sports list failed ${e}`); }
  const wanted = (await env.DB.prepare("SELECT league_id, name, sport FROM wanted_leagues").all()).results;
  for (const w of wanted)
    jobs.push({ league: `SPORT:${w.sport}|${w.name}`, name: "pinnacle", sources: ["pinnacle"], fn: () => pinnacleLeagueML(w.league_id, w.name, w.sport),
                covered: () => new Set([w.name]), floor: 2, next: nextOf.get(`SPORT:${w.sport}|${w.name}`) });
  for (const sp of sports.filter((x) => !PER_LEAGUE_SPORTS.has(x.name)))
    jobs.push({ league: `SPORT:${sp.name}`, name: "pinnacle", sources: ["pinnacle"], fn: () => pinnacleSport(sp.id, sp.name),
                covered: (rows) => new Set(rows.map((r) => r[1])), floor: sp.matchupCount > 300 ? 5 : 2, next: nextOf.get(`SPORT:${sp.name}`) });
  for (const j of jobs) {
    const every = cadence(j.next, now, j.floor);
    const t = lastOk.get(`${j.league}|${j.name}`);
    const overdue = t ? (now - Date.parse(t)) / 60000 - every : 1e9;
    if (overdue > -0.5) due.push([overdue / every, every, j]);
  }
  due.sort((a, b) => b[0] - a[0]);  // most overdue (relative to its cadence) first
  const log = [];
  for (const [, every, j] of due) {
    if (spent >= BUDGET - 3) break;  // leave headroom; the rest run next minute
    const t0 = Date.now();
    try {
      const { rows, next } = await j.fn();
      const written = await store(env, ts, j.sources, rows, j.covered(rows));
      if (next) await env.DB.prepare("INSERT OR REPLACE INTO job_next VALUES (?,?)").bind(j.league, next).run();
      await env.DB.prepare("INSERT INTO collector_runs (ts,league,source,ok,rows_seen,rows_written,error,seconds) VALUES (?,?,?,1,?,?,NULL,?)")
        .bind(ts, j.league, j.name, rows.length, written, (Date.now() - t0) / 1000).run();
      await env.DB.prepare("INSERT OR REPLACE INTO last_ok VALUES (?,?)").bind(`${j.league}|${j.name}`, ts).run();
      log.push(`${j.league} ${j.name} ok ${rows.length}/${written} (every ${every}m)`);
    } catch (e) {
      await env.DB.prepare("INSERT INTO collector_runs (ts,league,source,ok,rows_seen,rows_written,error,seconds) VALUES (?,?,?,0,NULL,NULL,?,?)")
        .bind(ts, j.league, j.name, String(e).slice(0, 300), (Date.now() - t0) / 1000).run();
      log.push(`${j.league} ${j.name} FAILED ${e}`);
    }
  }
  console.log(`spent ${spent} fetches; ${log.join(" | ")}`);
}

export default {
  async scheduled(_, env, ctx) { ctx.waitUntil(collect(env)); },
  // Read-only health view: last successful pull per league/source. No secrets, no writes.
  async fetch(_, env) {
    const r = await env.DB.prepare(`SELECT league, source, max(CASE WHEN ok=1 THEN ts END) last_ok, sum(ok=0) fails, count(*) runs
      FROM collector_runs WHERE ts > datetime('now','-1 day') GROUP BY league, source`).all();
    return Response.json({ now: iso(), runs: r.results });
  },
};
