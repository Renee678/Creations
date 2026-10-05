// Lookmate front end: plain JS over the JSON API. The user id lives in localStorage.
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} },
};
let vocab = { styles: {}, body_shapes: {} };
let userId = store.get("userId");

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join("; ") : body.detail;
    const fallback = { 413: "Those photos are too large. Try fewer or smaller photos.", 429: "Too many requests. Please try again in a moment." };
    throw new Error(detail || fallback[res.status] || `Request failed (${res.status})`);
  }
  return body;
}

// ---------- tabs ----------
function show(tab) {
  for (const id of ["find", "lookbook", "style", "trends", "profile"]) $("#" + id).hidden = id !== tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  if (tab === "style") loadStyle();
  if (tab === "trends") loadTrends();
  if (tab === "lookbook") loadLookbook(); else renderRoom();
}
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));

// ---------- profile ----------
function chips(container, options, multi, selected = []) {
  container.innerHTML = "";
  for (const [id, label] of Object.entries(options)) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip" + (selected.includes(id) ? " on" : ""); b.dataset.id = id; b.textContent = label;
    b.addEventListener("click", () => {
      if (!multi) container.querySelectorAll(".chip").forEach((c) => c.classList.remove("on"));
      b.classList.toggle("on", multi ? !b.classList.contains("on") : true);
    });
    container.appendChild(b);
  }
}
const picked = (container) => [...container.querySelectorAll(".chip.on")].map((c) => c.dataset.id);

async function openProfile() {
  let p = {};
  if (userId) {
    try { p = await api(`/api/users/${userId}`); } catch { userId = null; }
  }
  const f = $("#profile-form");
  for (const k of ["nickname", "height_cm", "weight_kg", "age", "budget_per_item"]) if (p[k] != null) f.elements[k].value = p[k];
  chips($("#shape-options"), vocab.body_shapes, false, [p.body_shape || "unsure"]);
  chips($("#style-options"), vocab.styles, true, p.preferred_styles || []);
  $("#profile-title").textContent = userId ? "My profile" : "Tell us about you";
  $("#profile-msg").textContent = p.fit_advice ? `Fit advice: ${p.fit_advice}` : "";
}

$("#profile-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {
    nickname: f.elements.nickname.value, height_cm: +f.elements.height_cm.value, weight_kg: +f.elements.weight_kg.value,
    age: +f.elements.age.value, budget_per_item: +f.elements.budget_per_item.value,
    body_shape: picked($("#shape-options"))[0] || "unsure", preferred_styles: picked($("#style-options")),
  };
  try {
    const saved = await api(userId ? `/api/users/${userId}` : "/api/users", {
      method: userId ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const isNew = !userId;
    userId = String(saved.id); store.set("userId", userId);
    $("#tabs").hidden = false;
    $("#profile-msg").textContent = `Saved. Fit advice: ${saved.fit_advice}`;
    if (isNew) show("find");
  } catch (err) { $("#profile-msg").textContent = err.message; }
});

// ---------- find look-alikes ----------
const drop = $("#drop");
["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove("over")));
drop.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]); });
$("#file").addEventListener("change", (e) => e.target.files[0] && handleFile(e.target.files[0]));

function setStatus(text, isError = false) {
  const s = $("#status"); s.hidden = !text; s.textContent = text || ""; s.classList.toggle("error", isError);
}

async function handleFile(file) {
  $("#preview").src = URL.createObjectURL(file); $("#preview").hidden = false;
  document.querySelectorAll("#drop .hint").forEach((el) => el.setAttribute("hidden", "")); $("#drop-text").textContent = "Try another photo";
  $("#results").innerHTML = "";
  setStatus("Uploading…");
  const form = new FormData(); form.append("user_id", userId); form.append("image", file);
  try {
    const look = await api("/api/looks", { method: "POST", body: form, headers: { "X-Access-Code": accessCode() } }).catch((err) => {
      if (err.message === "access code required") store.set("accessCode", "");
      throw err;
    });
    pollLook(look.id, 0);
  } catch (err) { setStatus(err.message, true); }
}

// ---------- price range (one per page, remembered on this device) ----------
const priceRange = {};  // page -> {low, high}, or undefined for the server default (0 to 1.5x budget)

function priceQuery(page) {
  const r = priceRange[page];
  return r ? `price_min=${r.low}&price_max=${r.high}` : "";
}

function showPriceRange(page, r) {
  const box = $(`#price-${page}`);
  if (!box || !r) return;
  box.querySelector(".pr-min").value = r.low;
  box.querySelector(".pr-max").value = r.high;
  box.querySelector(".pr-out").textContent = `$${Math.round(r.low)} – $${Math.round(r.high)}`;
}

function setupPriceRange(page, reload) {
  const box = $(`#price-${page}`);
  try { priceRange[page] = JSON.parse(store.get(`price:${page}`)) || undefined; } catch { priceRange[page] = undefined; }
  showPriceRange(page, priceRange[page]);
  const lo = box.querySelector(".pr-min"), hi = box.querySelector(".pr-max");
  const read = (moved) => {
    let low = Number(lo.value), high = Number(hi.value);
    if (low > high) { if (moved === lo) high = low; else low = high; }
    return { low, high };
  };
  [lo, hi].forEach((el) => {
    el.addEventListener("input", () => showPriceRange(page, read(el)));
    el.addEventListener("change", () => {
      priceRange[page] = read(el);
      store.set(`price:${page}`, JSON.stringify(priceRange[page]));
      reload();
    });
  });
  box.querySelector(".pr-reset").addEventListener("click", () => {
    priceRange[page] = undefined; store.set(`price:${page}`, ""); reload();
  });
}

let currentLookId = null;
setupPriceRange("find", () => currentLookId && refreshLook());

async function refreshLook() {
  try {
    const look = await api(`/api/looks/${currentLookId}?${priceQuery("find")}`);
    renderLook(look.result);
  } catch (err) { setStatus(err.message, true); }
}

async function pollLook(id, tries) {
  try {
    const look = await api(`/api/looks/${id}?${priceQuery("find")}`);
    if (look.status === "done") { setStatus(""); currentLookId = id; renderLook(look.result); return; }
    if (look.status === "failed") { setStatus(look.error || "Analysis failed. Please try another photo.", true); return; }
    setStatus(look.attempts > 1 ? `The AI is busy, retrying (attempt ${look.attempts})…` : "Spotting each piece and searching for dupes…");
    if (tries < 90) setTimeout(() => pollLook(id, tries + 1), 1500);
    else setStatus("This is taking too long. Refresh the page in a little while.", true);
  } catch (err) { setStatus(err.message, true); }
}

function productCard(p, styleTags, opts = {}) {
  const img = p.image_url ? `<img src="${esc(p.image_url)}" alt="" loading="lazy" onerror="this.parentElement.textContent='${esc(p.product_type)}'">` : esc(p.product_type);
  const saving = p.saving_usd > 0 ? `<span class="saving">Save about $${Math.round(p.saving_usd)}</span>` : "";
  const shop = p.shop_links
    ? `<div class="shop"><a class="gel" href="${esc(p.shop_links.shein)}" target="_blank" rel="noopener">SHEIN ↗</a><a class="gel" href="${esc(p.shop_links.asos)}" target="_blank" rel="noopener">ASOS ↗</a></div>`
    : "";
  return `<div class="card"><span class="tape"></span><span class="price">$${p.price.toFixed(2)}</span>
    <div class="img">${img}</div>
    <div class="body">
      <span class="name">${esc(p.name)}</span>
      <span class="meta">${esc(p.colour)}</span>${saving}
      <span class="reasons">${(p.reasons || []).map(esc).join(" · ")}</span>
      ${shop}
      ${opts.room ? `<button type="button" class="room-add" data-room="${esc(p.id)}">+ fitting room</button>` : ""}
      <button class="save" data-save="${esc(p.id)}" data-tags="${esc((styleTags || []).join(","))}">♡ save to lookbook</button>
    </div></div>`;
}

function renderLook(r) {
  showPriceRange("find", r.price_range);
  const tags = (r.style_tags || []).map((t) => `<span class="chip">${esc(vocab.styles[t] || t)}</span>`).join("");
  let html = `<div class="vibe"><span class="tape"></span><h2>the vibe</h2><p>${esc(r.vibe)}</p><div class="chips static">${tags}</div></div>`;
  for (const s of r.sections) {
    const it = s.item;
    const orig = it.estimated_original_price_usd ? `<span class="muted">Original about $${Math.round(it.estimated_original_price_usd)}</span>` : "";
    const count = s.picks.length ? `<span class="tag">${s.picks.length} dupes</span>` : "";
    html += `<div class="item"><div class="item-head"><h3>${esc(it.colour)} ${esc(it.name)}</h3>${count}${orig}</div>
      <div class="grid">${s.picks.map((p) => productCard(p, it.style_tags)).join("") || '<p class="muted">No good dupes found.</p>'}</div></div>`;
  }
  $("#results").innerHTML = html;
}

async function onSaveClick(e) {
  const b = e.target.closest("button[data-save]");
  if (!b || b.classList.contains("saved")) return;
  try {
    await api(`/api/users/${userId}/saved`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_id: b.dataset.save, style_tags: b.dataset.tags ? b.dataset.tags.split(",") : [] }),
    });
    b.classList.add("saved"); b.textContent = "♥ saved";
  } catch (err) { setStatus(err.message, true); }
}
$("#results").addEventListener("click", onSaveClick);

// ---------- my style report ----------
const pct = (x, total) => Math.round((x / total) * 100);

async function loadStyle() {
  const [s, p] = await Promise.all([api(`/api/users/${userId}/style`), api(`/api/users/${userId}`)]);
  const a = s.analysis;
  const list = (xs) => `<ul>${(xs || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  const hero = a ? `<div class="sr-hero" style="--g:${esc(a.best_colours.map((c) => c.hex).join(", "))}">
      <div class="sr-hero-band"></div>
      <div class="sr-hero-body">
        <span class="sr-kicker">Your colour season</span>
        <h2>${esc(cap(a.season_detail))}</h2>
        <div class="sr-stats">
          <span><small>Undertone</small>${esc(cap(a.undertone))}</span>
          <span><small>Contrast</small>${esc(cap(a.contrast))}</span>
          <span><small>Metals</small>${esc(cap(a.metals))}</span>
          <span><small>Face</small>${esc(cap(a.face_shape))}</span>
        </div>
        <p>${esc(a.colouring_notes)}</p>
      </div></div>`
    : `<div class="sr-hero sr-empty"><div class="sr-hero-body"><span class="sr-kicker">Your colour season</span>
        <h2>Not analysed yet</h2><p>Add a selfie and a full-body photo in the Lookbook tab to get your colour season,
        palette, face shape and hair and makeup ideas.</p>
        <button type="button" class="gel primary" data-goto="lookbook">Add my photos</button></div></div>`;
  const palette = a ? `<div class="sr-card"><h3>Your palette</h3><p class="muted small">Wear these near your face.</p>
      <div class="sr-dots">${a.best_colours.map((c) => `<figure><i style="background:${esc(c.hex)}"></i><figcaption>${esc(c.name)}</figcaption></figure>`).join("")}</div>
      <h3>Keep away from your face</h3>
      <div class="sr-dots sr-avoid">${a.avoid_colours.map((c) => `<figure><i style="background:${esc(c.hex)}"></i><figcaption>${esc(c.name)}</figcaption></figure>`).join("")}</div></div>` : "";
  const beauty = a ? `<div class="sr-pair">
      <div class="sr-card"><h3>Hair</h3><p class="muted">${esc(a.hair_now)}</p>${list(a.hair_suggestions)}</div>
      <div class="sr-card"><h3>Makeup</h3>${list(a.makeup_suggestions)}</div></div>` : "";
  const fit = s.fit;
  const body = `<div class="sr-card"><h3>Body and fit${fit.shape ? ` · ${esc(fit.shape)}` : ""}</h3><p>${esc(fit.summary)}</p>
      ${a && a.silhouette_notes ? `<p class="muted">${esc(a.silhouette_notes)}</p>` : ""}
      ${fit.look_for.length ? `<div class="sr-tags"><span class="sr-label">Look for</span>${fit.look_for.map((w) => `<span class="chip">${esc(w)}</span>`).join("")}</div>` : ""}
      ${fit.skip.length ? `<div class="sr-tags"><span class="sr-label">Skip</span>${fit.skip.map((w) => `<span class="chip skip">${esc(w)}</span>`).join("")}</div>` : ""}</div>`;
  const total = s.styles.reduce((t, x) => t + x.weight, 0) || 1;
  const dna = `<div class="sr-card"><h3>Your style DNA</h3><p>${esc(s.summary)}</p>
      <div class="sr-dna">${s.styles.map((x) => `<div class="sr-dna-row"><span>${esc(x.label)}</span><div><i style="width:${pct(x.weight, total)}%"></i></div><b>${pct(x.weight, total)}%</b></div>`).join("")}</div>
      <h3>Colours you wear most</h3>
      <div class="chips static">${s.colours.map(([c, n]) => `<span class="chip">${esc(c)} × ${n}</span>`).join("") || '<span class="muted">Nothing yet</span>'}</div>
      <p class="muted small">Learned from ${s.signals} signals: outfits you upload and pieces you save.</p></div>`;
  $("#style-report").innerHTML = hero + `<div class="sr-grid">${palette}${beauty}${body}${dna}</div>`
    + (a ? `<p class="muted small">${esc(a.caveats)}</p>` : "");
}
document.addEventListener("click", (e) => {
  const go = e.target.closest("[data-goto]");
  if (go) { show(go.dataset.goto); window.scrollTo({ top: 0, behavior: "smooth" }); }
});

// ---------- trends ----------
// By season, then style; each grouped into key pieces, bags and shoes, makeup and hair, colours.
let trendData = null;
let trendSeason = null;
let trendStyle = "mine";
const SEASON_NAMES = { spring: "Spring", summer: "Summer", autumn: "Autumn", winter: "Winter" };
const FIT_BADGE = { suits: ["fit-suits", "✓ Suits you"], adapt: ["fit-adapt", "♡ Adapt it"], neutral: ["fit-neutral", "Neutral for you"] };

async function loadTrends() {
  const t = await api(`/api/trends${userId ? `?user_id=${userId}` : ""}`);
  if (!t.trends.length) { $("#trends-meta").textContent = "Trends are still being gathered. Check back soon."; return; }
  trendData = t;
  trendSeason = trendSeason && t.seasons.includes(trendSeason) ? trendSeason : t.current_season;
  const when = new Date(t.refreshed_at).toLocaleDateString("en-US");
  $("#trends-meta").textContent = t.origin === "seed" ? `Sample trends (offline data), updated ${when}` : `Researched by AI on the web, updated ${when}`;
  renderTrends();
}

function renderTrends() {
  const t = trendData;
  const mine = new Set(t.my_styles.map((s) => s.id));
  const personal = t.trends.some((x) => x.fit);
  $("#trend-seasons").innerHTML = t.seasons.map((s) =>
    `<button type="button" class="chip ${s === trendSeason ? "on" : ""}" data-season="${s}">${SEASON_NAMES[s]}${s === t.current_season ? " · now" : ""}</button>`).join("");
  const inSeason = t.trends.filter((x) => x.season === trendSeason);
  const styles = [...new Map(inSeason.map((x) => [x.style_id, x.style])).entries()];
  const forYou = (x) => mine.has(x.style_id) || (x.fit && x.fit.verdict === "suits");
  if (trendStyle === "mine" && !inSeason.some(forYou)) trendStyle = "all";
  $("#trend-styles").innerHTML = [["mine", "For you"], ["all", "All styles"], ...styles].map(([id, label]) =>
    `<button type="button" class="chip ${id === trendStyle ? "on" : ""}" data-style="${esc(id)}">${esc(label)}</button>`).join("");
  $("#trend-note").textContent = personal
    ? "“For you” = your styles plus trends in your colours. Each card says whether it suits you."
    : "Add your photos in the Lookbook tab to see which trends suit your colouring.";
  const shown = inSeason.filter((x) => trendStyle === "all" || (trendStyle === "mine" ? forYou(x) : x.style_id === trendStyle));
  $("#trend-list").innerHTML = Object.entries(t.kinds).map(([kind, title]) => {
    const cards = shown.filter((x) => x.kind === kind);
    if (!cards.length) return "";
    return `<div class="trend-kind"><h2>${esc(title)}</h2>${cards.map(trendCard).join("")}</div>`;
  }).join("") || '<p class="muted">Nothing for this style this season yet.</p>';
}

function trendCard(x) {
  const fit = x.fit ? `<div class="fit ${FIT_BADGE[x.fit.verdict][0]}"><strong>${FIT_BADGE[x.fit.verdict][1]}</strong><span>${x.fit.notes.map(esc).join(" · ")}</span></div>` : "";
  const colours = x.colours.length ? `<div class="swatches">${x.colours.map((c) => `<span class="swatch"><i style="background:${esc(c.hex)}"></i>${esc(c.name)}</span>`).join("")}</div>` : "";
  return `<div class="trend"><span class="tape"></span>
      <div class="trend-head"><h3>${esc(x.label)}</h3><span class="tag">${esc(x.style)}</span></div>
      ${fit}<p>${esc(x.description)}</p>${colours}
      <div class="chips static">${x.keywords.map((k) => `<span class="chip">#${esc(k)}</span>`).join("")}</div>
      ${x.examples.length ? `<div class="grid">${x.examples.map((p) => productCard(p, [x.style_id])).join("")}</div>` : ""}
      <div class="sources">${(x.sources || []).map((u, i) => `<a href="${esc(u)}" target="_blank" rel="noopener">Source ${i + 1}</a>`).join("")}</div>
    </div>`;
}

$("#trend-seasons").addEventListener("click", (e) => {
  const b = e.target.closest("[data-season]");
  if (b) { trendSeason = b.dataset.season; trendStyle = "mine"; renderTrends(); }
});
$("#trend-styles").addEventListener("click", (e) => {
  const b = e.target.closest("[data-style]");
  if (b) { trendStyle = b.dataset.style; renderTrends(); }
});
$("#trend-list").addEventListener("click", onSaveClick);

// ---------- personal analysis + lookbook ----------
let lbMode = "seasons";
let lbSeason = "";  // empty: the server picks the current season
const meDrop = $("#me-drop");
["dragover", "dragenter"].forEach((ev) => meDrop.addEventListener(ev, (e) => { e.preventDefault(); meDrop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => meDrop.addEventListener(ev, () => meDrop.classList.remove("over")));
meDrop.addEventListener("drop", (e) => { e.preventDefault(); handlePhotos([...e.dataTransfer.files]); });
$("#me-files").addEventListener("change", (e) => handlePhotos([...e.target.files]));

function setMeStatus(text, isError = false) {
  const s = $("#me-status"); s.hidden = !text; s.textContent = text || ""; s.classList.toggle("error", isError);
}

function accessCode() {
  let code = store.get("accessCode") || "";
  if (vocab.access_code_required && !code) { code = prompt("Enter your access code") || ""; store.set("accessCode", code); }
  return code;
}

let mePhotos = [];  // the shrunk photos from this upload, to reuse the full-body one for try-on

async function handlePhotos(files) {
  files = files.slice(0, 3);
  if (!files.length) return;
  setMeStatus("Preparing your photos…");
  // Phone photos are 5-12 MB each; 1600px is plenty for colour analysis and keeps uploads small.
  mePhotos = await Promise.all(files.map((f) => shrinkPhoto(f, 1600)));
  // New photos, new lookbook: start the fitting room empty.
  room = {}; store.set("room", "{}"); renderRoom();
  // The old analysis and lookbook belong to the old photos: hide them until the new ones are read.
  analysing = true; lbShown = null; lbSeq++;
  $("#me-analysis").innerHTML = ""; $("#lb-note").textContent = "";
  $("#lb-sections").innerHTML = `<p class="muted lb-wait"><span class="spinner small"></span> Your lookbook appears once your photos are read.</p>`;
  $("#me-previews").innerHTML = mePhotos.map((f, i) =>
    `<figure class="me-photo"><img src="${URL.createObjectURL(f)}" alt=""><figcaption id="me-check-${i}"></figcaption></figure>`).join("");
  $("#me-previews").hidden = false; $("#me-drop-text").textContent = "Use different photos";
  setMeStatus("Uploading…");
  const form = new FormData();
  mePhotos.forEach((f, i) => form.append("photos", f, `photo-${i}.jpg`));
  try {
    const a = await api(`/api/users/${userId}/analyses`, { method: "POST", body: form, headers: { "X-Access-Code": accessCode() } })
      .catch((err) => { if (err.message === "access code required") store.set("accessCode", ""); throw err; });
    pollAnalysis(a.id, 0);
  } catch (err) { analysing = false; loadLookbook(true); setMeStatus(err.message, true); }
}

async function pollAnalysis(id, tries) {
  try {
    const a = await api(`/api/analyses/${id}`);
    if (a.status === "done") { analysing = false; setMeStatus(""); loadLookbook(true); return; }
    if (a.status === "failed") {
      analysing = false; loadLookbook(true);
      setMeStatus(a.error || "Analysis failed. Please try other photos.", true); return;
    }
    setMeStatus(a.attempts > 1 ? `The AI is busy, retrying (attempt ${a.attempts})…` : "Reading your colouring, face shape and style…");
    if (tries < 90) setTimeout(() => pollAnalysis(id, tries + 1), 1500);
    else setMeStatus("This is taking too long. Refresh the page in a little while.", true);
  } catch (err) { setMeStatus(err.message, true); }
}

const cap = (s) => String(s || "").replace(/^./, (c) => c.toUpperCase());

const FRAMING = { face: "Selfie", upper_body: "Waist up", full_body: "Full body", no_person: "No person found" };

/** Label each uploaded photo and keep the best full-body one for try-on. */
function applyPhotoChecks(checks) {
  if (!checks || !checks.length || checks.length !== mePhotos.length) return;
  checks.forEach((c, i) => {
    const el = $(`#me-check-${i}`);
    if (!el) return;
    const uses = [c.good_for_colour && "colours", c.good_for_tryon && "try-on"].filter(Boolean);
    el.className = uses.length ? "ok" : "warn";
    el.innerHTML = `<strong>${esc(FRAMING[c.framing] || c.framing)}</strong><span>${uses.length ? `✓ ${uses.join(" + ")}` : "✗ not usable"}</span><span>${esc(c.tip)}</span>`;
  });
  const best = checks.findIndex((c) => c.good_for_tryon);
  if (best >= 0) setTryonPhoto(mePhotos[best]);
  else setMeStatus("None of these photos works for try-on yet: add a full-body photo, standing and facing the camera.");
}

function renderAnalysis(a) {
  if (!a) { $("#me-analysis").innerHTML = ""; return; }
  applyPhotoChecks(a.photo_checks);
  // The full report lives in My Style; the Lookbook just shows which palette it is dressing.
  $("#me-analysis").innerHTML = `<div class="me-summary"><span>Dressing you as a <strong>${esc(cap(a.season_detail))}</strong></span>
    <span class="me-mini">${a.best_colours.slice(0, 6).map((c) => `<i style="background:${esc(c.hex)}" title="${esc(c.name)}"></i>`).join("")}</span>
    <button type="button" class="linklike" data-goto="style">See your full report →</button></div>`;
}

const WEARABLE = new Set(["top", "bottom", "dress", "outerwear"]);  // what the try-on model can render
const ROOM_SLOTS = ["outerwear", "top", "dress", "bottom", "shoes", "bag", "accessory"];  // one of each in the fitting room
const lbProducts = new Map();  // every piece on the lookbook page, for the fitting room

function outfitCard(o) {
  o.pieces.forEach((p) => lbProducts.set(p.id, p));
  const trend = o.trend ? `<span class="tag">On trend: ${esc(o.trend)}</span>` : "";
  // Try-on happens in one place, the fitting room; an outfit card just fills it.
  const ids = o.pieces.map((p) => p.id).join(",");
  return `<div class="outfit"><div class="item-head"><h3>${esc(o.title)}</h3>${trend}<span class="muted">$${o.total_price.toFixed(2)} total</span>
      <button type="button" class="gel room-all" data-room-all="${esc(ids)}">+ whole outfit to fitting room</button></div>
    ${o.why ? `<p class="outfit-why">✦ ${esc(o.why)}</p>` : ""}
    <div class="grid">${o.pieces.map((p) => productCard(p, [o.style_id], { room: true })).join("")}</div></div>`;
}

// ---------- virtual try-on ----------
// The try-on photo stays on this device (localStorage); it's sent with each try-on and never kept by Lookmate.
let tryonPhoto = null;
let pendingOutfit = null;
const tryonPieces = {};  // key -> the pieces being tried on ("room" is the fitting room)
const tryonHtml = {};    // key -> what its box shows, restored if the lookbook re-renders

async function setTryonPhoto(blob) {
  tryonPhoto = await shrinkPhoto(blob, 1024);
  const reader = new FileReader();
  reader.onload = () => { store.set("tryonPhoto", reader.result); renderRoom(); };
  reader.readAsDataURL(tryonPhoto);
}

function savedTryonPhoto() {
  const url = store.get("tryonPhoto");
  if (!url || !url.startsWith("data:image/")) return null;
  const [head, b64] = url.split(",");
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  return new Blob([bytes], { type: head.slice(5, head.indexOf(";")) });
}

/** Scale a photo down to at most `max` px on its longest side: faster upload, same result. */
async function shrinkPhoto(file, max = 1024) {
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, max / Math.max(bmp.width, bmp.height));
    const canvas = Object.assign(document.createElement("canvas"), { width: Math.round(bmp.width * scale), height: Math.round(bmp.height * scale) });
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    return await new Promise((ok) => canvas.toBlob((b) => ok(b || file), "image/jpeg", 0.9));
  } catch { return file; }
}

$("#tryon-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const key = pendingOutfit || "room";
  tryonBox(key, waitHtml("Checking your photo…"));
  const small = await shrinkPhoto(file, 1024);
  // Same check as the Lookbook upload: try-on needs head to knees, facing the camera.
  const form = new FormData();
  form.append("photo", small, "me.jpg");
  const check = await api(`/api/users/${userId}/photo-check`, { method: "POST", body: form, headers: { "X-Access-Code": accessCode() } })
    .catch(() => null);  // if the check itself fails, don't block the user
  if (check && !check.good_for_tryon) {
    pendingOutfit = null;
    return tryonBox(key, `<div class="tryon-need"><p><strong>This photo won't work for try-on</strong> (${esc(FRAMING[check.framing] || check.framing)}). ${esc(check.tip)}</p>
      <p class="muted small">Try-on needs one person standing, facing the camera, from head to at least the knees.</p>
      <button type="button" class="gel primary" data-tryon-pick>Choose another photo</button></div>`);
  }
  await setTryonPhoto(small);
  if (pendingOutfit !== null) startTryOn(pendingOutfit);
  else { delete tryonHtml[key]; const box = $(`#tryon-${key}`); if (box) { box.hidden = true; box.innerHTML = ""; } $("#room-result").hidden = true; }
});

function tryonBox(key, html) {
  tryonHtml[key] = html;
  const box = $(`#tryon-${key}`);
  if (box) { box.hidden = false; box.innerHTML = html; }
  if (key === "room") $("#room-result").hidden = false;
  return box;
}

let tryonRunning = false;

function startTryOn(key) {
  if (tryonRunning) return;
  pendingOutfit = key;
  tryonPhoto = tryonPhoto || savedTryonPhoto();
  if (!tryonPhoto) {
    // Photos never leave the device they were picked on, so a new phone or browser has to be given one once.
    tryonBox(key, `<div class="tryon-need"><p><strong>Pick a full-body photo of you</strong> (standing, facing the camera, head to knees).</p>
      <p class="muted small">Your photos stay on the device you picked them on, so this phone or browser needs one once. It remembers it after that.</p>
      <button type="button" class="gel primary" data-tryon-pick>Choose a photo</button></div>`);
    return;
  }
  pendingOutfit = null;
  const pieces = key === "room" ? roomPieces() : tryonPieces[key];
  tryonPieces[key] = pieces;
  tryonRunning = true;
  renderRoom();
  const started = Date.now();
  // Show the pieces pinned next to you straight away; the rendered picture replaces it when ready.
  tryonBox(key, previewHtml(pieces, URL.createObjectURL(tryonPhoto)));
  const tick = setInterval(() => {
    const el = $(`#tryon-${key} .tryon-clock`);
    if (!el) return;
    const s = Math.round((Date.now() - started) / 1000);
    el.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    if (s === 45) $(`#tryon-${key} .tryon-hint`).textContent = "The model is warming up, which can take a few minutes the first time.";
  }, 1000);
  const done = (html) => { clearInterval(tick); tryonRunning = false; tryonBox(key, html); renderRoom(); };
  if (key === "room") $("#room-result").scrollIntoView({ behavior: "smooth", block: "start" });
  const form = new FormData();
  form.append("photo", tryonPhoto, "me.jpg");
  form.append("product_ids", pieces.map((p) => p.id).join(","));
  api(`/api/users/${userId}/tryons`, { method: "POST", body: form, headers: { "X-Access-Code": accessCode() } })
    .then((t) => pollTryOn(key, t.id, started, 0, done))
    .catch((err) => done(errorHtml(key, networkHint(err))));
}

const TRYON_GIVE_UP_MS = 6 * 60 * 1000;  // the server cancels a stuck step after 4 minutes; this is the backstop

function previewHtml(pieces, photoUrl) {
  return `<div class="tryon-board">
      <figure class="tryon-shot rendering"><span class="tape"></span><img src="${esc(photoUrl)}" alt="You">
        <div class="tryon-overlay"><span class="spinner"></span><b>Dressing you</b><span class="tryon-clock">0:00</span></div>
        <figcaption>you + this look ♡</figcaption></figure>
      <div class="tryon-pins">${pins(pieces)}</div>
    </div>
    <p class="muted small tryon-hint">Usually ready in under a minute.</p>`;
}

const pins = (pieces) => pieces.filter((p) => p.image_url).map((p, i) =>
  `<figure class="pin" style="--r:${(i % 2 ? 1 : -1) * (2 + i % 3)}deg"><img src="${esc(p.image_url)}" alt=""><figcaption>${esc(p.product_type)}</figcaption></figure>`).join("");

const waitHtml = (text) => `<div class="tryon-wait"><span class="spinner"></span> ${esc(text)}</div>`;
const errorHtml = (key, text) => `<p class="status error">${esc(text)} <button type="button" class="linklike" data-tryon-retry="${key}">Try again</button></p>`;
const networkHint = (err) => err instanceof TypeError ? "Lost the connection to Lookmate. Check your signal." : err.message;

async function pollTryOn(key, id, started, misses, done) {
  if (Date.now() - started > TRYON_GIVE_UP_MS) return done(errorHtml(key, "The try-on model didn't answer in time."));
  let t;
  try { t = await api(`/api/tryons/${id}`); } catch (err) {
    // A phone drops its connection now and then (screen off, switching apps): keep waiting, don't give up.
    if (err instanceof TypeError && misses < 30) return setTimeout(() => pollTryOn(key, id, started, misses + 1, done), 4000);
    return done(errorHtml(key, networkHint(err)));
  }
  if (t.status === "done") return done(renderedHtml(key, t));
  if (t.status === "failed") return done(errorHtml(key, t.error || "Try-on failed. Try another photo?"));
  if (t.attempts > 1) { const h = $(`#tryon-${key} .tryon-hint`); if (h) h.textContent = `The try-on model is busy, retrying (attempt ${t.attempts})…`; }
  setTimeout(() => pollTryOn(key, id, started, 0, done), 2000);
}

function renderedHtml(key, t) {
  const pieces = tryonPieces[key] || [];
  const rendered = new Set(t.result.rendered_ids || []);
  // Pieces the model didn't draw (shoes, bags, a second top layer) are pinned beside the photo, scrapbook style.
  const pinned = pieces.filter((p) => !rendered.has(p.id));
  // A shop photo that wouldn't load is drawn from its description, so it may look less like the real piece.
  const described = pieces.filter((p) => (t.result.described_ids || []).includes(p.id)).map((p) => p.name);
  const note = t.result.rendered
    ? "Rendered by an AI try-on model. Colours and fit are an impression, not a promise."
      + (described.length ? ` The shop photo wouldn't load for ${described.join(", ")}, so it was drawn from its description.` : "")
    : "Collage preview: add a FASHN_API_KEY or REPLICATE_API_TOKEN to .env to render the outfit on you.";
  return `<div class="tryon-board">
      <figure class="tryon-shot"><span class="tape"></span><img src="${esc(t.image_url)}" alt="You wearing this outfit"><figcaption>${t.result.rendered ? "you, in this look ♡" : "you + this look ♡"}</figcaption></figure>
      <div class="tryon-pins">${pins(pinned)}</div>
    </div>
    <p class="muted small">${note} Your photo was deleted after rendering.
      <button type="button" class="linklike" data-tryon-new="${key}">Use another photo</button> ·
      <button type="button" class="linklike" data-tryon-delete="${esc(t.id)}" data-outfit="${key}">Delete this picture</button></p>`;
}

async function onTryonClick(e) {
  const retry = e.target.closest("[data-tryon-retry]");
  if (retry) return startTryOn(retry.dataset.tryonRetry);
  if (e.target.closest("[data-tryon-pick]")) return $("#tryon-file").click();
  const again = e.target.closest("[data-tryon-new]");
  if (again) {
    tryonPhoto = null; store.set("tryonPhoto", "");
    pendingOutfit = again.dataset.tryonNew;
    return $("#tryon-file").click();
  }
  const del = e.target.closest("[data-tryon-delete]");
  if (del) {
    await api(`/api/tryons/${del.dataset.tryonDelete}`, { method: "DELETE" }).catch(() => {});
    const key = del.dataset.outfit;
    delete tryonHtml[key];
    const box = $(`#tryon-${key}`); box.hidden = true; box.innerHTML = "";
    if (key === "room") $("#room-result").hidden = true;
  }
}
$("#lb-sections").addEventListener("click", onTryonClick);
$("#room-result").addEventListener("click", onTryonClick);

// ---------- fitting room: mix pieces from different outfits, then try them on together ----------
let room = {};  // slot -> product
try { room = JSON.parse(store.get("room")) || {}; } catch { room = {}; }

const roomPieces = () => ROOM_SLOTS.map((s) => room[s]).filter(Boolean);
const roomSlot = (p) => ROOM_SLOTS.includes(p.category) ? p.category : "accessory";

function toggleRoom(id, keep = false) {
  const p = lbProducts.get(id);
  if (!p) return;
  const slot = roomSlot(p);
  if (room[slot] && room[slot].id === id) { if (!keep) delete room[slot]; }
  else {
    room[slot] = { id: p.id, name: p.name, category: p.category, product_type: p.product_type, image_url: p.image_url, price: p.price };
    // A dress replaces a top and a bottom, and the other way round.
    if (slot === "dress") { delete room.top; delete room.bottom; }
    if (slot === "top" || slot === "bottom") delete room.dress;
  }
  store.set("room", JSON.stringify(room));
  renderRoom();
}

function renderRoom() {
  const pieces = roomPieces();
  const ids = new Set(pieces.map((p) => p.id));
  document.querySelectorAll("button[data-room]").forEach((b) => {
    const on = ids.has(b.dataset.room);
    b.classList.toggle("on", on);
    b.textContent = on ? "✓ in fitting room" : "+ fitting room";
  });
  const tray = $("#room");
  // No tray before the Lookbook has outfits (photos first), and only on the Lookbook tab.
  tray.hidden = !pieces.length || $("#lookbook").hidden || !$("#lb-sections .outfit");
  document.body.classList.toggle("room-open", !tray.hidden);
  if (!pieces.length) return;
  const photo = store.get("tryonPhoto");
  const hasPhoto = photo && photo.startsWith("data:image/");
  const me = hasPhoto ? `<img src="${photo}" alt="You">` : `<span class="room-me-empty">+ your photo</span>`;
  const canTry = pieces.some((p) => WEARABLE.has(p.category));
  const total = pieces.reduce((s, p) => s + p.price, 0);
  tray.innerHTML = `<button type="button" class="room-me" data-room-photo title="${hasPhoto ? "Change your try-on photo" : "Add a full-body photo of you"}">${me}</button>
    <div class="room-pieces">${pieces.map((p) => `<button type="button" class="room-piece" data-room="${esc(p.id)}" title="Remove ${esc(p.name)}">
      ${p.image_url ? `<img src="${esc(p.image_url)}" alt="">` : `<span>${esc(p.product_type)}</span>`}<i>×</i></button>`).join("")}</div>
    <div class="room-actions"><span class="room-total">$${total.toFixed(2)}</span>
      ${tryonRunning ? `<button type="button" class="gel primary" disabled><span class="spinner small"></span> Dressing you…</button>`
        : canTry ? `<button type="button" class="gel primary" data-room-try>✨ Try it on me</button>`
        : `<span class="room-need">Add a top, bottom,<br>dress or jacket</span>`}
      <button type="button" class="linklike" data-room-clear>clear</button></div>`;
}

document.addEventListener("click", (e) => {
  const add = e.target.closest("button[data-room]");
  if (add) return toggleRoom(add.dataset.room);
  const all = e.target.closest("[data-room-all]");
  if (all) return all.dataset.roomAll.split(",").forEach((id) => toggleRoom(id, true));
  if (e.target.closest("[data-room-try]")) {
    // No photo on this device yet: open the picker straight away, then try on.
    if (!(tryonPhoto || savedTryonPhoto())) { pendingOutfit = "room"; return $("#tryon-file").click(); }
    return startTryOn("room");
  }
  if (e.target.closest("[data-room-photo]")) { pendingOutfit = null; return $("#tryon-file").click(); }
  if (e.target.closest("[data-lb-browse]")) { store.set("lbBrowse", "1"); return loadLookbook(true); }
  if (e.target.closest("[data-room-clear]")) { room = {}; store.set("room", "{}"); renderRoom(); }
});

let lbShown = null;  // the query the lookbook on screen was built from
let lbSeq = 0;
let analysing = false;  // new photos are being read: no lookbook until they are

/** Build the lookbook. Opening the tab again doesn't rebuild it, so a try-on in progress stays put. */
async function loadLookbook(force = false) {
  const season = lbMode === "seasons" && lbSeason ? `&season=${lbSeason}` : "";
  const query = `mode=${lbMode}${season}&${priceQuery("lookbook")}`;
  if (analysing) return;
  if (!force && query === lbShown) return renderRoom();
  const seq = ++lbSeq;
  // A new page of outfits goes past the AI stylist, which takes a few seconds; say so if it's slow.
  const slow = setTimeout(() => {
    if (seq === lbSeq) $("#lb-sections").innerHTML = `<p class="muted lb-wait"><span class="spinner small"></span> Styling your outfits…</p>`;
  }, 600);
  try {
    const lb = await api(`/api/users/${userId}/lookbook?${query}`);
    if (seq !== lbSeq) return;  // a newer request (another range or mode) is on its way
    lbShown = query;
    showPriceRange("lookbook", lb.price_range);
    renderAnalysis(lb.analysis);
    renderSeasonChips(lb);
    $("#lb-note").textContent = lb.personal
      ? `Built from your palette and your styles: ${lb.styles.map((s) => s.label).join(", ")}.`
        + (lb.styled && lb.stylist !== "fake" ? " An AI stylist picked each outfit from the best-matching pieces." : "")
      : "Upload a photo for outfits in your own colours. For now these use neutral colours and your styles.";
    if (!lb.personal && store.get("lbBrowse") !== "1") {
      // Photos first: outfits are built from your colours, and the full-body photo is what try-on dresses.
      $("#lb-note").textContent = "";
      $("#lb-sections").innerHTML = `<div class="lb-gate"><span class="tape"></span><h2>Start with your photos ♡</h2>
        <p>Add a selfie and a full-body photo above. Your lookbook is then built in your own colours, and the full-body photo is what <strong>Try it on me</strong> dresses.</p>
        <button type="button" class="linklike" data-lb-browse>or browse outfits without a photo</button></div>`;
      return renderRoom();
    }
    $("#lb-sections").innerHTML = lb.sections.map((s) =>
      `<div class="lb-section"><h2>${esc(s.title)}</h2>${s.outfits.map(outfitCard).join("") || '<p class="muted">Nothing found for this one yet.</p>'}</div>`).join("");
    renderRoom();
  } catch (err) { setMeStatus(err.message, true); }
  finally { clearTimeout(slow); }
}

/** This season first; the next one is a tap away. */
function renderSeasonChips(lb) {
  const box = $("#lb-seasons");
  box.hidden = lb.mode !== "seasons";
  if (box.hidden) return;
  const chip = (id, tag) => `<button type="button" class="chip${lb.season === id ? " on" : ""}" data-season="${id}">${cap(id)} · ${tag}</button>`;
  box.innerHTML = chip(lb.current_season, "now") + chip(lb.next_season, "next");
}

$("#lb-seasons").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-season]");
  if (!b) return;
  lbSeason = b.dataset.season;
  loadLookbook();
});

$("#lb-mode").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-mode]");
  if (!b) return;
  lbMode = b.dataset.mode;
  $("#lb-mode").querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === b));
  loadLookbook();
});
$("#lb-sections").addEventListener("click", onSaveClick);
setupPriceRange("lookbook", () => loadLookbook());

// ---------- installable app ----------
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

// ---------- boot ----------
(async function boot() {
  vocab = await api("/api/vocab");
  await openProfile();
  if (userId) { $("#tabs").hidden = false; show("find"); } else { show("profile"); }
})();
