(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const QUALITIES = JSON.parse($("qualities-data").textContent);
  const KIND = { track: "ترک", album: "آلبوم", playlist: "پلی‌لیست" };
  const POLL_MS = 1500;

  const svg = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;
  const ICONS = {
    mp3_128: svg("M4 15v-3a8 8 0 0 1 16 0v3M4 15a2 2 0 0 0 2 2h1v-5H6a2 2 0 0 0-2 2Zm16 0a2 2 0 0 1-2 2h-1v-5h1a2 2 0 0 1 2 2Z"),
    best: svg("M6 3h12l3 6-9 12L3 9l3-6Zm-3 6h18M9 3l3 18 3-18"),
    mp3_320: svg("M12 3c1 4 5 5.5 5 10a5 5 0 0 1-10 0c0-2.2 1-3.6 2-4.5.3 1.6 1.2 2.5 2 2.5 0-3 0-5.5 1-8Z"),
    flac: svg("M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-6a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"),
    lock: svg("M7 11V8a5 5 0 0 1 10 0v3M5 11h14v10H5z"),
    download: svg("M12 4v11m0 0 4.5-4.5M12 15l-4.5-4.5M5 19h14"),
    error: svg("M12 8v5m0 3.5v.01M12 3l9.5 17h-19L12 3Z"),
  };

  const state = { collection: null, group: "best", format: "best_mp3", jobId: null, timer: null, items: [] };

  // ---------- helpers ----------
  const fa = (v) => String(v).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[d]);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function duration(sec) {
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
    const pad = (n) => String(n).padStart(2, "0");
    return fa(h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`);
  }
  function csrf() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }
  async function api(url, body) {
    const opts = body
      ? { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() }, body: JSON.stringify(body) }
      : {};
    let res;
    try {
      res = await fetch(url, opts);
    } catch {
      throw new Error("ارتباط با سرور برقرار نشد.");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "خطای سرور. دوباره امتحان کن.");
    return data;
  }
  function showError(msg) {
    $("formError").textContent = msg;
    $("formError").hidden = !msg;
  }
  function setLoading(btn, on) {
    btn.classList.toggle("loading", on);
    btn.disabled = on;
  }
  const selectedQuality = () => (state.group === "best" ? state.format : state.group);

  // ---------- resolve ----------
  async function resolve(url) {
    showError("");
    setLoading($("resolveBtn"), true);
    try {
      const data = await api("/api/resolve", { url });
      stopPolling();
      state.collection = data.collection;
      state.jobId = null;
      renderCollection(data.collection);
      renderQualities(data.qualities);
      $("hero").classList.add("compact");
      $("features").hidden = true;
      $("result").hidden = false;
      $("qualityCard").hidden = false;
      $("progressCard").hidden = true;
      $("result").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      showError(err.message);
    } finally {
      setLoading($("resolveBtn"), false);
    }
  }

  function renderCollection(c) {
    const cover = c.cover_url || "";
    $("cover").src = cover;
    $("cover").alt = c.title;
    const bg = $("backdropCover");
    bg.style.backgroundImage = cover ? `url("${cover}")` : "";
    bg.classList.toggle("on", !!cover);
    document.documentElement.style.setProperty("--tint", c.color || "#1d6b52");

    $("kind").textContent = KIND[c.kind] || "";
    $("title").textContent = c.title;
    $("subtitle").textContent = c.subtitle;

    const chips = [];
    if (c.kind === "track") {
      const t = c.tracks[0];
      if (t.album) chips.push(esc(t.album) + (t.release_date ? " · " + fa(t.release_date.slice(0, 4)) : ""));
      chips.push(duration(t.duration_s));
      if (t.explicit) chips.push('<span class="explicit">🅴 Explicit</span>');
    } else {
      chips.push(`${fa(c.tracks.length)} ترک`);
      chips.push(duration(c.duration_s));
    }
    $("meta").innerHTML = chips.map((h) => `<span class="chip">${h}</span>`).join("");

    $("truncWarn").hidden = !c.truncated;
    if (c.truncated) {
      $("truncWarn").textContent = `این پلی‌لیست ${fa(c.total)} ترک داره؛ فقط ${fa(c.tracks.length)} ترک اول دریافت میشه.`;
    }

    $("tracks").innerHTML = c.tracks.map((t, i) => `
      <li class="track" data-index="${i + 1}">
        <span class="track-num">${fa(i + 1)}</span>
        <div class="track-main">
          <div class="track-title">${esc(t.title)}</div>
          <div class="track-artist">${esc(t.artist)}</div>
          <div class="track-detail" hidden></div>
        </div>
        <div class="track-side">
          <span class="track-time">${duration(t.duration_s)}</span>
          <span class="status"></span>
        </div>
      </li>`).join("");
  }

  function renderQualities(groups) {
    const grid = $("qgrid");
    grid.innerHTML = groups.map((g) => `
      <button type="button" class="qopt" role="radio" data-group="${g.group}" ${g.available ? "" : "disabled"}
              title="${g.available ? "" : "برای این کیفیت باید یک منبع واقعی (lossless) تنظیم بشه"}">
        <span class="qopt-icon">${ICONS[g.group] || ""}</span>
        <b>${esc(g.label)}</b>
        <small>${g.available ? esc(g.hint) : "منبع واقعی تنظیم نشده"}</small>
        ${g.available ? "" : `<span class="lock">${ICONS.lock}</span>`}
      </button>`).join("");
    const firstAvailable = groups.find((g) => g.available && g.group === state.group) || groups.find((g) => g.available);
    selectGroup(firstAvailable ? firstAvailable.group : null);
  }

  function selectGroup(group) {
    state.group = group;
    document.querySelectorAll(".qopt").forEach((b) => {
      const on = b.dataset.group === group;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", on);
    });
    $("formats").hidden = group !== "best";
    $("startBtn").disabled = !group;
  }

  // ---------- job ----------
  async function start() {
    const btn = $("startBtn");
    setLoading(btn, true);
    try {
      const { id } = await api("/api/jobs", { url: state.collection.url, quality: selectedQuality() });
      state.jobId = id;
      $("qualityCard").hidden = true;
      $("progressCard").hidden = false;
      $("progressCard").classList.add("working");
      $("downloadAll").hidden = true;
      $("downloadAll").textContent = state.collection.tracks.length > 1 ? "دانلود همه" : "دانلود فایل";
      document.querySelectorAll(".track .status").forEach((s) => (s.className = "status pending"));
      poll();
    } catch (err) {
      showError(err.message);
      $("hero").scrollIntoView({ behavior: "smooth" });
    } finally {
      setLoading(btn, false);
    }
  }

  function stopPolling() {
    clearTimeout(state.timer);
    state.timer = null;
  }

  async function poll() {
    const jobId = state.jobId;
    try {
      const job = await api(`/api/jobs/${jobId}`);
      if (jobId !== state.jobId) return;
      renderJob(job);
      if (!job.finished) state.timer = setTimeout(poll, POLL_MS);
    } catch {
      state.timer = setTimeout(poll, POLL_MS * 2);
    }
  }

  function renderJob(job) {
    state.items = job.items;
    const { done, error } = job.counts;
    const finishedCount = done + error;
    const pct = job.total ? Math.round((100 * finishedCount) / job.total) : 0;
    $("bar").style.width = `${Math.max(pct, job.finished ? 100 : 4)}%`;
    $("progressPct").textContent = `${fa(pct)}٪`;
    $("progressCard").classList.toggle("working", !job.finished);
    if (job.finished) {
      $("progressText").textContent = done
        ? `آماده‌ست! ${fa(done)} از ${fa(job.total)} فایل · ${job.quality_label}`
        : "متأسفانه هیچ فایلی دریافت نشد.";
    } else {
      $("progressText").textContent = `در حال دانلود ${fa(finishedCount)} از ${fa(job.total)} · ${job.quality_label}`;
    }
    $("downloadAll").hidden = done === 0;

    for (const item of job.items) {
      const row = document.querySelector(`.track[data-index="${item.index}"]`);
      if (!row || row.dataset.status === item.status) continue;
      row.dataset.status = item.status;
      row.classList.toggle("done", item.status === "done");
      const detail = row.querySelector(".track-detail");
      let status = row.querySelector(".status");

      if (item.status === "done") {
        const link = document.createElement("a");
        link.className = "status done";
        link.href = item.download_url;
        link.setAttribute("download", item.filename);
        link.title = `دانلود ${item.filename}`;
        link.innerHTML = ICONS.download;
        status.replaceWith(link);
        // This line is LTR: file format, then the real source quality.
        const extras = [item.label, `from ${item.source}`];
        if (item.lyrics) extras.push(item.lyrics === "synced" ? "synced lyrics" : "lyrics");
        extras.push(`${(item.size / 1048576).toFixed(1)} MB`);
        detail.className = "track-detail";
        detail.textContent = extras.join(" · ");
        detail.hidden = false;
      } else if (item.status === "error") {
        status.className = "status error";
        status.innerHTML = ICONS.error;
        status.title = item.error;
        detail.className = "track-detail error";
        detail.textContent = item.error;
        detail.hidden = false;
      } else {
        status.className = `status ${item.status}`;
        status.innerHTML = item.status === "working" ? '<i class="spinner"></i>' : "";
      }
    }
  }

  async function downloadAll() {
    const btn = $("downloadAll");
    btn.disabled = true;
    for (const item of state.items.filter((i) => i.status === "done")) {
      const a = document.createElement("a");
      a.href = item.download_url;
      a.download = item.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      await sleep(800);
    }
    btn.disabled = false;
  }

  function reset() {
    stopPolling();
    state.jobId = null;
    $("result").hidden = true;
    $("features").hidden = false;
    $("hero").classList.remove("compact");
    $("backdropCover").classList.remove("on");
    $("linkInput").value = "";
    $("linkInput").focus();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ---------- events ----------
  $("linkForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const url = $("linkInput").value.trim();
    if (url) resolve(url);
  });
  $("linkInput").addEventListener("paste", () => {
    setTimeout(() => {
      if (/spotify/i.test($("linkInput").value)) $("linkForm").requestSubmit();
    }, 0);
  });
  $("pasteBtn").addEventListener("click", async () => {
    try {
      const text = (await navigator.clipboard.readText()).trim();
      $("linkInput").value = text;
      if (/spotify/i.test(text)) $("linkForm").requestSubmit();
    } catch {
      showError("دسترسی به کلیپ‌بورد داده نشد؛ لینک رو دستی بچسبون.");
      $("linkInput").focus();
    }
  });
  $("qgrid").addEventListener("click", (e) => {
    const btn = e.target.closest(".qopt");
    if (btn && !btn.disabled) selectGroup(btn.dataset.group);
  });
  document.querySelectorAll(".segmented button").forEach((b) =>
    b.addEventListener("click", () => {
      state.format = b.dataset.format;
      document.querySelectorAll(".segmented button").forEach((x) => x.classList.toggle("active", x === b));
    })
  );
  $("startBtn").addEventListener("click", start);
  $("downloadAll").addEventListener("click", downloadAll);
  $("newLink").addEventListener("click", reset);
})();
