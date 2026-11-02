/**
 * CoA public brief for zerotouchalgo.com/ltp.
 *
 * Cron copies every Strike Desk board into KV. GET /public-brief.json?market=
 * proxies the live desk file so /ltp cannot freeze on a leftover snapshot
 * (BTC 24x7 was stuck on 29 Oct 15:45 CALL TAGGED while the desk was WATCHING).
 *
 * Crude / Natgas have no desk file yet — those stay on KV.
 */
const DESK = "https://desk.zerotouchalgo.com";
// Custom-domain fetch() to desk.*.zerotouchalgo.com is a same-zone subrequest
// and fails. workers.dev is off-zone and can reach the GCE origin.
const SELF = "https://coa-brief-pull.ashukla2k2.workers.dev";

const DESK_PATHS = {
  nifty: "/public-brief.json",
  banknifty: "/public-brief-banknifty.json",
  sensex: "/public-brief-sensex.json",
  "btc-options": "/public-brief-btc-options.json",
};

const KV_KEYS = {
  nifty: "public:coa-brief",
  banknifty: "public:coa-brief:banknifty",
  sensex: "public:coa-brief:sensex",
  "btc-options": "public:coa-brief:btc-options",
  crude: "public:coa-brief:crude",
  natgas: "public:coa-brief:natgas",
};

const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store, no-cache, must-revalidate",
  "access-control-allow-origin": "https://zerotouchalgo.com",
};

function marketOf(url) {
  const raw = (url.searchParams.get("market") || "nifty").toLowerCase().trim();
  if (!raw || raw === "index") return "nifty";
  if (raw === "btc") return "btc-options";
  return raw;
}

function isCoaBrief(parsed) {
  return Boolean(
    parsed && parsed.opportunity_source === "coa" && typeof parsed.line === "string"
  );
}

async function pullOne(env, market) {
  const path = DESK_PATHS[market];
  if (!path) return;
  const res = await fetch(DESK + path, { headers: { "cache-control": "no-store" } });
  if (!res.ok) return;
  const text = await res.text();
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return;
  }
  if (!isCoaBrief(parsed)) return;
  await env.STATUS.put(KV_KEYS[market], text);
}

async function pullAll(env) {
  await Promise.all(
    Object.keys(DESK_PATHS).map((market) => pullOne(env, market).catch(() => {}))
  );
}

function jsonResponse(body, status = 200) {
  return new Response(body, { status, headers: JSON_HEADERS });
}

export default {
  async scheduled(_event, env) {
    await pullAll(env);
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname !== "/public-brief.json") {
      return new Response("ok");
    }

    const market = marketOf(url);
    const deskPath = DESK_PATHS[market];
    if (deskPath) {
      try {
        const offZone = url.hostname.endsWith(".workers.dev");
        const target = offZone
          ? DESK + deskPath
          : SELF + "/public-brief.json?market=" + encodeURIComponent(market);
        const res = await fetch(target, {
          headers: { "cache-control": "no-store" },
        });
        if (res.ok) {
          const text = await res.text();
          const parsed = JSON.parse(text);
          if (isCoaBrief(parsed)) {
            ctx.waitUntil(env.STATUS.put(KV_KEYS[market], text).catch(() => {}));
            return jsonResponse(text);
          }
        }
      } catch {
        // fall through to KV
      }
    }

    const key = KV_KEYS[market];
    if (key) {
      const cached = await env.STATUS.get(key);
      if (cached) return jsonResponse(cached);
    }
    return jsonResponse(JSON.stringify({ error: "brief unavailable" }), 502);
  },
};
