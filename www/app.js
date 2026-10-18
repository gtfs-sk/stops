'use strict';

const DATA_URL = './data/stops.geojson';
const INITIAL_CENTER = [19.25, 48.70];
const INITIAL_ZOOM = 7.05;
const MAX_RESULTS = 75;

const COLORS = {
  stop: '#087c70',
  station: '#d58c44',
  platform: '#4385c6',
};

const KIND_LABELS = {
  stop: 'Stop',
  station: 'Station / terminal',
  platform: 'Platform',
};

const MODE_LABELS = {
  local_bus: 'Local bus', regional_bus: 'Regional bus', train: 'Train',
  tram: 'Tram', trolleybus: 'Trolleybus', subway: 'Subway',
  cable_car: 'Cable car', ferry: 'Ferry', funicular: 'Funicular',
  light_rail: 'Light rail', monorail: 'Monorail',
};

const COUNTRY_LABELS = { SK: 'Slovakia', AT: 'Austria', HU: 'Hungary', CZ: 'Czechia', PL: 'Poland', UA: 'Ukraine' };
const formatNumber = new Intl.NumberFormat('en-US');
const collator = new Intl.Collator('en', { sensitivity: 'base', numeric: true });
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const state = {
  records: [],
  byId: new Map(),
  filtered: [],
  view: 'stops',
  mode: 'all',
  country: '',
  region: '',
  query: '',
  selectedId: null,
  loaded: false,
};

let map = null;
let mapReady = false;
let searchTimer = null;

// Cluster labels are HTML markers. They require no external MapLibre glyph font.
const clusterMarkers = new Map();
let clusterUpdateFrame = null;

function normalize(text) {
  return String(text ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
}

function modeArray(value) {
  if (Array.isArray(value)) return [...new Set(value.map(String).filter(Boolean))];
  if (typeof value === 'string' && value) return [...new Set(value.split(/[;,]/).map((s) => s.trim()).filter(Boolean))];
  return [];
}

function coordsFromGeometry(geometry) {
  if (!geometry || geometry.type !== 'Point' || !Array.isArray(geometry.coordinates)) return null;
  if (geometry.coordinates[0] == null || geometry.coordinates[1] == null) return null;
  const [lon, lat] = geometry.coordinates.map(Number);
  if (!Number.isFinite(lon) || !Number.isFinite(lat) || Math.abs(lon) > 180 || Math.abs(lat) > 90) return null;
  return [lon, lat];
}

function parseGeoJSON(data) {
  if (data?.type !== 'FeatureCollection' || !Array.isArray(data.features)) {
    throw new Error('Expected a valid GeoJSON FeatureCollection.');
  }

  const records = [];
  const byId = new Map();
  for (const feature of data.features) {
    if (!feature || feature.type !== 'Feature') continue;
    const p = feature.properties || {};
    const kind = String(p.type || '').toLowerCase();
    if (!['stop', 'station', 'platform'].includes(kind)) continue;
    const id = String(p.id ?? feature.id ?? '');
    if (!id || byId.has(id)) continue;
    const record = {
      id,
      name: String(p.name || 'Unnamed stop'),
      kind,
      country: String(p.country || ''),
      region: String(p.region || ''),
      district: String(p.district || ''),
      municipality: String(p.municipality || ''),
      code: String(p.platform_code ?? ''),
      parentId: String(p.stop_id ?? p.parent_station_id ?? ''),
      modes: modeArray(p.modes),
      amenities: modeArray(p.amenities),
      source: p.source ? String(p.source) : '',
      originalId: p.original_id ? String(p.original_id) : '',
      coords: coordsFromGeometry(feature.geometry),
      hasOwnLocation: !!coordsFromGeometry(feature.geometry),
      platforms: [],
      searchText: '',
    };
    records.push(record);
    byId.set(id, record);
  }

  for (const record of records) {
    if (record.kind !== 'platform') continue;
    const parent = byId.get(record.parentId);
    if (parent && parent.kind !== 'platform') parent.platforms.push(record);
  }
  for (const record of records) {
    if (record.kind !== 'platform') {
      if (record.platforms.length) {
        record.modes = [...new Set([...record.modes, ...record.platforms.flatMap((p) => p.modes)])];
      }
      record.platforms.sort((a, b) => collator.compare(a.code || a.name, b.code || b.name));
    }
    record.searchText = normalize([
      record.name, record.id, record.country, record.region, record.district,
      record.municipality, record.code, record.parentId,
      ...record.platforms.map((p) => p.code),
    ].join(' '));
  }
  return { records, byId };
}

function createMap() {
  if (typeof maplibregl === 'undefined') {
    $('#map-count').textContent = 'Map library is unavailable';
    return;
  }
  map = new maplibregl.Map({
    container: 'map',
    center: INITIAL_CENTER,
    zoom: INITIAL_ZOOM,
    minZoom: 4,
    maxZoom: 18,
    attributionControl: false,
    style: {
      version: 8,
      sources: {
        'base': {
          type: 'raster',
          tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
          tileSize: 256,
          maxzoom: 19,
          attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors',
        },
      },
      layers: [{ id: 'base', type: 'raster', source: 'base' }],
    },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
  map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');
  map.on('load', () => {
    map.addSource('locations', { type: 'geojson', data: emptyGeoJSON(), cluster: true, clusterMaxZoom: 17, clusterRadius: 64 });
    map.addSource('selected', { type: 'geojson', data: emptyGeoJSON() });

    map.addLayer({
      id: 'cluster-halo', type: 'circle', source: 'locations',
      filter: ['has', 'point_count'],
      paint: {
        'circle-color': '#1f8c7b',
        'circle-radius': ['step', ['get', 'point_count'], 24, 25, 30, 150, 36, 650, 43],
        'circle-opacity': 0.17,
      },
    });
    map.addLayer({
      id: 'clusters', type: 'circle', source: 'locations',
      filter: ['has', 'point_count'],
      paint: {
        'circle-color': ['step', ['get', 'point_count'], '#138b7e', 25, '#0b766f', 150, '#0c5c59', 650, '#153d48'],
        'circle-radius': ['step', ['get', 'point_count'], 19, 25, 24, 150, 30, 650, 37],
        'circle-stroke-color': '#ffffff', 'circle-stroke-width': 3,
        'circle-opacity': .96,
        'circle-stroke-opacity': 1,
      },
    });
    for (const kind of ['platform', 'stop', 'station']) {
      map.addLayer({
        id: `point-${kind}`, type: 'circle', source: 'locations',
        filter: ['all', ['!', ['has', 'point_count']], ['==', ['get', 'kind'], kind]],
        paint: {
          'circle-color': COLORS[kind],
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 3, 9, 5, 13, 7, 17, 9],
          'circle-stroke-color': '#fff', 'circle-stroke-width': 1.8,
          'circle-opacity': .96,
        },
      });
    }
    map.addLayer({ id: 'selected-halo', type: 'circle', source: 'selected',
      paint: { 'circle-color': '#fff', 'circle-radius': 16, 'circle-opacity': .85, 'circle-stroke-color': '#0b635c', 'circle-stroke-width': 3 },
    });
    map.addLayer({ id: 'selected-center', type: 'circle', source: 'selected',
      paint: { 'circle-color': ['get', 'color'], 'circle-radius': 8 },
    });

    async function openCluster(e) {
      const feature = e.features?.[0];
      if (feature?.properties?.cluster_id == null) return;
      const source = map.getSource('locations');
      const clusterId = Number(feature.properties.cluster_id);
      try {
        const zoom = await source.getClusterExpansionZoom(clusterId);
        // At high zoom, overlapping platforms can share identical coordinates.
        // Offer a clickable list instead of zooming indefinitely.
        if (zoom <= map.getZoom() + 0.1 || map.getZoom() >= 17) {
          const leaves = await source.getClusterLeaves(clusterId, 12, 0);
          const wrapper = document.createElement('div');
          const heading = document.createElement('h3');
          heading.textContent = `${formatNumber.format(feature.properties.point_count)} locations here`;
          wrapper.append(heading);
          for (const leaf of leaves) {
            const record = state.byId.get(String(leaf.properties.id));
            if (!record) continue;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'cluster-stop-button';
            button.textContent = `${record.name}${record.code ? ' · ' + record.code : ''}`;
            button.addEventListener('click', () => { selectRecord(record.id, false); popup.remove(); });
            wrapper.append(button);
          }
          if (feature.properties.point_count > leaves.length) {
            const note = document.createElement('p');
            note.textContent = 'Zoom in or filter to narrow down the results.';
            wrapper.append(note);
          }
          const popup = new maplibregl.Popup({ closeButton: true, maxWidth: '290px', className: 'cluster-popup' })
            .setLngLat(feature.geometry.coordinates).setDOMContent(wrapper).addTo(map);
          return;
        }
        map.easeTo({ center: feature.geometry.coordinates, zoom: Math.min(Math.max(zoom, map.getZoom() + 1), 18), duration: 550 });
      } catch (error) { console.warn('Cluster expansion failed', error); }
    }
    // Labels sit above circles; handle both with one hit test to avoid double clicks.
    map.on('click', (e) => {
      const cluster = map.queryRenderedFeatures(e.point, { layers: ['clusters'] })[0];
      if (cluster) void openCluster({ features: [cluster] });
    });
    for (const layer of ['point-platform', 'point-stop', 'point-station']) {
      map.on('click', layer, (e) => {
        const id = e.features?.[0]?.properties?.id;
        if (id) selectRecord(String(id), false);
      });
    }
    for (const layer of ['clusters', 'point-stop', 'point-station', 'point-platform']) {
      map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
    }
    map.on('moveend', scheduleClusterMarkers);
    map.on('sourcedata', (e) => {
      if (e.sourceId === 'locations' && e.isSourceLoaded) scheduleClusterMarkers();
    });
    map.on('idle', scheduleClusterMarkers);
    mapReady = true;
    renderMap();
    updateSelectedMarker();
  });
}

function scheduleClusterMarkers() {
  if (!mapReady || clusterUpdateFrame !== null) return;
  clusterUpdateFrame = requestAnimationFrame(() => {
    clusterUpdateFrame = null;
    updateClusterMarkers();
  });
}

function clearClusterMarkers() {
  for (const marker of clusterMarkers.values()) marker.remove();
  clusterMarkers.clear();
}

function clusterSize(count) {
  if (count >= 650) return 76;
  if (count >= 150) return 62;
  if (count >= 25) return 50;
  return 40;
}

function updateClusterMarkers() {
  if (!mapReady || !map?.getLayer('clusters')) return;

  const canvas = map.getCanvas();
  const visible = map.queryRenderedFeatures(
    [[0, 0], [canvas.clientWidth, canvas.clientHeight]],
    { layers: ['clusters'] },
  );
  const nextIds = new Set();

  for (const feature of visible) {
    const id = feature.properties?.cluster_id;
    if (id == null || feature.geometry?.type !== 'Point') continue;
    const key = String(id);
    if (nextIds.has(key)) continue; // Same cluster can span multiple tiles.
    nextIds.add(key);

    const count = Number(feature.properties.point_count) || 0;
    const coordinates = feature.geometry.coordinates;
    let marker = clusterMarkers.get(key);
    if (!marker) {
      const element = document.createElement('button');
      element.type = 'button';
      element.className = 'map-cluster-marker';
      element.setAttribute('aria-label', `${formatNumber.format(count)} stops. Click to zoom in.`);
      element.title = `${formatNumber.format(count)} stops · click to zoom in`;
      element.style.setProperty('--cluster-size', `${clusterSize(count)}px`);
      element.textContent = formatNumber.format(count);
      element.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        const current = map.getSource('locations');
        if (!current) return;
        const clusterId = Number(key);
        void (async () => {
          try {
            const zoom = await current.getClusterExpansionZoom(clusterId);
            if (zoom > map.getZoom() + 0.1 && map.getZoom() < 18) {
              map.easeTo({ center: marker.getLngLat(), zoom: Math.min(zoom, 18), duration: 550 });
              return;
            }
            // Same-coordinate points or deepest cluster: show individual stops.
            const leaves = await current.getClusterLeaves(clusterId, 25, 0);
            const wrapper = document.createElement('div');
            const title = document.createElement('h3');
            title.textContent = `${formatNumber.format(count)} locations here`;
            wrapper.append(title);
            const popup = new maplibregl.Popup({ maxWidth: '290px', className: 'cluster-popup' });
            for (const leaf of leaves) {
              const record = state.byId.get(String(leaf.properties?.id));
              if (!record) continue;
              const button = document.createElement('button');
              button.type = 'button';
              button.className = 'cluster-stop-button';
              button.textContent = record.name + (record.code ? ` · ${record.code}` : '');
              button.addEventListener('click', () => { selectRecord(record.id, false); popup.remove(); });
              wrapper.append(button);
            }
            if (count > leaves.length) {
              const note = document.createElement('p');
              note.textContent = 'Zoom in or use filters to narrow down the results.';
              wrapper.append(note);
            }
            popup.setLngLat(marker.getLngLat()).setDOMContent(wrapper).addTo(map);
          } catch (error) {
            console.warn('Could not expand stop cluster', error);
          }
        })();
      });
      marker = new maplibregl.Marker({ element, anchor: 'center' })
        .setLngLat(coordinates)
        .addTo(map);
      clusterMarkers.set(key, marker);
    } else {
      marker.setLngLat(coordinates);
    }
  }
  for (const [key, marker] of clusterMarkers) {
    if (!nextIds.has(key)) {
      marker.remove();
      clusterMarkers.delete(key);
    }
  }
}

function emptyGeoJSON() { return { type: 'FeatureCollection', features: [] }; }

function viewMatches(record) {
  if (state.view === 'all') return true;
  if (state.view === 'platforms') return record.kind === 'platform';
  return record.kind !== 'platform' || !state.byId.has(record.parentId);
}

function modeMatches(record) {
  if (state.mode === 'all') return true;
  const modes = record.modes;
  if (state.mode === 'bus') return modes.some((m) => ['local_bus', 'regional_bus', 'trolleybus'].includes(m));
  if (state.mode === 'train') return modes.includes('train');
  if (state.mode === 'tram') return modes.includes('tram');
  return modes.some((m) => !['local_bus', 'regional_bus', 'trolleybus', 'train', 'tram'].includes(m));
}

function applyFilters() {
  state.filtered = state.records.filter((r) =>
    viewMatches(r) && modeMatches(r) &&
    (!state.country || r.country === state.country) &&
    (!state.region || r.region === state.region) &&
    (!state.query || r.searchText.includes(state.query))
  );
  $('#results-count').textContent = formatNumber.format(state.filtered.length);
  renderResults();
  renderMap();
}

function renderResults() {
  const container = $('#results');
  container.replaceChildren();
  const visible = state.filtered.slice(0, MAX_RESULTS);
  if (!visible.length) {
    const empty = document.createElement('div');
    empty.className = 'results-hint';
    empty.textContent = state.loaded ? 'No results. Try a different search or filters.' : 'Loading stops…';
    container.append(empty);
  }
  const fragment = document.createDocumentFragment();
  for (const record of visible) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `result${record.id === state.selectedId ? ' active' : ''}`;
    const icon = document.createElement('span');
    icon.className = `result-icon ${record.kind}`;
    icon.textContent = record.kind === 'station' ? '▤' : record.kind === 'platform' ? '↧' : '●';
    const body = document.createElement('span');
    body.className = 'result-body';
    const name = document.createElement('span');
    name.className = 'result-name';
    name.textContent = record.name;
    const sub = document.createElement('div');
    sub.className = 'result-sub';
    const loc = [record.municipality, record.district && record.district !== record.municipality ? record.district : '', record.country].filter(Boolean).join(' · ');
    const tag = record.kind === 'platform' ? `Platform${record.code ? ' ' + record.code : ''}` : (record.kind === 'station' ? 'Station' : 'Stop');
    sub.textContent = `${tag} · ${loc || 'Unknown location'}`;
    body.append(name, sub);
    const arrow = document.createElement('span');
    arrow.className = 'result-end';
    arrow.textContent = '›';
    button.append(icon, body, arrow);
    button.addEventListener('click', () => selectRecord(record.id, true));
    fragment.append(button);
  }
  container.append(fragment);
  const remaining = state.filtered.length - visible.length;
  $('#results-more').hidden = remaining <= 0;
  if (remaining > 0) $('#results-more').textContent = `Showing the first ${MAX_RESULTS} results. Refine your search to see more.`;
}

function mapFeature(record) {
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: record.coords },
    properties: { id: record.id, kind: record.kind },
  };
}

function mapRecords() {
  const points = new Map();
  for (const record of state.filtered) {
    if (record.hasOwnLocation && record.coords) points.set(record.id, record);

    if (state.view === 'stops' && record.kind === 'stop') {
      for (const platform of record.platforms) {
        if (platform.hasOwnLocation && platform.coords) points.set(platform.id, platform);
      }
    }
  }
  return [...points.values()];
}

function renderMap() {
  const points = mapRecords();
  $('#map-count').textContent = `${formatNumber.format(points.length)} physical locations on map`;
  if (!mapReady) return;
  const source = map.getSource('locations');
  if (!source) return;
  clearClusterMarkers();
  source.setData({ type: 'FeatureCollection', features: points.map(mapFeature) });
}

function updateSelectedMarker() {
  if (!mapReady) return;
  const record = state.byId.get(state.selectedId);
  const features = record?.coords ? [{
    type: 'Feature', geometry: { type: 'Point', coordinates: record.coords },
    properties: { color: COLORS[record.kind] },
  }] : [];
  map.getSource('selected')?.setData({ type: 'FeatureCollection', features });
}

function selectRecord(id, moveMap = true) {
  const record = state.byId.get(id);
  if (!record) return;
  state.selectedId = id;
  renderResults();
  renderDetail(record);
  updateSelectedMarker();
  if (moveMap && map) {
    if (record.hasOwnLocation && record.coords) {
      map.flyTo({ center: record.coords, zoom: Math.max(map.getZoom(), 14.5), essential: true });
    } else if (record.platforms.length) {
      const positions = record.platforms.filter((p) => p.hasOwnLocation && p.coords).map((p) => p.coords);
      if (positions.length === 1) {
        map.flyTo({ center: positions[0], zoom: Math.max(map.getZoom(), 15), essential: true });
      } else if (positions.length > 1) {
        const bounds = positions.reduce(
          (acc, coords) => acc.extend(coords),
          new maplibregl.LngLatBounds(positions[0], positions[0]),
        );
        map.fitBounds(bounds, { padding: 90, maxZoom: 16, duration: 550 });
      }
    }
  }
  const url = new URL(location.href);
  url.searchParams.set('stop', id);
  history.replaceState(null, '', url);
}

function closeDetail() {
  state.selectedId = null;
  $('#detail-panel').hidden = true;
  updateSelectedMarker();
  renderResults();
  const url = new URL(location.href);
  url.searchParams.delete('stop');
  history.replaceState(null, '', url);
}

function detailLine(label, value) {
  if (!value) return null;
  const line = document.createElement('p');
  line.className = 'detail-line';
  const bold = document.createElement('strong');
  bold.textContent = `${label}: `;
  line.append(bold, document.createTextNode(value));
  return line;
}

function detailSectionTitle(text) {
  const p = document.createElement('p');
  p.className = 'detail-section-title';
  p.textContent = text;
  return p;
}

function detailButton(label, handler) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'detail-action';
  button.textContent = label;
  button.addEventListener('click', handler);
  return button;
}

function renderDetail(record) {
  $('#detail-panel').hidden = false;
  $('#detail-kind').textContent = KIND_LABELS[record.kind];
  $('#detail-name').textContent = record.name;
  const body = $('#detail-body');
  body.replaceChildren();

  for (const [label, text] of [
    ['Municipality', record.municipality], ['District', record.district],
    ['Region', record.region], ['Country', COUNTRY_LABELS[record.country] || record.country],
    ['Platform code', record.code],
  ]) {
    const line = detailLine(label, text);
    if (line) body.append(line);
  }
  if (record.kind === 'platform' && state.byId.has(record.parentId)) {
    const parent = state.byId.get(record.parentId);
    const parentButton = detailButton(`↖ ${parent.name} (${KIND_LABELS[parent.kind].toLowerCase()})`, () => selectRecord(parent.id));
    body.append(detailSectionTitle('Belongs to'), parentButton);
  }
  if (record.modes.length) {
    const pills = document.createElement('div');
    pills.className = 'detail-pills';
    for (const mode of record.modes) {
      const pill = document.createElement('span');
      pill.className = 'detail-pill';
      pill.textContent = MODE_LABELS[mode] || mode;
      pills.append(pill);
    }
    body.append(detailSectionTitle('Transport modes'), pills);
  }
  if (record.amenities.length) {
    const pills = document.createElement('div');
    pills.className = 'detail-pills';
    for (const amenity of record.amenities) {
      const pill = document.createElement('span');
      pill.className = 'detail-pill';
      pill.textContent = amenity.replaceAll('_', ' ');
      pills.append(pill);
    }
    body.append(detailSectionTitle('Amenities'), pills);
  }
  if (record.platforms.length) {
    const list = document.createElement('div');
    list.className = 'detail-list';
    for (const child of record.platforms) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'detail-platform';
      const title = document.createElement('span');
      title.textContent = child.code ? `Platform ${child.code}` : 'Platform';
      const id = document.createElement('code');
      id.textContent = child.id;
      button.append(title, id);
      button.addEventListener('click', () => selectRecord(child.id));
      list.append(button);
    }
    body.append(detailSectionTitle(`Platforms (${record.platforms.length})`), list);
  }
  const coordText = record.coords ? `${record.coords[1].toFixed(6)}, ${record.coords[0].toFixed(6)}` : 'Not available';
  body.append(detailSectionTitle('Location'));
  const position = detailLine('Coordinates', coordText);
  if (position) body.append(position);
  if (!record.hasOwnLocation) {
    const virtual = document.createElement('p');
    virtual.className = 'detail-line';
    virtual.textContent = 'Virtual stop group — no own GPS coordinates. Only its platforms are mapped.';
    body.append(virtual);
    if (record.platforms.some((p) => p.hasOwnLocation)) {
      body.append(detailButton('Show platforms on map', () => selectRecord(record.id, true)));
    }
  }

  const actions = document.createElement('div');
  actions.className = 'detail-actions';
  if (record.coords) {
    actions.append(detailButton('Open in OpenStreetMap ↗', () => {
      const [lon, lat] = record.coords;
      window.open(`https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=17/${lat}/${lon}`, '_blank', 'noopener,noreferrer');
    }));
    actions.append(detailButton('Copy coordinates', async (event) => {
      try {
        await navigator.clipboard.writeText(coordText);
        event.target.textContent = 'Copied ✓';
      } catch { window.prompt('Copy coordinates:', coordText); }
    }));
  }
  if (actions.children.length) body.append(actions);
  const identifier = document.createElement('div');
  identifier.className = 'detail-id';
  identifier.textContent = `ID: ${record.id}`;
  body.append(identifier);
}

function fillSelect(element, options, placeholder) {
  const old = element.value;
  element.replaceChildren();
  const empty = document.createElement('option');
  empty.value = '';
  empty.textContent = placeholder;
  element.append(empty);
  for (const value of options) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = element === $('#country') ? (COUNTRY_LABELS[value] || value) : value;
    element.append(option);
  }
  element.value = options.includes(old) ? old : '';
}

function renderSelects() {
  const logical = state.records.filter((r) => r.kind !== 'platform');
  const countries = [...new Set(logical.map((r) => r.country).filter(Boolean))].sort(collator.compare);
  fillSelect($('#country'), countries, 'All countries');
  const regions = [...new Set(logical
    .filter((r) => !state.country || r.country === state.country)
    .map((r) => r.region).filter(Boolean))].sort(collator.compare);
  fillSelect($('#region'), regions, 'All regions');
  $('#region').disabled = regions.length === 0;
}

function loadDataset(data) {
  const parsed = parseGeoJSON(data);
  state.records = parsed.records;
  state.byId = parsed.byId;
  state.records.sort((a, b) => collator.compare(a.name, b.name) || collator.compare(a.id, b.id));
  state.loaded = true;
  $('#load-status').hidden = true;
  $('#load-error').hidden = true;
  $('#stat-stops').textContent = formatNumber.format(state.records.filter((r) => r.kind === 'stop').length);
  $('#stat-stations').textContent = formatNumber.format(state.records.filter((r) => r.kind === 'station').length);
  $('#stat-platforms').textContent = formatNumber.format(state.records.filter((r) => r.kind === 'platform').length);
  renderSelects();
  applyFilters();
  const linkedId = new URLSearchParams(location.search).get('stop');
  if (linkedId && state.byId.has(linkedId)) selectRecord(linkedId);
}

async function loadDefaultData() {
  try {
    const response = await fetch(DATA_URL);
    if (!response.ok) throw new Error(`HTTP ${response.status} – ${response.statusText}`);
    loadDataset(await response.json());
  } catch (error) {
    console.error('Dataset could not be loaded', error);
    $('#load-status').hidden = true;
    $('#load-error').hidden = false;
    $('#load-error-message').textContent = `${error.message}. Serve the repository root over HTTP, or choose a local GeoJSON file.`;
    $('#results').textContent = '';
  }
}

function attachEvents() {
  $('#search').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.query = normalize(e.target.value);
      applyFilters();
    }, 180);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) {
      e.preventDefault();
      $('#search').focus();
    } else if (e.key === 'Escape') {
      if (document.activeElement === $('#search') && $('#search').value) {
        $('#search').value = '';
        state.query = '';
        applyFilters();
      } else closeDetail();
    }
  });
  $$('.segment').forEach((button) => button.addEventListener('click', () => {
    state.view = button.dataset.view;
    $$('.segment').forEach((element) => {
      element.classList.toggle('active', element === button);
      element.setAttribute('aria-pressed', String(element === button));
    });
    applyFilters();
  }));
  $$('.chip').forEach((button) => button.addEventListener('click', () => {
    state.mode = button.dataset.mode;
    $$('.chip').forEach((element) => {
      element.classList.toggle('selected', element === button);
      element.setAttribute('aria-pressed', String(element === button));
    });
    applyFilters();
  }));
  $('#country').addEventListener('change', (e) => {
    state.country = e.target.value;
    state.region = '';
    renderSelects();
    applyFilters();
  });
  $('#region').addEventListener('change', (e) => { state.region = e.target.value; applyFilters(); });
  $('#clear-filters').addEventListener('click', () => {
    $('#search').value = '';
    state.query = '';
    state.mode = 'all';
    state.country = '';
    state.region = '';
    $$('.chip').forEach((button) => {
      button.classList.toggle('selected', button.dataset.mode === 'all');
      button.setAttribute('aria-pressed', String(button.dataset.mode === 'all'));
    });
    renderSelects();
    applyFilters();
  });
  $('#reset-map').addEventListener('click', () => map?.flyTo({ center: INITIAL_CENTER, zoom: INITIAL_ZOOM, essential: true }));
  $('#detail-close').addEventListener('click', closeDetail);
  $('#file-input').addEventListener('change', async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try { loadDataset(JSON.parse(await file.text())); }
    catch (error) { $('#load-error-message').textContent = `File error: ${error.message}`; }
  });
}

attachEvents();
createMap();
loadDefaultData();
