/* Theater: library catalogue, detail sheet, and a controls-first player.
   Every field from the API is treated as optional; art falls back to the
   generated SVG endpoints. No transcoding happens here. */

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const state = {
    items: [],
    detail: null,
    detailItem: null,
    detailFocus: null,
    playerFocus: null,
    player: {
      item: null,
      detail: null,
      tracks: { audio: [], subtitles: [] },
      session: null,
      hls: null,
      audioIndex: 0,
      subIndex: null,
      offset: 0,
      hlsMode: false,
      seekTarget: null,
      seekTimer: null,
      sessionSeq: 0,
      sessionChain: Promise.resolve(),
      token: 0,
      error: false,
      closing: false,
    },
  };

  const detailCache = new Map();
  const shell = document.querySelector(".shell");

  const els = {
    loading: $("loading"),
    content: $("content"),
    rails: $("rails"),
    empty: $("empty"),
    noMatch: $("no-match"),
    fatal: $("fatal"),
    fatalReason: $("fatal-reason"),
    filter: $("filter"),
    railCount: $("rail-count"),
    hero: $("hero"),
    heroArt: $("hero-art"),
    heroTitle: $("hero-title"),
    heroFacts: $("hero-facts"),
    heroGenres: $("hero-genres"),
    heroOverview: $("hero-overview"),
    heroRatings: $("hero-ratings"),
    heroPlay: $("hero-play"),
    heroTrailer: $("hero-trailer"),
    heroDetail: $("hero-detail"),
    detailLayer: $("detail-layer"),
    detail: $("detail"),
    detailArt: $("detail-art"),
    detailTitle: $("detail-title"),
    detailMeta: $("detail-meta"),
    detailRatings: $("detail-ratings"),
    detailGenres: $("detail-genres"),
    detailPoster: $("detail-poster"),
    detailOverview: $("detail-overview"),
    detailPeople: $("detail-people"),
    detailTrailer: $("detail-trailer"),
    detailFacts: $("detail-facts"),
    detailTracks: $("detail-tracks"),
    detailAudio: $("detail-audio"),
    detailSubs: $("detail-subs"),
    detailNote: $("detail-note"),
    detailPlay: $("detail-play"),
    detailBack: $("detail-back"),
    player: $("player"),
    video: $("video"),
    playerTitle: $("player-title"),
    playerSub: $("player-sub"),
    playToggle: $("play-toggle"),
    playIcon: $("play-icon"),
    back10: $("back10"),
    fwd10: $("fwd10"),
    cur: $("cur"),
    dur: $("dur"),
    timeline: $("timeline"),
    timelineBuffer: $("timeline-buffer"),
    timelineFill: $("timeline-fill"),
    timelineThumb: $("timeline-thumb"),
    volume: $("volume"),
    mute: $("mute"),
    muteIcon: $("mute-icon"),
    speed: $("speed"),
    audio: $("audio"),
    audioWrap: $("audio-wrap"),
    subs: $("subs"),
    subsWrap: $("subs-wrap"),
    aspect: $("aspect"),
    pip: $("pip"),
    fullscreen: $("fullscreen"),
    closePlayer: $("close-player"),
    buffering: $("buffering"),
    shortcuts: $("shortcuts"),
    playerError: $("player-error"),
    playerErrorReason: $("player-error-reason"),
    playerFallback: $("player-fallback"),
    retry: $("retry"),
  };

  /* ---------- formatting ---------- */

  function clock(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }

  function runtime(seconds) {
    const total = Math.floor(Number(seconds) || 0);
    if (!total) return "";
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    if (!h) return `${m}m`;
    return `${h}h ${String(m).padStart(2, "0")}m`;
  }

  function bytes(n) {
    const value = Number(n);
    if (!Number.isFinite(value) || value <= 0) return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = value;
    let i = 0;
    while (size >= 1024 && i < units.length - 1) { size /= 1024; i += 1; }
    return `${size < 10 && i > 0 ? size.toFixed(1) : Math.round(size)} ${units[i]}`;
  }

  function codec(value) {
    return typeof value === "string" && value ? value.toUpperCase() : "";
  }

  function kindWord(item) {
    return item.kind === "series" ? "Series" : "Movie";
  }

  function text(value) {
    return typeof value === "string" && value.trim() ? value.trim() : null;
  }

  function strings(value) {
    return Array.isArray(value)
      ? value.filter((entry) => typeof entry === "string" && entry.trim()).map((entry) => entry.trim())
      : [];
  }

  function rating(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : null;
  }

  function metaLine(item) {
    const parts = [];
    if (item.year) parts.push(String(item.year));
    const length = runtime(item.duration);
    if (length) parts.push(length);
    if (item.certification) parts.push(item.certification);
    return parts.join(" · ");
  }

  function ratingEntries(item) {
    const entries = [];
    if (item.tmdbRating) entries.push(["TMDB", item.tmdbRating.toFixed(1)]);
    if (item.imdbRating) entries.push(["IMDb", item.imdbRating.toFixed(1)]);
    if (item.rtRating) entries.push(["RT", item.rtRating]);
    return entries;
  }

  function badgeRating(item) {
    const value = item.imdbRating || item.tmdbRating;
    return value ? value.toFixed(1) : "";
  }

  function renderRatings(node, item) {
    const entries = ratingEntries(item);
    node.replaceChildren(...entries.map(([label, value]) => {
      const entry = document.createElement("span");
      entry.className = "rating";
      const name = document.createElement("span");
      name.className = "rating-label";
      name.textContent = label;
      const score = document.createElement("span");
      score.className = "rating-value";
      score.textContent = value;
      entry.append(name, score);
      return entry;
    }));
    node.hidden = entries.length === 0;
  }

  function renderGenres(node, item) {
    node.replaceChildren(...item.genres.map((genre) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = genre;
      return chip;
    }));
    node.hidden = item.genres.length === 0;
  }

  function renderTrailer(node, id) {
    if (!id) {
      node.removeAttribute("href");
      node.hidden = true;
      return;
    }
    node.href = `https://www.youtube.com/watch?v=${encodeURIComponent(id)}`;
    node.hidden = false;
  }

  function renderPeople(item) {
    const rows = [
      fact("Director", item.director.join(", ")),
      fact("Cast", item.cast.join(", ")),
    ].filter(Boolean);
    els.detailPeople.replaceChildren(...rows);
    els.detailPeople.hidden = rows.length === 0;
  }

  function trackName(track, index) {
    const name = track.title || track.lang || `Track ${index + 1}`;
    const extras = [];
    if (track.codec) extras.push(track.codec.toUpperCase());
    if (track.channels) extras.push(`${track.channels} ch`);
    return extras.length ? `${name} · ${extras.join(" · ")}` : name;
  }

  /* ---------- API ---------- */

  async function failure(response) {
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string") return body.detail;
    } catch { /* body is not JSON */ }
    return `Request failed (${response.status})`;
  }

  function normalizeItem(value) {
    if (!value || typeof value !== "object") return null;
    const id = typeof value.id === "string" ? value.id : "";
    if (!id) return null;
    const year = Number(value.year);
    const duration = Number(value.duration);
    const size = Number(value.size);
    const audioTracks = Number(value.audioTracks);
    const subtitleTracks = Number(value.subtitleTracks);
    return {
      id,
      title: typeof value.title === "string" && value.title.trim() ? value.title.trim() : "Untitled",
      year: Number.isFinite(year) && year > 1800 ? year : null,
      kind: value.kind === "series" ? "series" : "movie",
      size: Number.isFinite(size) && size > 0 ? size : null,
      mtime: Number.isFinite(Number(value.mtime)) ? Number(value.mtime) : null,
      duration: Number.isFinite(duration) && duration > 0 ? duration : null,
      container: typeof value.container === "string" ? value.container.toLowerCase() : "",
      videoCodec: typeof value.videoCodec === "string" ? value.videoCodec.toLowerCase() : "",
      audioTracks: Number.isFinite(audioTracks) && audioTracks > 0 ? Math.floor(audioTracks) : 0,
      subtitleTracks: Number.isFinite(subtitleTracks) && subtitleTracks > 0 ? Math.floor(subtitleTracks) : 0,
      poster: typeof value.poster === "string" && value.poster ? value.poster : null,
      backdrop: typeof value.backdrop === "string" && value.backdrop ? value.backdrop : null,
      description: text(value.description),
      genres: strings(value.genres),
      tmdbRating: rating(value.tmdbRating),
      imdbRating: rating(value.imdbRating),
      rtRating: text(value.rtRating),
      certification: text(value.certification),
      director: strings(value.director),
      cast: strings(value.cast),
      trailer: text(value.trailer),
      playableDirect: value.playableDirect === true,
    };
  }

  function normalizeTrack(value, index) {
    const track = value && typeof value === "object" ? value : {};
    const given = Number(track.index);
    const channels = Number(track.channels);
    return {
      index: Number.isInteger(given) && given >= 0 ? given : index,
      lang: typeof track.lang === "string" ? track.lang : "",
      title: typeof track.title === "string" ? track.title : "",
      codec: typeof track.codec === "string" ? track.codec : "",
      channels: Number.isFinite(channels) && channels > 0 ? channels : 0,
    };
  }

  function fallbackTracks(item) {
    return {
      audio: Array.from({ length: item.audioTracks }, (_, index) => normalizeTrack({}, index)),
      subtitles: Array.from({ length: item.subtitleTracks }, (_, index) => normalizeTrack({}, index)),
    };
  }

  async function fetchDetail(item) {
    if (detailCache.has(item.id)) return detailCache.get(item.id);
    const response = await fetch(`/api/media/${encodeURIComponent(item.id)}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(await failure(response));
    const data = await response.json();
    const body = data && typeof data === "object" ? data : {};
    const base = normalizeItem(body.item) || item;
    const tracks = body.tracks && typeof body.tracks === "object" ? body.tracks : {};
    const fallback = fallbackTracks(base);
    const detail = {
      item: base,
      tracks: {
        audio: Array.isArray(tracks.audio)
          ? tracks.audio.map(normalizeTrack)
          : fallback.audio,
        subtitles: Array.isArray(tracks.subtitles)
          ? tracks.subtitles.map(normalizeTrack)
          : fallback.subtitles,
      },
      directUrl: typeof body.directUrl === "string" && body.directUrl
        ? body.directUrl
        : `/api/stream/${encodeURIComponent(item.id)}`,
      hlsUrl: typeof body.hlsUrl === "string" && body.hlsUrl ? body.hlsUrl : null,
    };
    detailCache.set(item.id, detail);
    return detail;
  }

  /* ---------- art ---------- */

  function posterFallback(item) {
    return `/api/poster/${encodeURIComponent(item.id)}.svg`;
  }

  function backdropFallback(item) {
    return `/api/backdrop/${encodeURIComponent(item.id)}.svg`;
  }

  function art(img, preferred, fallback, alt) {
    img.alt = alt || "";
    img.onerror = () => {
      img.onerror = null;
      img.src = fallback;
    };
    img.src = preferred || fallback;
  }

  /* ---------- loading ---------- */

  function setLoading(on) {
    els.loading.hidden = !on;
    els.content.hidden = on;
  }

  function showFatal(message) {
    setLoading(false);
    els.content.hidden = true;
    els.fatal.hidden = false;
    els.fatalReason.textContent = message || "The server did not answer.";
  }

  async function loadLibrary() {
    els.fatal.hidden = true;
    setLoading(true);
    els.empty.hidden = true;
    els.noMatch.hidden = true;
    try {
      const response = await fetch("/api/library", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(await failure(response));
      const data = await response.json();
      const raw = data && Array.isArray(data.items) ? data.items : [];
      const items = raw.map(normalizeItem).filter(Boolean);
      if (items.some((item) => item.mtime !== null)) {
        items.sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
      }
      state.items = items;
      renderLibrary();
    } catch (error) {
      showFatal(error && error.message ? error.message : "The server did not answer.");
    }
  }

  function renderLibrary() {
    setLoading(false);
    els.content.hidden = false;
    els.railCount.textContent = state.items.length
      ? `${state.items.length} title${state.items.length === 1 ? "" : "s"}`
      : "Local library";

    if (!state.items.length) {
      els.hero.hidden = true;
      els.rails.replaceChildren();
      els.empty.hidden = false;
      els.noMatch.hidden = true;
      return;
    }

    els.empty.hidden = true;
    renderHero(state.items[0]);
    renderRails(els.filter.value.trim());
  }

  function renderHero(item) {
    els.hero.hidden = false;
    art(els.heroArt, item.backdrop, backdropFallback(item));
    els.heroTitle.textContent = item.title;
    els.heroFacts.textContent = metaLine(item);
    renderGenres(els.heroGenres, item);
    els.heroOverview.textContent = item.description || "";
    els.heroOverview.hidden = !item.description;
    renderRatings(els.heroRatings, item);
    renderTrailer(els.heroTrailer, item.trailer);
    els.heroPlay.onclick = () => playItem(item, { audio: 0, subtitle: null });
    els.heroDetail.onclick = () => openDetail(item);
  }

  /* ---------- rails ---------- */

  function dedupe(items) {
    const seen = new Set();
    return items.filter((item) => {
      const key = `${item.title.toLowerCase()}::${item.year || ""}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function card(item) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "card";
    button.setAttribute("aria-label", `Open ${item.title}${item.year ? `, ${item.year}` : ""}`);

    const poster = document.createElement("span");
    poster.className = "poster";
    const img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    art(img, item.poster, posterFallback(item));
    poster.appendChild(img);

    const badge = badgeRating(item);
    if (badge) {
      const score = document.createElement("span");
      score.className = "card-rating";
      score.textContent = badge;
      poster.appendChild(score);
    }

    const title = document.createElement("span");
    title.className = "card-title";
    title.textContent = item.title;

    const sub = document.createElement("span");
    sub.className = "card-sub";
    const subParts = [];
    if (item.year) subParts.push(String(item.year));
    if (item.duration) subParts.push(runtime(item.duration));
    if (!subParts.length) subParts.push(kindWord(item));
    sub.textContent = subParts.join(" · ");

    button.append(poster, title, sub);
    button.addEventListener("click", () => openDetail(item));
    return button;
  }

  function railSection(title, items, query) {
    const list = query
      ? items.filter((item) => item.title.toLowerCase().includes(query))
      : items;
    if (!list.length) return null;

    const section = document.createElement("section");
    section.className = "rail-section";
    section.setAttribute("aria-label", title);

    const head = document.createElement("div");
    head.className = "rail-head";
    const heading = document.createElement("h2");
    heading.textContent = title;
    const count = document.createElement("span");
    count.textContent = String(list.length);
    head.append(heading, count);

    const track = document.createElement("div");
    track.className = "rail-track";
    track.append(...list.map(card));

    section.append(head, track);
    return section;
  }

  function renderRails(rawQuery) {
    const query = String(rawQuery || "").trim().toLowerCase();
    const sections = [
      ["Recently added", dedupe(state.items).slice(0, 16)],
      ["Movies", dedupe(state.items.filter((item) => item.kind === "movie"))],
      ["Series", dedupe(state.items.filter((item) => item.kind === "series"))],
    ];

    const nodes = [];
    let matches = 0;
    for (const [title, items] of sections) {
      if (query) matches += items.filter((item) => item.title.toLowerCase().includes(query)).length;
      const node = railSection(title, items, query);
      if (node) nodes.push(node);
    }

    els.rails.replaceChildren(...nodes);
    if (query && matches === 0) {
      els.noMatch.hidden = false;
      els.noMatch.textContent = `No titles match “${rawQuery.trim()}”.`;
    } else {
      els.noMatch.hidden = true;
    }
  }

  /* ---------- detail ---------- */

  function fact(label, value) {
    if (!value) return null;
    const row = document.createElement("div");
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = value;
    row.append(term, description);
    return row;
  }

  function fillTrackSelect(select, tracks, kind, selected) {
    select.replaceChildren();
    if (kind === "subtitles") {
      const off = document.createElement("option");
      off.value = "off";
      off.textContent = "Off";
      select.appendChild(off);
    }
    tracks.forEach((track, index) => {
      const option = document.createElement("option");
      option.value = String(track.index);
      option.textContent = trackName(track, index);
      select.appendChild(option);
    });
    select.value = selected === null || selected === undefined
      ? (kind === "subtitles" ? "off" : (tracks[0] ? String(tracks[0].index) : "0"))
      : String(selected);
  }

  function renderDetailTracks(tracks) {
    const audio = Array.isArray(tracks.audio) ? tracks.audio : [];
    const subtitles = Array.isArray(tracks.subtitles) ? tracks.subtitles : [];

    fillTrackSelect(els.detailAudio, audio, "audio", audio[0] ? audio[0].index : 0);
    fillTrackSelect(els.detailSubs, subtitles, "subtitles", null);

    els.detailAudio.parentElement.hidden = audio.length <= 1;
    els.detailSubs.parentElement.hidden = subtitles.length === 0;
    els.detailTracks.hidden = els.detailAudio.parentElement.hidden
      && els.detailSubs.parentElement.hidden;
  }

  function renderDetailInfo(item) {
    els.detailTitle.textContent = item.title;
    els.detailMeta.textContent = metaLine(item);
    renderRatings(els.detailRatings, item);
    renderGenres(els.detailGenres, item);
    els.detailOverview.textContent = item.description || "";
    els.detailOverview.hidden = !item.description;
    renderPeople(item);
    renderTrailer(els.detailTrailer, item.trailer);
    art(els.detailArt, item.backdrop, backdropFallback(item));
    art(els.detailPoster, item.poster, posterFallback(item));
  }

  function renderDetailFacts(base) {
    const facts = [
      fact("Year", base.year ? String(base.year) : ""),
      fact("Runtime", runtime(base.duration)),
      fact("Size", bytes(base.size)),
      fact("Container", codec(base.container)),
      fact("Video", codec(base.videoCodec)),
      fact("Audio", base.audioTracks ? `${base.audioTracks} track${base.audioTracks === 1 ? "" : "s"}` : ""),
      fact("Subtitles", base.subtitleTracks ? `${base.subtitleTracks} track${base.subtitleTracks === 1 ? "" : "s"}` : ""),
    ].filter(Boolean);
    els.detailFacts.replaceChildren(...facts);
    els.detailFacts.hidden = facts.length === 0;
  }

  async function openDetail(item) {
    state.detailItem = item;
    state.detail = null;
    state.detailFocus = document.activeElement;

    const cached = detailCache.get(item.id);
    const base = cached ? cached.item : item;

    renderDetailInfo(base);
    renderDetailFacts(base);

    els.detailNote.hidden = true;
    els.detailLayer.hidden = false;
    syncLock();

    const tracks = cached ? cached.tracks : fallbackTracks(base);
    renderDetailTracks(tracks);

    els.detailPlay.onclick = () => {
      const audioValue = els.detailAudio.value === "" ? 0 : Number(els.detailAudio.value);
      const subValue = els.detailSubs.value === "off" || els.detailSubs.value === ""
        ? null
        : Number(els.detailSubs.value);
      playItem(base, {
        audio: Number.isInteger(audioValue) ? audioValue : 0,
        subtitle: Number.isInteger(subValue) ? subValue : null,
      });
    };

    els.detail.focus({ preventScroll: true });
    history.pushState({ shelf: "detail", id: item.id }, "");

    if (cached) return;

    try {
      const detail = await fetchDetail(item);
      if (state.detailItem !== item || els.detailLayer.hidden) return;
      state.detail = detail;
      renderDetailInfo(detail.item);
      renderDetailFacts(detail.item);
      renderDetailTracks(detail.tracks);
    } catch (error) {
      if (state.detailItem !== item || els.detailLayer.hidden) return;
      els.detailNote.hidden = false;
      els.detailNote.textContent = `Track details are unavailable. ${error && error.message ? error.message : ""}`.trim();
    }
  }

  function closeDetail() {
    if (els.detailLayer.hidden) return;
    els.detailLayer.hidden = true;
    state.detailItem = null;
    state.detail = null;
    syncLock();
    const focus = state.detailFocus;
    state.detailFocus = null;
    if (focus && typeof focus.focus === "function") focus.focus({ preventScroll: true });
  }

  function requestCloseDetail() {
    if (history.state && history.state.shelf === "detail") history.back();
    else closeDetail();
  }

  /* ---------- player ---------- */

  function playerDuration() {
    const player = state.player;
    const itemDuration = player.item && Number.isFinite(player.item.duration) ? player.item.duration : 0;
    if (player.hlsMode) return itemDuration;
    const native = els.video.duration;
    if (Number.isFinite(native) && native > 0) return native;
    return itemDuration;
  }

  function clampPosition(value) {
    const duration = playerDuration();
    const target = Math.max(0, Number(value) || 0);
    return duration > 0 ? Math.min(duration, target) : target;
  }

  function playerPosition() {
    const player = state.player;
    if (player.seekTarget !== null) return player.seekTarget;
    const current = Number.isFinite(els.video.currentTime) ? els.video.currentTime : 0;
    return Math.max(0, player.offset + current);
  }

  function seekBy(seconds) {
    const player = state.player;
    if (!playerDuration()) return;
    const target = clampPosition(playerPosition() + seconds);
    if (player.hlsMode) {
      player.seekTarget = target;
      commitHlsSeek();
      return;
    }
    els.video.currentTime = target;
    renderProgress();
    poke();
  }

  function commitHlsSeek() {
    const player = state.player;
    window.clearTimeout(player.seekTimer);
    player.seekTimer = null;
    if (!player.hlsMode || player.seekTarget === null) return;
    player.seekTarget = clampPosition(player.seekTarget);
    els.video.pause();
    setBuffering(true);
    renderProgress();
    renderBuffered();
    startSession(player.audioIndex, player.seekTarget, true);
  }

  function scheduleHlsSeek() {
    const player = state.player;
    if (!player.hlsMode || player.seekTarget === null) return;
    window.clearTimeout(player.seekTimer);
    player.seekTimer = window.setTimeout(() => {
      player.seekTimer = null;
      commitHlsSeek();
    }, 140);
  }

  function setIcon(svg, path, filled) {
    svg.replaceChildren();
    const shape = document.createElementNS("http://www.w3.org/2000/svg", "path");
    shape.setAttribute("d", path);
    svg.appendChild(shape);
    svg.style.fill = filled ? "currentColor" : "none";
    svg.style.stroke = filled ? "none" : "currentColor";
  }

  function showPlayerError(message) {
    const player = state.player;
    if (player.closing || els.player.hidden) return;
    player.error = true;
    els.buffering.hidden = true;
    els.playerError.hidden = false;
    els.playerErrorReason.textContent = message || "The stream could not be opened.";
    els.playerFallback.href = player.detail && player.detail.directUrl
      ? player.detail.directUrl
      : `/api/stream/${encodeURIComponent(player.item ? player.item.id : "")}`;
    els.shortcuts.hidden = true;
    els.player.dataset.idle = "false";
    showControls();
  }

  function clearPlayerError() {
    state.player.error = false;
    els.playerError.hidden = true;
  }

  function setBuffering(on) {
    els.buffering.hidden = !on;
  }

  function resetVideo() {
    const video = els.video;
    video.pause();
    video.removeAttribute("src");
    video.querySelectorAll("track").forEach((track) => track.remove());
    video.load();
  }

  function mountSubtitles(index) {
    const video = els.video;
    const item = state.player.item;
    video.querySelectorAll("track").forEach((track) => track.remove());
    state.player.subIndex = Number.isInteger(index) ? index : null;
    if (!item || !Number.isInteger(index)) return;

    const tracks = state.player.tracks.subtitles || [];
    const found = tracks.find((track) => track.index === index);
    const track = document.createElement("track");
    track.kind = "subtitles";
    track.label = found ? (found.title || found.lang || `Subtitle ${index + 1}`) : `Subtitle ${index + 1}`;
    track.srclang = found && found.lang ? found.lang : "und";
    track.src = `/api/subtitles/${encodeURIComponent(item.id)}/${index}.vtt`;
    track.default = true;
    track.addEventListener("load", () => {
      if (track.track) track.track.mode = "showing";
    }, { once: true });
    video.appendChild(track);
    if (track.track) track.track.mode = "showing";
  }

  function fillMenus() {
    const player = state.player;
    const tracks = player.tracks;

    els.audio.replaceChildren();
    tracks.audio.forEach((track, index) => {
      const option = document.createElement("option");
      option.value = String(track.index);
      option.textContent = trackName(track, index);
      els.audio.appendChild(option);
    });
    const audioCount = tracks.audio.length || (player.item ? player.item.audioTracks : 0);
    els.audioWrap.hidden = audioCount <= 1;
    if (tracks.audio.length) els.audio.value = String(player.audioIndex);

    els.subs.replaceChildren();
    const off = document.createElement("option");
    off.value = "off";
    off.textContent = "Off";
    els.subs.appendChild(off);
    tracks.subtitles.forEach((track, index) => {
      const option = document.createElement("option");
      option.value = String(track.index);
      option.textContent = trackName(track, index);
      els.subs.appendChild(option);
    });
    const subCount = tracks.subtitles.length || (player.item ? player.item.subtitleTracks : 0);
    els.subsWrap.hidden = subCount === 0;
    els.subs.value = Number.isInteger(player.subIndex) ? String(player.subIndex) : "off";
  }

  function startDirect(item, prefs) {
    const player = state.player;
    const video = els.video;
    const source = player.detail ? player.detail.directUrl : `/api/stream/${encodeURIComponent(item.id)}`;
    video.src = source;
    video.load();
    video.playbackRate = Number(els.speed.value) || 1;
    mountSubtitles(Number.isInteger(prefs.subtitle) ? prefs.subtitle : null);
    video.play().catch(() => {
      setBuffering(false);
      renderPlayState();
    });
  }

  function startSession(audio, start, autoplay = true) {
    const player = state.player;
    const seq = player.sessionSeq + 1;
    player.sessionSeq = seq;
    player.sessionChain = player.sessionChain
      .catch(() => {})
      .then(() => openSession(seq, audio, start, autoplay));
    return player.sessionChain;
  }

  async function openSession(seq, audio, start, autoplay) {
    const player = state.player;
    if (seq !== player.sessionSeq || player.closing) return;
    const token = player.token;
    clearPlayerError();
    setBuffering(true);

    const previous = player.session;
    player.session = null;
    if (previous) {
      await fetch(`/api/player/session/${encodeURIComponent(previous)}`, { method: "DELETE" }).catch(() => {});
    }
    if (seq !== player.sessionSeq || token !== player.token || player.closing) return;

    let playlist = "";
    try {
      const response = await fetch("/api/player/session", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          id: player.item.id,
          audio: Number.isInteger(audio) ? audio : 0,
          start: Math.max(0, Number(start) || 0),
        }),
      });
      if (!response.ok) throw new Error(await failure(response));
      const data = await response.json();
      playlist = data && typeof data.playlist === "string" ? data.playlist : "";
      const session = data && typeof data.session === "string" ? data.session : null;
      if (!playlist) throw new Error("The server returned no playlist.");
      if (seq !== player.sessionSeq || token !== player.token || player.closing) {
        if (session) {
          await fetch(`/api/player/session/${encodeURIComponent(session)}`, { method: "DELETE" }).catch(() => {});
        }
        return;
      }
      player.session = session;
      player.offset = Math.max(0, Number(start) || 0);
      player.hlsMode = true;
    } catch (error) {
      if (seq !== player.sessionSeq || token !== player.token || player.closing) return;
      setBuffering(false);
      showPlayerError(error && error.message ? error.message : "The player session could not start.");
      return;
    }

    attachPlaylist(playlist, autoplay, seq);
  }

  function playlistReady(url, seq) {
    const player = state.player;
    const token = player.token;
    const deadline = Date.now() + 25000;
    return (async () => {
      while (Date.now() < deadline) {
        if (seq !== player.sessionSeq || token !== player.token || player.closing) return false;
        try {
          const response = await fetch(url, { cache: "no-store" });
          if (response.ok) {
            const text = await response.text();
            if (text.includes("#EXTINF")) return true;
          }
        } catch { /* session is still writing the playlist */ }
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      return false;
    })();
  }

  async function attachPlaylist(url, autoplay, seq) {
    const player = state.player;
    const video = els.video;

    // ffmpeg writes the first segment before the playlist exists. Waiting here
    // avoids hls.js backing off into a live-edge start on that first 404.
    await playlistReady(url, seq);
    if (seq !== player.sessionSeq || player.closing) return;

    const settleSeek = () => {
      if (seq !== player.sessionSeq || player.seekTarget === null) return;
      player.seekTarget = null;
      renderProgress();
      renderBuffered();
    };
    video.addEventListener("loadedmetadata", settleSeek, { once: true });
    video.addEventListener("canplay", settleSeek, { once: true });

    if (player.hls) {
      player.hls.destroy();
      player.hls = null;
    }

    const native = video.canPlayType("application/vnd.apple.mpegurl");
    if (window.Hls && window.Hls.isSupported()) {
      const hls = new window.Hls({
        enableWorker: true,
        lowLatencyMode: false,
        startPosition: 0,
        manifestLoadingMaxRetry: 30,
        manifestLoadingRetryDelay: 250,
        manifestLoadingMaxRetryTimeout: 2000,
        levelLoadingMaxRetry: 20,
        levelLoadingRetryDelay: 250,
        levelLoadingMaxRetryTimeout: 2000,
      });
      player.hls = hls;
      hls.on(window.Hls.Events.ERROR, (_event, data) => {
        if (!data || !data.fatal) return;
        const reason = data.details ? `HLS error: ${data.details}` : "HLS playback failed.";
        showPlayerError(reason);
      });
      hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
        setBuffering(false);
        if (autoplay) video.play().catch(() => setBuffering(false));
      });
      hls.loadSource(url);
      hls.attachMedia(video);
      video.playbackRate = Number(els.speed.value) || 1;
      return;
    }

    if (native) {
      video.src = url;
      video.load();
      video.playbackRate = Number(els.speed.value) || 1;
      if (autoplay) video.play().catch(() => setBuffering(false));
      return;
    }

    setBuffering(false);
    showPlayerError("This browser cannot play HLS. Open the direct stream instead.");
  }

  function hevcSupported() {
    const probe = document.createElement("video");
    return Boolean(
      probe.canPlayType('video/mp4; codecs="hvc1.1.6.L120.90"') ||
      probe.canPlayType('video/mp4; codecs="hvc1"') ||
      probe.canPlayType('video/mp4; codecs="hev1"')
    );
  }

  async function playItem(item, prefs) {
    if (!item) return;
    const options = prefs || {};
    const player = state.player;
    player.token += 1;
    const token = player.token;

    state.playerFocus = document.activeElement;
    if (player.session) {
      fetch(`/api/player/session/${encodeURIComponent(player.session)}`, { method: "DELETE" }).catch(() => {});
      player.session = null;
    }
    if (player.hls) {
      player.hls.destroy();
      player.hls = null;
    }
    player.item = item;
    player.detail = null;
    player.session = null;
    player.error = false;
    player.closing = false;
    player.audioIndex = Number.isInteger(options.audio) ? options.audio : 0;
    player.subIndex = Number.isInteger(options.subtitle) ? options.subtitle : null;
    player.tracks = fallbackTracks(item);
    player.offset = 0;
    player.hlsMode = !item.playableDirect || player.audioIndex > 0;
    player.seekTarget = null;

    clearPlayerError();
    setBuffering(true);
    els.player.hidden = false;
    els.player.setAttribute("aria-hidden", "false");
    els.player.tabIndex = -1;
    els.player.dataset.idle = "false";
    els.player.dataset.ratio = "fit";
    els.aspect.value = "fit";
    els.speed.value = "1";
    els.video.playbackRate = 1;
    syncLock();

    if (["hevc", "h265", "hevc1"].includes(item.videoCodec) && !hevcSupported()) {
      els.playerTitle.textContent = item.title;
      els.playerSub.textContent = "H.265 / HEVC";
      showPlayerError("This browser cannot decode HEVC. Use the direct stream link in a native player such as Stremio, VLC or mpv.");
      return;
    }

    els.playerTitle.textContent = item.title;
    els.playerSub.textContent = metaLine(item);

    resetVideo();
    fillMenus();
    renderPlayState();
    renderProgress();
    renderBuffered();
    els.player.focus({ preventScroll: true });
    history.pushState({ shelf: "player", id: item.id }, "");
    showHint();
    poke();

    const cached = detailCache.get(item.id);
    if (cached) {
      player.detail = cached;
      player.tracks = cached.tracks;
      fillMenus();
    } else {
      try {
        const detail = await fetchDetail(item);
        if (token !== player.token) return;
        player.detail = detail;
        player.tracks = detail.tracks;
        fillMenus();
      } catch {
        if (token !== player.token) return;
      }
    }

    if (token !== player.token) return;

    const tracks = player.tracks;
    const wantsSession = !item.playableDirect
      || (player.audioIndex > 0 && tracks.audio.length > 1);
    player.hlsMode = wantsSession;

    if (wantsSession) {
      mountSubtitles(player.subIndex);
      await startSession(player.audioIndex, 0);
    } else {
      startDirect(item, options);
    }
  }

  function teardownPlayer() {
    const player = state.player;
    player.closing = true;
    player.token += 1;
    player.sessionSeq += 1;
    window.clearTimeout(idleTimer);
    window.clearTimeout(hintTimer);
    window.clearTimeout(player.seekTimer);

    if (player.hls) {
      player.hls.destroy();
      player.hls = null;
    }
    if (player.session) {
      fetch(`/api/player/session/${encodeURIComponent(player.session)}`, {
        method: "DELETE",
        keepalive: true,
      }).catch(() => {});
      player.session = null;
    }

    resetVideo();
    setBuffering(false);
    clearPlayerError();
    els.player.hidden = true;
    els.player.setAttribute("aria-hidden", "true");
    els.player.dataset.idle = "false";
    els.shortcuts.hidden = true;
    player.item = null;
    player.detail = null;
    player.tracks = { audio: [], subtitles: [] };
    player.offset = 0;
    player.hlsMode = false;
    player.seekTarget = null;
    player.seekTimer = null;
    player.closing = false;
    syncLock();

    const focus = state.playerFocus;
    state.playerFocus = null;
    if (focus && typeof focus.focus === "function") focus.focus({ preventScroll: true });
  }

  function requestClosePlayer() {
    if (els.player.hidden) return;
    if (history.state && history.state.shelf === "player") history.back();
    else teardownPlayer();
  }

  function syncLock() {
    document.body.classList.toggle("locked", !els.player.hidden || !els.detailLayer.hidden);
    if (shell) shell.inert = !els.player.hidden || !els.detailLayer.hidden;
    els.detailLayer.inert = !els.player.hidden;
  }

  /* ---------- player controls ---------- */

  function renderProgress() {
    const duration = playerDuration();
    const current = duration > 0
      ? Math.min(duration, playerPosition())
      : playerPosition();
    const percent = duration ? Math.min(100, Math.max(0, (current / duration) * 100)) : 0;

    els.timelineFill.style.width = `${percent}%`;
    els.timelineThumb.style.left = `${percent}%`;
    els.cur.textContent = clock(current);
    els.dur.textContent = clock(duration);
    els.timeline.setAttribute("aria-valuemax", String(duration || 0));
    els.timeline.setAttribute("aria-valuenow", current.toFixed(1));
    els.timeline.setAttribute("aria-valuetext", `${clock(current)} of ${clock(duration)}`);
  }

  function renderBuffered() {
    const video = els.video;
    const player = state.player;
    const duration = playerDuration();
    const nodes = [];
    if (duration > 0 && player.seekTarget === null) {
      for (let i = 0; i < video.buffered.length; i += 1) {
        const start = player.offset + video.buffered.start(i);
        const end = player.offset + video.buffered.end(i);
        const left = Math.min(100, Math.max(0, (start / duration) * 100));
        const right = Math.min(100, Math.max(0, (end / duration) * 100));
        if (right <= left) continue;
        const span = document.createElement("span");
        span.style.left = `${left}%`;
        span.style.width = `${right - left}%`;
        nodes.push(span);
      }
    }
    els.timelineBuffer.replaceChildren(...nodes);
  }

  function renderPlayState() {
    const paused = els.video.paused;
    els.playToggle.setAttribute("aria-label", paused ? "Play" : "Pause");
    els.playToggle.title = paused ? "Play" : "Pause";
    setIcon(els.playIcon, paused ? "M8 5.5 18 12 8 18.5z" : "M8.5 5.5v13M15.5 5.5v13", paused);
    if (paused) {
      showControls();
    } else {
      poke();
    }
  }

  function renderVolume() {
    const video = els.video;
    const muted = video.muted || video.volume === 0;
    els.volume.value = String(muted ? 0 : video.volume);
    els.mute.setAttribute("aria-label", muted ? "Unmute" : "Mute");
    els.mute.title = muted ? "Unmute" : "Mute";
    const path = muted
      ? "M4 9.8h3.6L12 6v12l-4.4-3.8H4zM16.5 9.5l4 5M20.5 9.5l-4 5"
      : "M4 9.8h3.6L12 6v12l-4.4-3.8H4zM16 9.5a4 4 0 0 1 0 5M18.5 7a7.5 7.5 0 0 1 0 10";
    setIcon(els.muteIcon, path, false);
  }

  function togglePlay() {
    if (state.player.error) return;
    if (els.video.paused) els.video.play().catch(() => {});
    else els.video.pause();
  }

  function toggleMute() {
    els.video.muted = !els.video.muted;
    if (!els.video.muted && els.video.volume === 0) els.video.volume = 1;
    renderVolume();
  }

  function setVolume(value) {
    const volume = Math.min(1, Math.max(0, Number(value) || 0));
    els.video.volume = volume;
    els.video.muted = volume === 0;
    renderVolume();
  }

  function timelineTarget(event) {
    const rect = els.timeline.getBoundingClientRect();
    const duration = playerDuration();
    if (!rect.width || duration <= 0) return null;
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    return ratio * duration;
  }

  function previewTimeline(event) {
    const target = timelineTarget(event);
    if (target === null) return null;
    const player = state.player;
    if (player.hlsMode) {
      player.seekTarget = target;
      renderProgress();
      renderBuffered();
    } else {
      els.video.currentTime = target;
      renderProgress();
    }
    return target;
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else if (els.player.requestFullscreen) {
      els.player.requestFullscreen().catch(() => {});
    } else if (els.video.webkitEnterFullscreen) {
      els.video.webkitEnterFullscreen();
    }
  }

  function togglePip() {
    if (document.pictureInPictureElement) {
      document.exitPictureInPicture().catch(() => {});
    } else if (els.video.requestPictureInPicture) {
      els.video.requestPictureInPicture().catch(() => {});
    }
  }

  /* ---------- idle controls ---------- */

  let idleTimer = null;

  function showControls() {
    els.player.dataset.idle = "false";
  }

  function poke() {
    if (els.player.hidden || state.player.error) return;
    showControls();
    window.clearTimeout(idleTimer);
    if (els.video.paused) return;
    idleTimer = window.setTimeout(() => {
      if (els.video.paused || state.player.error) return;
      const active = document.activeElement;
      if (active && (active.closest(".player-controls") || active.closest(".player-top"))) return;
      els.player.dataset.idle = "true";
    }, 3000);
  }

  let hintTimer = null;

  function showHint() {
    let seen = false;
    try { seen = sessionStorage.getItem("shelf-theater-hint") === "1"; } catch { seen = false; }
    if (seen) return;
    try { sessionStorage.setItem("shelf-theater-hint", "1"); } catch { /* storage blocked */ }
    els.shortcuts.hidden = false;
    window.clearTimeout(hintTimer);
    hintTimer = window.setTimeout(() => { els.shortcuts.hidden = true; }, 6000);
  }

  /* ---------- keyboard ---------- */

  function keyboard(event) {
    const playerOpen = !els.player.hidden;
    if (!playerOpen) {
      if (event.key === "Escape" && !els.detailLayer.hidden) {
        event.preventDefault();
        requestCloseDetail();
      }
      return;
    }

    const target = event.target;
    const inField = target && typeof target.closest === "function"
      && target.closest("input, select, textarea");
    const onControl = target && typeof target.closest === "function"
      && target.closest("button, a");

    if (event.key === "Escape") {
      event.preventDefault();
      requestClosePlayer();
      return;
    }
    if (inField) return;

    if (event.key === " " || event.key === "k") {
      if (onControl) return;
      event.preventDefault();
      togglePlay();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      seekBy(-10);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      seekBy(10);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setVolume(els.video.volume + 0.05);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setVolume(els.video.volume - 0.05);
    } else if (event.key === "m" || event.key === "M") {
      event.preventDefault();
      toggleMute();
    } else if (event.key === "f" || event.key === "F") {
      event.preventDefault();
      toggleFullscreen();
    }
  }

  /* ---------- wiring ---------- */

  function wire() {
    els.filter.addEventListener("input", () => {
      if (state.items.length) renderRails(els.filter.value);
    });

    els.retry.addEventListener("click", loadLibrary);

    els.detailBack.addEventListener("click", requestCloseDetail);
    els.detailLayer.addEventListener("click", (event) => {
      if (event.target === els.detailLayer) requestCloseDetail();
    });

    els.playToggle.addEventListener("click", togglePlay);
    els.back10.addEventListener("click", () => seekBy(-10));
    els.fwd10.addEventListener("click", () => seekBy(10));
    els.mute.addEventListener("click", toggleMute);
    els.volume.addEventListener("input", () => setVolume(els.volume.value));
    els.closePlayer.addEventListener("click", requestClosePlayer);

    els.speed.addEventListener("change", () => {
      els.video.playbackRate = Number(els.speed.value) || 1;
    });
    els.aspect.addEventListener("change", () => {
      els.player.dataset.ratio = els.aspect.value || "fit";
    });
    els.pip.addEventListener("click", togglePip);
    els.fullscreen.addEventListener("click", toggleFullscreen);

    els.audio.addEventListener("change", () => {
      const index = Number(els.audio.value);
      const player = state.player;
      if (!Number.isInteger(index) || index === player.audioIndex) return;
      const position = playerPosition();
      player.audioIndex = index;
      player.hlsMode = true;
      player.seekTarget = position;
      window.clearTimeout(player.seekTimer);
      player.seekTimer = null;
      els.video.pause();
      setBuffering(true);
      renderProgress();
      renderBuffered();
      startSession(index, position, true);
    });

    els.subs.addEventListener("change", () => {
      const value = els.subs.value;
      mountSubtitles(value === "off" ? null : Number(value));
    });

    els.timeline.addEventListener("pointerdown", (event) => {
      window.clearTimeout(state.player.seekTimer);
      state.player.seekTimer = null;
      els.timeline.dataset.dragging = "true";
      els.timeline.setPointerCapture(event.pointerId);
      previewTimeline(event);
      poke();
    });
    els.timeline.addEventListener("pointermove", (event) => {
      if (els.timeline.dataset.dragging === "true") previewTimeline(event);
    });
    els.timeline.addEventListener("pointerup", () => {
      delete els.timeline.dataset.dragging;
      if (state.player.hlsMode) scheduleHlsSeek();
    });
    els.timeline.addEventListener("pointercancel", () => {
      delete els.timeline.dataset.dragging;
      if (state.player.hlsMode && state.player.seekTimer === null) {
        state.player.seekTarget = null;
        renderProgress();
        renderBuffered();
      }
    });

    els.video.addEventListener("click", togglePlay);
    els.video.addEventListener("play", renderPlayState);
    els.video.addEventListener("pause", renderPlayState);
    els.video.addEventListener("timeupdate", renderProgress);
    els.video.addEventListener("durationchange", () => {
      renderProgress();
      renderBuffered();
    });
    els.video.addEventListener("progress", renderBuffered);
    els.video.addEventListener("loadedmetadata", renderProgress);
    els.video.addEventListener("volumechange", renderVolume);
    els.video.addEventListener("waiting", () => setBuffering(true));
    els.video.addEventListener("playing", () => setBuffering(false));
    els.video.addEventListener("canplay", () => setBuffering(false));
    els.video.addEventListener("ended", showControls);
    els.video.addEventListener("error", () => {
      if (state.player.closing || !els.video.getAttribute("src")) return;
      const code = els.video.error ? els.video.error.code : 0;
      const reasons = {
        1: "Playback was aborted.",
        2: "A network error stopped the stream.",
        3: "This browser cannot decode the video.",
        4: "The stream is not available.",
      };
      showPlayerError(reasons[code] || "The video failed to play.");
    });

    els.player.addEventListener("pointermove", poke);
    els.player.addEventListener("pointerdown", poke);

    document.addEventListener("keydown", keyboard);
    document.addEventListener("fullscreenchange", () => {
      els.fullscreen.setAttribute("aria-pressed", String(Boolean(document.fullscreenElement)));
      els.fullscreen.setAttribute("aria-label", document.fullscreenElement ? "Exit fullscreen" : "Fullscreen");
    });

    if (!document.pictureInPictureEnabled) els.pip.hidden = true;

    window.addEventListener("popstate", () => {
      if (!els.player.hidden) teardownPlayer();
      else if (!els.detailLayer.hidden) closeDetail();
    });

    window.addEventListener("pagehide", () => {
      const session = state.player.session;
      if (!session) return;
      fetch(`/api/player/session/${encodeURIComponent(session)}`, {
        method: "DELETE",
        keepalive: true,
      }).catch(() => {});
    });

    renderVolume();
  }

  wire();
  loadLibrary();
})();
