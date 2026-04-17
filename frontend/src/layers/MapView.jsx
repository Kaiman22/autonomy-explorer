import React, { useRef, useEffect, useState, useCallback } from 'react'
import maplibregl from 'maplibre-gl'

const BASEMAPS = {
  dark: {
    name: 'Dark',
    url: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
  },
  light: {
    name: 'Light',
    url: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
  },
  swisstopo: {
    name: 'Swisstopo',
    url: 'https://vectortiles.geo.admin.ch/styles/ch.swisstopo.lightbasemap.vt/style.json',
  },
}

// Price colors (green → yellow → orange → red)
const PRICE_COLORS = [
  [3000, '#1b5e20'],
  [5000, '#4caf50'],
  [7000, '#ffc107'],
  [10000, '#ef6c00'],
  [13000, '#e65100'],
  [16000, '#e94560'],
]

// Raw travel time colors (green → yellow → red, lower = better)
const ACCESS_TIME_COLORS = [
  [10, '#1b5e20'],
  [30, '#4caf50'],
  [60, '#ffc107'],
  [120, '#ef6c00'],
  [180, '#e94560'],
]

// Diverging: car/PT delta in minutes (blue = car faster → white = equal → red = PT faster)
const DELTA_MIN_COLORS = [
  [-60, '#1565c0'],
  [-30, '#42a5f5'],
  [0, '#f5f5f5'],
  [30, '#ef5350'],
  [60, '#b71c1c'],
]

// AV value unlock: capitalized CHF from AV time savings (pale yellow → deep amber)
const AV_VALUE_COLORS = [
  [10000, '#fff9c4'],
  [30000, '#fdd835'],
  [60000, '#ff8f00'],
  [100000, '#e65100'],
]

// AV upside: higher = more minutes saved by switching to AV (pale → deep green)
const AV_UPSIDE_COLORS = [
  [0, '#e8f5e9'],
  [5, '#66bb6a'],
  [10, '#2e7d32'],
  [20, '#1b5e20'],
]

// AV deal score: compound bang-for-buck (m² equivalent). Cool teal → magenta
// so it visually stands apart from AV Value Unlock (amber) and AV Upside (green)
const AV_DEAL_COLORS = [
  [0, '#263238'],
  [20, '#00838f'],
  [50, '#26a69a'],
  [100, '#ce93d8'],
  [200, '#e91e63'],
]

// Build CSS gradient for legend with proportional stop positions
function buildGradientCSS(stops) {
  const lo = stops[0][0]
  const hi = stops[stops.length - 1][0]
  const range = hi - lo || 1
  return 'linear-gradient(to right, ' +
    stops.map(([val, color]) => `${color} ${((val - lo) / range * 100).toFixed(1)}%`).join(', ') +
    ')'
}

// Map from colorBy property to its config
const METRIC_CONFIG = {
  avg_car_access: {
    colors: ACCESS_TIME_COLORS,
    gradient: buildGradientCSS(ACCESS_TIME_COLORS),
    lowLabel: '10m',
    highLabel: '180m',
  },
  avg_pt_access: {
    colors: ACCESS_TIME_COLORS,
    gradient: buildGradientCSS(ACCESS_TIME_COLORS),
    lowLabel: '10m',
    highLabel: '180m',
  },
  optimum_access: {
    colors: ACCESS_TIME_COLORS,
    gradient: buildGradientCSS(ACCESS_TIME_COLORS),
    lowLabel: '10m',
    highLabel: '180m',
  },
  car_pt_delta_min: {
    colors: DELTA_MIN_COLORS,
    gradient: buildGradientCSS(DELTA_MIN_COLORS),
    lowLabel: '\u2190 Car faster',
    highLabel: 'PT faster \u2192',
    centerLabel: 'Equal',
  },
  av_upside: {
    colors: AV_UPSIDE_COLORS,
    gradient: buildGradientCSS(AV_UPSIDE_COLORS),
    lowLabel: '0m',
    highLabel: '20m+',
  },
  av_value_unlock: {
    colors: AV_VALUE_COLORS,
    gradient: buildGradientCSS(AV_VALUE_COLORS),
    lowLabel: '10k',
    highLabel: '100k+',
  },
  av_deal_score: {
    colors: AV_DEAL_COLORS,
    gradient: buildGradientCSS(AV_DEAL_COLORS),
    lowLabel: '0 m\u00b2',
    highLabel: '200+ m\u00b2',
  },
  chf_per_m2: {
    colors: PRICE_COLORS,
    gradient: buildGradientCSS(PRICE_COLORS),
    lowLabel: '3k',
    highLabel: '16k+',
  },
}

// Build resolved metric config with quantile-based color stops.
// Each color band covers ~equal number of data points for maximum contrast.
// Falls back to static config when no bounds are available.
function getResolvedMetricConfig(property, colorBounds) {
  const b = colorBounds?.[property]
  const staticConfig = METRIC_CONFIG[property]

  // Access time metrics: quantile stops at p10, p25, p50, p75, p90
  if (['avg_car_access', 'avg_pt_access', 'optimum_access'].includes(property) && b) {
    const stops = [
      [Math.round(b.p10), '#1b5e20'],
      [Math.round(b.p25), '#4caf50'],
      [Math.round(b.p50), '#ffc107'],
      [Math.round(b.p75), '#ef6c00'],
      [Math.round(b.p90), '#e94560'],
    ]
    // Deduplicate stops with identical values (can happen with sparse data)
    const deduped = stops.filter((s, i) => i === 0 || s[0] > stops[i - 1][0])
    return { colors: deduped, gradient: buildGradientCSS(deduped),
      lowLabel: `${deduped[0][0]}m`, highLabel: `${deduped[deduped.length - 1][0]}m` }
  }

  // Diverging delta: quantile stops on each side of zero
  if (property === 'car_pt_delta_min' && b) {
    // Negative side (car faster): p10 → p25, zero, positive side: p75 → p90
    const stops = [
      [Math.round(b.p10), '#1565c0'],
      [Math.round(b.p25), '#42a5f5'],
      [0, '#f5f5f5'],
      [Math.round(b.p75), '#ef5350'],
      [Math.round(b.p90), '#b71c1c'],
    ]
    const deduped = stops.filter((s, i) => i === 0 || s[0] > stops[i - 1][0])
    return { colors: deduped, gradient: buildGradientCSS(deduped),
      lowLabel: '\u2190 Car faster', highLabel: 'PT faster \u2192', centerLabel: 'Equal' }
  }

  // AV upside: quantile stops (all values positive)
  if (property === 'av_upside' && b) {
    const stops = [
      [Math.round(b.p10), '#e8f5e9'],
      [Math.round(b.p25), '#66bb6a'],
      [Math.round(b.p75), '#2e7d32'],
      [Math.round(b.p90), '#1b5e20'],
    ]
    const deduped = stops.filter((s, i) => i === 0 || s[0] > stops[i - 1][0])
    return { colors: deduped, gradient: buildGradientCSS(deduped),
      lowLabel: `${deduped[0][0]}m`, highLabel: `${deduped[deduped.length - 1][0]}m` }
  }

  // AV value unlock: quantile stops (CHF, all positive)
  if (property === 'av_value_unlock' && b) {
    const stops = [
      [Math.round(b.p10), '#fff9c4'],
      [Math.round(b.p25), '#fdd835'],
      [Math.round(b.p75), '#ff8f00'],
      [Math.round(b.p90), '#e65100'],
    ]
    const deduped = stops.filter((s, i) => i === 0 || s[0] > stops[i - 1][0])
    return { colors: deduped, gradient: buildGradientCSS(deduped),
      lowLabel: `${Math.round(deduped[0][0] / 1000)}k`, highLabel: `${Math.round(deduped[deduped.length - 1][0] / 1000)}k` }
  }

  // AV deal score: compound bang-for-buck (m² equivalent, all positive)
  if (property === 'av_deal_score' && b) {
    const stops = [
      [Math.round(b.p10), '#263238'],
      [Math.round(b.p25), '#00838f'],
      [Math.round(b.p50), '#26a69a'],
      [Math.round(b.p75), '#ce93d8'],
      [Math.round(b.p90), '#e91e63'],
    ]
    const deduped = stops.filter((s, i) => i === 0 || s[0] > stops[i - 1][0])
    return { colors: deduped, gradient: buildGradientCSS(deduped),
      lowLabel: `${deduped[0][0]} m\u00b2`, highLabel: `${deduped[deduped.length - 1][0]}+ m\u00b2` }
  }

  return staticConfig || null
}

// Metrics that depend on price data — show no color when price is missing
const PRICE_DEPENDENT_METRICS = new Set(['chf_per_m2'])

function getColorExpression(property, colorBounds) {
  const config = getResolvedMetricConfig(property, colorBounds)
  if (!config) return ['coalesce', ['get', property], 0]
  const stops = config.colors.flatMap(([val, color]) => [val, color])

  const interpolation = [
    'interpolate',
    ['linear'],
    ['coalesce', ['get', property], 0],
    ...stops,
  ]

  // For price-dependent metrics, return transparent when value is null
  if (PRICE_DEPENDENT_METRICS.has(property)) {
    return [
      'case',
      ['==', ['get', property], null],
      'rgba(100,100,120,0.3)',
      interpolation,
    ]
  }

  return interpolation
}

// Fixed radius that only scales with zoom — no data-driven sizing
function getRadiusExpression() {
  return [
    'interpolate',
    ['linear'],
    ['zoom'],
    6, 2.5,
    8, 4.5,
    10, 7,
    12, 11,
  ]
}

// Large blurred radius for heatmap-like continuous surface
function getHeatRadiusExpression() {
  return [
    'interpolate',
    ['linear'],
    ['zoom'],
    6, 12,
    8, 25,
    10, 50,
    12, 80,
    14, 120,
  ]
}

// Legend labels for each metric
const LEGEND_LABELS = {
  avg_car_access: 'Average Car Travel Time',
  avg_pt_access: 'Average PT Travel Time',
  optimum_access: 'Optimum Accessibility (best mode)',
  car_pt_delta_min: 'Car vs PT Travel Time (minutes)',
  av_upside: 'AV Upside Potential',
  av_value_unlock: 'AV Value Unlock (CHF)',
  av_deal_score: 'AV Deal Score (m\u00b2 equivalent)',
  chf_per_m2: 'Property Price (CHF/m\u00b2)',
}

export default function MapView({
  data,
  colorBy,
  colorBounds,
  onSelect,
  onHover,
  selected,
}) {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const layersAddedRef = useRef(false)
  const [basemap, setBasemap] = useState('dark')
  const [layerMode, setLayerMode] = useState('circles')

  // Initialize map
  useEffect(() => {
    if (mapRef.current) return

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: BASEMAPS[basemap].url,
      center: [8.2275, 46.8182],
      zoom: 7.5,
      minZoom: 6,
      maxZoom: 14,
      attributionControl: false,
    })

    map.addControl(new maplibregl.NavigationControl(), 'bottom-right')
    map.addControl(
      new maplibregl.AttributionControl({ compact: true }),
      'bottom-right'
    )

    mapRef.current = map

    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  // Stable refs for callbacks so map event handlers don't go stale
  const onSelectRef = useRef(onSelect)
  const onHoverRef = useRef(onHover)
  useEffect(() => { onSelectRef.current = onSelect }, [onSelect])
  useEffect(() => { onHoverRef.current = onHover }, [onHover])
  const colorBoundsRef = useRef(colorBounds)
  useEffect(() => { colorBoundsRef.current = colorBounds }, [colorBounds])

  // Add layers to map (called when data first arrives or after basemap switch)
  function addLayersToMap(map, geojsonData, colorProp, lMode, selId) {
    if (map.getLayer('municipalities-heat')) map.removeLayer('municipalities-heat')
    if (map.getLayer('municipalities-circles')) map.removeLayer('municipalities-circles')
    if (map.getLayer('municipalities-selected')) map.removeLayer('municipalities-selected')
    if (map.getSource('municipalities')) map.removeSource('municipalities')

    map.addSource('municipalities', { type: 'geojson', data: geojsonData })

    const isHeatmap = lMode === 'heatmap'

    // Continuous surface layer: large blurred circles that blend together
    map.addLayer({
      id: 'municipalities-heat',
      type: 'circle',
      source: 'municipalities',
      layout: { visibility: isHeatmap ? 'visible' : 'none' },
      paint: {
        'circle-radius': getHeatRadiusExpression(),
        'circle-color': [
          'case',
          ['==', ['get', 'excluded'], true],
          'rgba(100,100,120,0.3)',
          getColorExpression(colorProp, colorBoundsRef.current),
        ],
        'circle-opacity': [
          'case',
          ['==', ['get', 'excluded'], true],
          0.05,
          ['==', ['get', colorProp], null],
          0.03,
          0.35,
        ],
        'circle-blur': 1,
        'circle-stroke-width': 0,
      },
    })

    // Crisp circle layer
    map.addLayer({
      id: 'municipalities-circles',
      type: 'circle',
      source: 'municipalities',
      layout: { visibility: isHeatmap ? 'none' : 'visible' },
      paint: {
        'circle-radius': getRadiusExpression(),
        'circle-color': [
          'case',
          ['==', ['get', 'excluded'], true],
          'rgba(100,100,120,0.5)',
          getColorExpression(colorProp, colorBoundsRef.current),
        ],
        'circle-opacity': [
          'case',
          ['==', ['get', 'excluded'], true],
          0.15,
          ['==', ['get', colorProp], null],
          0.12,
          0.75,
        ],
        'circle-stroke-width': 0.5,
        'circle-stroke-color': [
          'case',
          ['==', ['get', 'excluded'], true],
          'rgba(255,255,255,0.05)',
          'rgba(255,255,255,0.3)',
        ],
      },
    })

    map.addLayer({
      id: 'municipalities-selected',
      type: 'circle',
      source: 'municipalities',
      filter: ['==', ['get', 'id'], selId || ''],
      paint: {
        'circle-radius': getRadiusExpression(),
        'circle-color': 'transparent',
        'circle-stroke-width': 3,
        'circle-stroke-color': '#ffffff',
      },
    })

    map.on('click', 'municipalities-circles', (e) => {
      if (e.features?.length) onSelectRef.current(e.features[0])
    })
    map.on('click', 'municipalities-heat', (e) => {
      if (e.features?.length) onSelectRef.current(e.features[0])
    })
    map.on('mousemove', 'municipalities-circles', (e) => {
      map.getCanvas().style.cursor = 'pointer'
      if (e.features?.length) onHoverRef.current(e.features[0], e.point)
    })
    map.on('mousemove', 'municipalities-heat', (e) => {
      map.getCanvas().style.cursor = 'pointer'
      if (e.features?.length) onHoverRef.current(e.features[0], e.point)
    })
    map.on('mouseleave', 'municipalities-circles', () => {
      map.getCanvas().style.cursor = ''
      onHoverRef.current(null)
    })
    map.on('mouseleave', 'municipalities-heat', () => {
      map.getCanvas().style.cursor = ''
      onHoverRef.current(null)
    })

    layersAddedRef.current = true
  }

  // Load/update data on map
  useEffect(() => {
    const map = mapRef.current
    if (!map || !data) return

    const tryAdd = () => {
      const src = map.getSource('municipalities')
      if (src) {
        src.setData(data)
      } else {
        addLayersToMap(map, data, colorBy, layerMode, selected?.properties?.id)
      }
    }

    if (map.isStyleLoaded()) {
      tryAdd()
    } else {
      map.once('load', tryAdd)
    }
  }, [data])

  // Update paint properties when colorBy changes
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    if (map.getLayer('municipalities-circles')) {
      map.setPaintProperty(
        'municipalities-circles',
        'circle-color',
        [
          'case',
          ['==', ['get', 'excluded'], true],
          'rgba(100,100,120,0.5)',
          getColorExpression(colorBy, colorBounds),
        ]
      )
      map.setPaintProperty(
        'municipalities-circles',
        'circle-opacity',
        [
          'case',
          ['==', ['get', 'excluded'], true],
          0.15,
          ['==', ['get', colorBy], null],
          0.12,
          0.75,
        ]
      )
    }

    if (map.getLayer('municipalities-heat')) {
      map.setPaintProperty(
        'municipalities-heat',
        'circle-color',
        [
          'case',
          ['==', ['get', 'excluded'], true],
          'rgba(100,100,120,0.3)',
          getColorExpression(colorBy, colorBounds),
        ]
      )
      map.setPaintProperty(
        'municipalities-heat',
        'circle-opacity',
        [
          'case',
          ['==', ['get', 'excluded'], true],
          0.05,
          ['==', ['get', colorBy], null],
          0.03,
          0.35,
        ]
      )
    }
  }, [colorBy, colorBounds])

  // Update layer visibility for mode toggle
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const isHeatmap = layerMode === 'heatmap'

    if (map.getLayer('municipalities-heat')) {
      map.setLayoutProperty(
        'municipalities-heat',
        'visibility',
        isHeatmap ? 'visible' : 'none'
      )
    }
    if (map.getLayer('municipalities-circles')) {
      map.setLayoutProperty(
        'municipalities-circles',
        'visibility',
        isHeatmap ? 'none' : 'visible'
      )
    }
  }, [layerMode])

  // Update selected feature highlight
  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.getLayer('municipalities-selected')) return

    if (selected) {
      map.setFilter('municipalities-selected', [
        '==',
        ['get', 'id'],
        selected.properties.id,
      ])
    } else {
      map.setFilter('municipalities-selected', ['==', ['get', 'id'], ''])
    }
  }, [selected])

  // Switch basemap
  const switchBasemap = useCallback(
    (key) => {
      const map = mapRef.current
      if (!map || key === basemap) return
      setBasemap(key)
      map.setStyle(BASEMAPS[key].url)
      map.once('style.load', () => {
        layersAddedRef.current = false
        if (data) {
          addLayersToMap(map, data, colorBy, layerMode, selected?.properties?.id)
        }
      })
    },
    [basemap, data, colorBy, layerMode, selected]
  )

  // Fly to selected municipality
  useEffect(() => {
    const map = mapRef.current
    if (!map || !selected) return

    const coords = selected.geometry?.coordinates
    if (coords) {
      map.flyTo({
        center: coords,
        zoom: Math.max(map.getZoom(), 9),
        duration: 800,
      })
    }
  }, [selected])

  const config = getResolvedMetricConfig(colorBy, colorBounds)
  const gradientBg = config
    ? config.gradient
    : 'linear-gradient(to right, #1b5e20, #4caf50, #ffc107, #ef6c00, #e94560)'

  return (
    <>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      <div className="map-controls">
        {Object.entries(BASEMAPS).map(([key, { name }]) => (
          <button
            key={key}
            className={`map-control-btn ${basemap === key ? 'active' : ''}`}
            onClick={() => switchBasemap(key)}
          >
            {name}
          </button>
        ))}
        <button
          className={`map-control-btn ${layerMode === 'heatmap' ? 'active' : ''}`}
          onClick={() =>
            setLayerMode((m) => (m === 'circles' ? 'heatmap' : 'circles'))
          }
        >
          {layerMode === 'circles' ? 'Heatmap' : 'Circles'}
        </button>
      </div>
      <div className="legend">
        <div className="legend-title">
          {LEGEND_LABELS[colorBy] || colorBy.replace(/_/g, ' ')}
        </div>
        <div
          className="legend-bar"
          style={{ background: gradientBg }}
        />
        <div className="legend-labels">
          <span>{config ? config.lowLabel : '0'}</span>
          {config?.centerLabel && <span>{config.centerLabel}</span>}
          <span>{config ? config.highLabel : '100'}</span>
        </div>
      </div>
    </>
  )
}
