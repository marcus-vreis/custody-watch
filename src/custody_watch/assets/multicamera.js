"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const colors = ["#6bd5c2", "#b4a2ff", "#efbd80", "#82bcf5"];
  const labels = {not_started: "Não iniciada", observed: "Quadro disponível", missing: "Quadro ausente", stale: "Sem quadro recente", completed: "Gravação concluída", unavailable: "Sem observações"};
  const seconds = value => `${Number(value).toLocaleString("pt-BR", {minimumFractionDigits: 2, maximumFractionDigits: 2})} s`;
  let config, state, selected = null, cursor = 0, generation = 0, controller, timer;
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const svg = (tag, attrs, text) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const keyOf = track => JSON.stringify(track.track_key);
  const colorFor = camera => colors[config.cameras.findIndex(c => c.camera_id === camera.camera_id) % colors.length];

  function renderMap() {
    const floor = config.floors.find(f => f.floor_id === $("floor").value);
    const cameras = config.cameras.filter(c => c.floor_id === floor.floor_id);
    const snapshots = state.cameras.filter(c => c.floor_id === floor.floor_id);
    const points = cameras.flatMap(c => [...(c.coverage_m || []), ...(c.position_m ? [c.position_m] : [])]);
    for (const camera of snapshots) for (const track of [...camera.tracks, ...camera.last_tracks]) {
      if (track.position_m) points.push(track.position_m);
    }
    const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
    const minX = Math.min(0, ...xs) - 1, maxX = Math.max(8, ...xs) + 1;
    const minY = Math.min(0, ...ys) - 1, maxY = Math.max(6, ...ys) + 1;
    const x = value => 52 + (value - minX) / (maxX - minX) * 535;
    const y = value => 365 - (value - minY) / (maxY - minY) * 315;
    const map = $("map");
    map.replaceChildren();
    for (let i = 0; i <= 6; i++) {
      const px = 52 + i * 535 / 6, py = 50 + i * 315 / 6;
      map.append(svg("line", {x1: px, y1: 50, x2: px, y2: 365, stroke: "#253345", "stroke-width": .6}));
      map.append(svg("line", {x1: 52, y1: py, x2: 587, y2: py, stroke: "#253345", "stroke-width": .6}));
      map.append(svg("text", {x: px, y: 388, fill: "#8293a8", "font-size": 10, "text-anchor": "middle"}, (minX + i * (maxX - minX) / 6).toFixed(1)));
      map.append(svg("text", {x: 40, y: py + 3, fill: "#8293a8", "font-size": 10, "text-anchor": "end"}, (maxY - i * (maxY - minY) / 6).toFixed(1)));
    }
    for (const camera of cameras) {
      const color = colorFor(camera), opacity = selected && selected !== camera.camera_id ? .55 : 1;
      if (camera.coverage_m) map.append(svg("polygon", {points: camera.coverage_m.map(p => `${x(p[0])},${y(p[1])}`).join(" "), fill: color, "fill-opacity": .055, stroke: color, "stroke-opacity": opacity * .6, "stroke-dasharray": "5 4", "stroke-width": 1}));
      if (camera.position_m) {
        const px = x(camera.position_m[0]), py = y(camera.position_m[1]);
        const group = svg("g", {role: "button", tabindex: 0, "aria-label": `Selecionar ${camera.camera_id} na planta`, opacity});
        group.append(svg("rect", {x: px - 10, y: py - 8, width: 20, height: 16, rx: 4, fill: "#162531", stroke: color, "stroke-width": 1.5}));
        group.append(svg("path", {d: `M ${px + 10} ${py - 4} l 7 -3 v 14 l -7 -3 Z`, fill: color}));
        group.append(svg("text", {x: px, y: py + 25, fill: color, "font-size": 10, "text-anchor": "middle"}, camera.camera_id));
        const choose = () => { selected = camera.camera_id; render(); };
        group.addEventListener("click", choose);
        group.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(); } });
        map.append(group);
      }
    }
    snapshots.forEach((camera, cameraIndex) => {
      const metadata = cameras.find(c => c.camera_id === camera.camera_id), color = colorFor(metadata);
      const active = new Set(camera.tracks.map(keyOf));
      const tracks = [...camera.tracks.map(track => ({track, old: false}))];
      if ($("history").checked) for (const track of camera.last_tracks) if (!active.has(keyOf(track))) tracks.push({track, old: true});
      for (const {track, old} of tracks) {
        if (!track.position_m) continue;
        const px = x(track.position_m[0]), py = y(track.position_m[1]);
        const group = svg("g", {opacity: selected && selected !== camera.camera_id ? .55 : 1});
        const attrs = {fill: old ? "none" : color, stroke: color, "stroke-width": 1.8, "stroke-dasharray": old ? "3 2" : "none"};
        if (track.cls === "person") group.append(svg("circle", {...attrs, cx: px, cy: py, r: 6 + cameraIndex * 2}));
        else group.append(svg("polygon", {...attrs, points: `${px},${py-7} ${px+7},${py} ${px},${py+7} ${px-7},${py}`}));
        const label = `${camera.camera_id} · #${track.track_key.local_track_id}${old ? ` · há ${seconds(track.age_s)}` : ""}`;
        const person = track.cls === "person";
        group.append(svg("text", {x: px + (person ? -14 : 14), y: py + (cameraIndex % 2 ? 22 : -13), "text-anchor": person ? "end" : "start", fill: color, "font-size": 10, "paint-order": "stroke", stroke: "#111a25", "stroke-width": 3}, label));
        const kind = {person: "Pessoa", suitcase: "Mala", backpack: "Mochila", handbag: "Bolsa"}[track.cls] || track.cls;
        group.append(svg("title", {}, `${kind} · ${label} · segmento ${track.track_key.segment_id}`));
        map.append(group);
      }
    });
    $("coordinate-label").textContent = `${floor.coordinate_frame.origin} · X: ${floor.coordinate_frame.x_axis} · Y: ${floor.coordinate_frame.y_axis}`;
  }

  function renderCard(camera, snapshot) {
    const index = config.cameras.findIndex(c => c.camera_id === camera.camera_id), ticket = generation;
    const card = make("article", undefined, `camera-card accent-${index % 4}${selected === camera.camera_id ? " selected" : ""}`);
    card.setAttribute("aria-label", `Câmera ${camera.camera_id}`);
    const head = make("div", undefined, "card-head"), choose = make("button", undefined, "camera-select");
    choose.type = "button"; choose.setAttribute("aria-label", `Selecionar ${camera.camera_id}`);
    choose.setAttribute("aria-pressed", String(selected === camera.camera_id));
    choose.append(make("span", undefined, "camera-symbol"), make("span", camera.camera_id));
    choose.addEventListener("click", () => { selected = camera.camera_id; render(); });
    const status = make("span", labels[snapshot.status], `status ${snapshot.status}`);
    head.append(choose, status);
    const holder = make("div", undefined, "camera-image"), placeholder = make("span", "Sem imagem neste instante", "placeholder");
    holder.append(placeholder);
    if (snapshot.image) {
      const descriptor = snapshot.image, image = new Image();
      placeholder.textContent = "Carregando imagem…";
      image.alt = `${camera.camera_id} · quadro ${descriptor.frame_index} · ${seconds(descriptor.t)}`;
      image.onload = () => {
        if (ticket !== generation || !holder.isConnected) return;
        placeholder.hidden = true; holder.prepend(image);
      };
      image.onerror = () => {
        if (ticket === generation && holder.isConnected) {
          placeholder.textContent = "Falha ao carregar imagem; observações vêm do cache";
          status.textContent = "Imagem indisponível"; status.className = "status unavailable";
        }
      };
      image.src = descriptor.url;
      if (descriptor.historical) holder.append(make("span", "HISTÓRICO", "historical"));
    }
    const foot = make("div", undefined, "card-foot"), description = make("div"), clocks = make("div", undefined, "clock-details");
    const origin = camera.mode === "oracle" ? "ORACLE / GABARITO" : camera.provenance === "synthetic" ? "SINTÉTICO" : "PREDIÇÕES DO DETECTOR";
    description.append(make("p", camera.media_kind === "schematic" ? "Esquema sintético · sem vídeo" : camera.media_kind === "video" ? "Vídeo gravado" : "Vídeo indisponível", "origin"));
    description.append(make("p", `${origin} · ${camera.segment_id}`));
    const missing = [];
    if (!camera.position_m) missing.push("posição desconhecida");
    if (!camera.projection_frame_id) missing.push("sem calibração comum");
    if (!camera.coverage_m) missing.push("cobertura desconhecida");
    if (missing.length) description.append(make("p", missing.join(" · ")));
    clocks.append(make("p", snapshot.image ? `Imagem ${seconds(snapshot.image.t)} · idade ${seconds(snapshot.image.age_s)}` : "Imagem ausente"));
    clocks.append(make("p", camera.clock.uncertainty_s === null ? "Sincronização: precisão desconhecida" : `Incerteza do relógio ±${seconds(camera.clock.uncertainty_s)}`));
    foot.append(description, clocks); card.append(head, holder, foot);
    return card;
  }

  function render() {
    if (!state) return;
    const floor = $("floor").value, cameras = config.cameras.filter(c => c.floor_id === floor);
    $("camera-count").textContent = `${cameras.length} câmeras`;
    $("selected-label").textContent = selected ? `${selected} em destaque` : "IDs locais por câmera";
    $("cameras").replaceChildren(...cameras.map(camera => renderCard(camera, state.cameras.find(c => c.camera_id === camera.camera_id))));
    $("clock").textContent = seconds(state.t); renderMap();
  }

  function stop() {
    if (timer) clearInterval(timer);
    timer = undefined; $("play").textContent = "Reproduzir";
  }
  async function seek(t) {
    cursor = Math.max(config.timeline.start_s, Math.min(config.timeline.end_s, t));
    $("timeline").value = String(cursor);
    const ticket = ++generation;
    if (controller) controller.abort();
    controller = new AbortController();
    $("loading").textContent = "Carregando instante…"; $("workspace").setAttribute("aria-busy", "true");
    try {
      const response = await fetch(`/api/state?t=${encodeURIComponent(cursor)}`, {signal: controller.signal});
      if (!response.ok) throw new Error("O servidor não retornou o estado do replay.");
      const next = await response.json();
      if (ticket !== generation) return;
      state = next; $("error").hidden = true; render();
    } catch (error) {
      if (error.name === "AbortError" || ticket !== generation) return;
      stop(); $("error").textContent = "Replay não atualizado. Verifique o servidor local e tente mover a linha de tempo."; $("error").hidden = false;
    } finally {
      if (ticket === generation) { $("loading").textContent = ""; $("workspace").setAttribute("aria-busy", "false"); }
    }
  }
  function start() {
    if (cursor >= config.timeline.end_s) cursor = config.timeline.start_s;
    const origin = cursor, began = performance.now(), speed = Number($("speed").value);
    $("play").textContent = "Pausar";
    timer = setInterval(() => {
      const t = origin + (performance.now() - began) / 1000 * speed;
      seek(t);
      if (t >= config.timeline.end_s) stop();
    }, 250);
  }
  async function init() {
    try {
      const response = await fetch("/api/config");
      if (!response.ok) throw new Error("Configuração indisponível");
      config = await response.json();
      $("site").textContent = `${config.site_id} · Referenciais locais, sem identidade global`;
      for (const floor of config.floors) { const option = make("option", floor.floor_id); option.value = floor.floor_id; $("floor").append(option); }
      const target = replayTarget(window.location.search, config);
      $("floor").value = target.floor_id;
      selected = target.camera_id;
      $("timeline").min = String(config.timeline.start_s); $("timeline").max = String(config.timeline.end_s); $("timeline").step = String(config.timeline.step_s);
      $("extent").textContent = `${seconds(config.timeline.start_s)} — ${seconds(config.timeline.end_s)} · segundos relativos`;
      $("timeline").addEventListener("input", event => { stop(); seek(Number(event.target.value)); });
      $("play").addEventListener("click", () => timer ? stop() : start());
      $("reset").addEventListener("click", () => { stop(); seek(config.timeline.start_s); });
      $("speed").addEventListener("change", () => { if (timer) { stop(); start(); } });
      $("floor").addEventListener("change", () => { selected = config.cameras.find(c => c.floor_id === $("floor").value)?.camera_id || null; render(); });
      $("history").addEventListener("change", renderMap);
      await seek(target.t);
    } catch (error) { $("error").textContent = "Não foi possível carregar o laboratório. Verifique o servidor local."; $("error").hidden = false; }
  }
  init();
})();
