const API_BASE = 'http://localhost:8000';

const URGENCY_COLOR = {
  pruned_recently: '#a9c4bc',
  moderate: '#f5b942',
  attention: '#ff5a4e',
};
const URGENCY_LABEL = {
  pruned_recently: 'Recém-podado',
  moderate: 'Crescimento moderado',
  attention: 'Requer atenção',
};
const CONFIDENCE_COLOR = { high: '#a9c4bc', medium: '#f5b942', low: '#ff5a4e' };
const CONFIDENCE_LABEL = { high: 'Alta', medium: 'Média', low: 'Baixa' };
const FALLBACK_COLOR = '#4b5f5a';

// agenda mockada: guardada só em memória, por segment_id
const scheduleStore = {};

let allFeatures = [];   // todas as features carregadas da API, na ordem em que vieram (já ordenadas por km no backend)
let currentIndex = -1;  // índice do segmento atualmente focado, dentro de allFeatures
let currentSegmentId = null;
let currentGeojson = null;

const DARK_TILES = ['https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
                     'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
                     'https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'];
const LIGHT_TILES = ['https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
                      'https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
                      'https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png'];

function buildStyle(tiles) {
  return {
    version: 8,
    sources: {
      'dark-basemap': {
        type: 'raster',
        tiles,
        tileSize: 256,
        attribution: '© CARTO © OpenStreetMap contributors'
      }
    },
    layers: [{ id: 'dark-basemap', type: 'raster', source: 'dark-basemap' }]
  };
}

const map = new maplibregl.Map({
  container: 'map',
  style: buildStyle(DARK_TILES),
  center: [-47.2, -22.5],
  zoom: 8,
});

map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

function addSegmentLayers() {
  if (!currentGeojson) return;
  if (map.getSource('segments')) return; // já adicionado, evita duplicar

  map.addSource('segments', { type: 'geojson', data: currentGeojson });

  map.addLayer({
    id: 'segments-glow',
    type: 'line',
    source: 'segments',
    paint: {
      'line-color': [
        'match', ['coalesce', ['get', 'predicted_urgency'], 'none'],
        'pruned_recently', URGENCY_COLOR.pruned_recently,
        'moderate', URGENCY_COLOR.moderate,
        'attention', URGENCY_COLOR.attention,
        FALLBACK_COLOR
      ],
      'line-width': 7,
      'line-blur': 4,
      'line-opacity': 0.35,
    }
  });

  map.addLayer({
    id: 'segments-line',
    type: 'line',
    source: 'segments',
    paint: {
      'line-color': [
        'match', ['coalesce', ['get', 'predicted_urgency'], 'none'],
        'pruned_recently', URGENCY_COLOR.pruned_recently,
        'moderate', URGENCY_COLOR.moderate,
        'attention', URGENCY_COLOR.attention,
        FALLBACK_COLOR
      ],
      'line-width': 3,
    }
  });

  map.on('click', 'segments-line', (e) => {
    if (e.features && e.features[0]) {
      const idx = allFeatures.findIndex(f => f.properties.segment_id === e.features[0].properties.segment_id);
      focusSegment(idx, { fly: false });
    }
  });
  map.on('mouseenter', 'segments-line', () => map.getCanvas().style.cursor = 'pointer');
  map.on('mouseleave', 'segments-line', () => map.getCanvas().style.cursor = '');
}

map.on('load', async () => {
  try {
    const res = await fetch(`${API_BASE}/segments`);
    if (!res.ok) throw new Error('status ' + res.status);
    currentGeojson = await res.json();

    addSegmentLayers();

    allFeatures = currentGeojson.features || [];
    document.getElementById('stat-total').textContent = allFeatures.length;
    document.getElementById('stat-attention').textContent =
      allFeatures.filter(f => f.properties.predicted_urgency === 'attention').length;

    if (allFeatures.length) {
      const bounds = new maplibregl.LngLatBounds();
      allFeatures.forEach(f => (f.geometry.coordinates || []).forEach(c => bounds.extend(c)));
      map.fitBounds(bounds, { padding: 60, duration: 0 });
    }

    renderAnalise();

    // inicia o navegador de KM no primeiro segmento, sem abrir o painel
    // automaticamente -- só popula a barra de navegação inferior
    if (allFeatures.length) updateKmNav(0);

  } catch (err) {
    console.error(err);
    document.getElementById('api-error').style.display = 'block';
  }
});

// -----------------------------------------------------------------
// TEMA CLARO / ESCURO
// -----------------------------------------------------------------
let isLight = false;

function waitForStyleThenAddLayers() {
  if (map.isStyleLoaded()) {
    addSegmentLayers();
  } else {
    map.once('idle', waitForStyleThenAddLayers);
  }
}

document.getElementById('theme-toggle').onclick = () => {
  isLight = !isLight;
  document.body.classList.toggle('theme-light', isLight);
  document.getElementById('theme-toggle').textContent = isLight ? '☀ Claro' : '🌙 Escuro';

  map.setStyle(buildStyle(isLight ? LIGHT_TILES : DARK_TILES));
  waitForStyleThenAddLayers();
};

// -----------------------------------------------------------------
// NAVEGADOR DE KM (complementar ao clique no mapa)
// -----------------------------------------------------------------
function updateKmNav(index) {
  currentIndex = index;
  const feat = allFeatures[index];
  if (!feat) return;
  const p = feat.properties;

  document.getElementById('km-nav-current').textContent =
    `KM ${Number(p.km_start).toFixed(1)} – ${Number(p.km_end).toFixed(1)}`;

  const prev = allFeatures[index - 1];
  const next = allFeatures[index + 1];
  document.getElementById('km-prev-name').textContent = prev
    ? (prev.properties.track_start_name || `KM ${Number(prev.properties.km_start).toFixed(1)}`)
    : '— início da malha —';
  document.getElementById('km-next-name').textContent = next
    ? (next.properties.track_start_name || `KM ${Number(next.properties.km_start).toFixed(1)}`)
    : '— fim da malha —';

  document.getElementById('km-prev-btn').disabled = !prev;
  document.getElementById('km-next-btn').disabled = !next;
}

function focusSegment(index, opts = {}) {
  if (index < 0 || index >= allFeatures.length) return;
  updateKmNav(index);
  const feat = allFeatures[index];
  openPanel(feat.properties);
  switchTab('detalhes');
  document.getElementById('panel').classList.add('open');

  if (opts.fly !== false) {
    const coords = feat.geometry.coordinates || [];
    if (coords.length) {
      const bounds = new maplibregl.LngLatBounds();
      coords.forEach(c => bounds.extend(c));
      map.fitBounds(bounds, { padding: 200, maxZoom: 15, duration: 500 });
    }
  }
}

document.getElementById('km-prev-btn').onclick = () => focusSegment(currentIndex - 1);
document.getElementById('km-next-btn').onclick = () => focusSegment(currentIndex + 1);
document.getElementById('km-prev-col').onclick = () => { if (currentIndex > 0) focusSegment(currentIndex - 1); };
document.getElementById('km-next-col').onclick = () => { if (currentIndex < allFeatures.length - 1) focusSegment(currentIndex + 1); };

// -----------------------------------------------------------------
// ABAS DO PAINEL
// -----------------------------------------------------------------
function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('active', p.id === `tab-${name}`));
  if (name === 'analise') renderAnalise();
  if (name === 'agendados') renderAgendados();
}
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});
document.getElementById('open-analise').onclick = () => {
  document.getElementById('panel').classList.add('open');
  switchTab('analise');
};
document.getElementById('open-agendados').onclick = () => {
  document.getElementById('panel').classList.add('open');
  switchTab('agendados');
};

// -----------------------------------------------------------------
// ABA DETALHES
// -----------------------------------------------------------------
function openPanel(props) {
  currentSegmentId = props.segment_id;
  document.getElementById('empty-panel').style.display = 'none';
  document.getElementById('panel-content').style.display = 'block';

  document.getElementById('detail-km').textContent =
    `KM ${Number(props.km_start).toFixed(1)} – ${Number(props.km_end).toFixed(1)}`;
  document.getElementById('detail-road').textContent = `SP-${props.road_code || '330'}`;

  const tag = document.getElementById('status-tag');
  const urgency = props.predicted_urgency;
  if (urgency && URGENCY_LABEL[urgency]) {
    tag.textContent = URGENCY_LABEL[urgency];
    tag.style.color = URGENCY_COLOR[urgency];
    tag.style.borderColor = URGENCY_COLOR[urgency];
  } else {
    tag.textContent = 'Sem previsão disponível';
    tag.style.color = FALLBACK_COLOR;
    tag.style.borderColor = FALLBACK_COLOR;
  }

  document.getElementById('detail-confidence').textContent = CONFIDENCE_LABEL[props.confidence] || '—';
  document.getElementById('detail-grass').textContent =
    props.grass_ratio != null ? `${(props.grass_ratio * 100).toFixed(0)}%` : '—';

  const trackName = [props.track_start_name, props.track_end_name].filter(Boolean).join(' → ');
  document.getElementById('detail-track').textContent = trackName || 'Sem referência de trecho';

  loadNdvi(props.segment_id);
  resetSchedulingUI();
  renderExistingSchedule();
}

async function loadNdvi(segmentId) {
  const mini = document.getElementById('ndvi-mini');
  const note = document.getElementById('ndvi-note');
  mini.innerHTML = '';
  note.textContent = 'carregando…';
  try {
    const res = await fetch(`${API_BASE}/segments/${encodeURIComponent(segmentId)}/ndvi`);
    if (!res.ok) throw new Error();
    const data = await res.json();
    const obs = (data.observations || []).slice(-16);
    if (!obs.length) { note.textContent = 'sem observações registradas'; return; }
    const max = Math.max(...obs.map(o => o.ndvi_avg), 0.05);
    obs.forEach(o => {
      const bar = document.createElement('div');
      bar.className = 'bar';
      bar.style.height = Math.max(4, (o.ndvi_avg / max) * 40) + 'px';
      bar.title = `${o.date_capture}: NDVI ${o.ndvi_avg.toFixed(3)}`;
      mini.appendChild(bar);
    });
    const last = obs[obs.length - 1];
    note.textContent = `Última leitura: ${last.date_capture} · NDVI ${last.ndvi_avg.toFixed(3)}`;
  } catch {
    note.textContent = 'não foi possível carregar a série de NDVI';
  }
}

function resetSchedulingUI() {
  document.getElementById('schedule-form').classList.remove('open');
  document.getElementById('schedule-success').style.display = 'none';
  document.getElementById('btn-agendar').style.display = 'block';
  document.getElementById('schedule-error').style.display = 'none';
  document.getElementById('schedule-date').value = '';
  document.getElementById('schedule-time').value = '';
}

function renderExistingSchedule() {
  const existing = scheduleStore[currentSegmentId];
  if (existing) {
    document.getElementById('btn-agendar').style.display = 'none';
    document.getElementById('schedule-success').style.display = 'block';
    document.getElementById('success-text').textContent = `${existing.date} às ${existing.time}`;
  }
}

function updateScheduledCount() {
  document.getElementById('stat-scheduled').textContent = Object.keys(scheduleStore).length;
}

document.getElementById('panel-close').onclick = () => {
  document.getElementById('panel').classList.remove('open');
};

document.getElementById('btn-agendar').onclick = () => {
  document.getElementById('schedule-form').classList.add('open');
  document.getElementById('btn-agendar').style.display = 'none';
};

document.getElementById('btn-cancel').onclick = () => {
  resetSchedulingUI();
  renderExistingSchedule();
};

document.getElementById('btn-confirm').onclick = () => {
  const date = document.getElementById('schedule-date').value;
  const time = document.getElementById('schedule-time').value;
  const errEl = document.getElementById('schedule-error');
  if (!date || !time) {
    errEl.style.display = 'block';
    return;
  }
  errEl.style.display = 'none';
  scheduleStore[currentSegmentId] = { date, time };
  document.getElementById('schedule-form').classList.remove('open');
  document.getElementById('schedule-success').style.display = 'block';
  document.getElementById('success-text').textContent = `${date} às ${time}`;
  updateScheduledCount();
};

document.getElementById('btn-new-schedule').onclick = () => {
  delete scheduleStore[currentSegmentId];
  resetSchedulingUI();
  updateScheduledCount();
};

// -----------------------------------------------------------------
// ABA ANÁLISE -- estatísticas agregadas sobre os dados já carregados
// -----------------------------------------------------------------
function renderAnalise() {
  if (!allFeatures.length) return;

  const urgencyCounts = { pruned_recently: 0, moderate: 0, attention: 0, none: 0 };
  const confidenceCounts = { high: 0, medium: 0, low: 0, none: 0 };
  let grassSum = 0, grassCount = 0;
  let kmMin = Infinity, kmMax = -Infinity;

  allFeatures.forEach(f => {
    const p = f.properties;
    urgencyCounts[p.predicted_urgency || 'none']++;
    confidenceCounts[p.confidence || 'none']++;
    if (p.grass_ratio != null) { grassSum += p.grass_ratio; grassCount++; }
    if (p.km_start != null) kmMin = Math.min(kmMin, p.km_start);
    if (p.km_end != null) kmMax = Math.max(kmMax, p.km_end);
  });

  const total = allFeatures.length;
  const urgencyRows = [
    { key: 'pruned_recently', label: 'Recém-podado', color: URGENCY_COLOR.pruned_recently },
    { key: 'moderate', label: 'Moderado', color: URGENCY_COLOR.moderate },
    { key: 'attention', label: 'Atenção', color: URGENCY_COLOR.attention },
    { key: 'none', label: 'Sem previsão', color: FALLBACK_COLOR },
  ];
  document.getElementById('analise-urgencia').innerHTML = urgencyRows.map(row => barRow(
    row.label, urgencyCounts[row.key], total, row.color
  )).join('');

  const confidenceRows = [
    { key: 'high', label: 'Alta', color: CONFIDENCE_COLOR.high },
    { key: 'medium', label: 'Média', color: CONFIDENCE_COLOR.medium },
    { key: 'low', label: 'Baixa', color: CONFIDENCE_COLOR.low },
    { key: 'none', label: 'Sem dado', color: FALLBACK_COLOR },
  ];
  document.getElementById('analise-confianca').innerHTML = confidenceRows.map(row => barRow(
    row.label, confidenceCounts[row.key], total, row.color
  )).join('');

  document.getElementById('analise-grass-avg').textContent =
    grassCount ? `${((grassSum / grassCount) * 100).toFixed(0)}%` : '—';
  document.getElementById('analise-km-total').textContent =
    isFinite(kmMax - kmMin) ? `${(kmMax - kmMin).toFixed(1)} km` : '—';
}

function barRow(label, count, total, color) {
  const pct = total ? (count / total) * 100 : 0;
  return `
    <div class="analise-bar-row">
      <div class="analise-bar-label">${label}</div>
      <div class="analise-bar-track"><div class="analise-bar-fill" style="width:${pct}%;background:${color};"></div></div>
      <div class="analise-bar-count">${count}</div>
    </div>`;
}

// -----------------------------------------------------------------
// ABA AGENDADOS
// -----------------------------------------------------------------
function renderAgendados() {
  const listEl = document.getElementById('agendados-list');
  const emptyEl = document.getElementById('agendados-empty');
  const entries = Object.entries(scheduleStore);

  if (!entries.length) {
    listEl.innerHTML = '';
    emptyEl.style.display = 'block';
    return;
  }
  emptyEl.style.display = 'none';

  listEl.innerHTML = entries.map(([segId, sched]) => {
    const feat = allFeatures.find(f => f.properties.segment_id === segId);
    const p = feat ? feat.properties : {};
    const kmLabel = feat ? `KM ${Number(p.km_start).toFixed(1)} – ${Number(p.km_end).toFixed(1)}` : segId;
    return `
      <div class="agendado-item" data-seg="${segId}">
        <div class="ai-km">${kmLabel}</div>
        <div class="ai-when">${sched.date} às ${sched.time}</div>
        <button class="ai-cancel" data-seg="${segId}">Cancelar agendamento</button>
      </div>`;
  }).join('');

  listEl.querySelectorAll('.ai-cancel').forEach(btn => {
    btn.addEventListener('click', () => {
      delete scheduleStore[btn.dataset.seg];
      updateScheduledCount();
      renderAgendados();
    });
  });
}
