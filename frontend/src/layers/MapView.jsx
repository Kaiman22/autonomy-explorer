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

// Standard 0-100 score colors (blue → purple → orange → red)
const SCORE_COLORS = [
  [0, '#1a237e'],
  [20, '#283593'],
  [35, '#4527a0'],
  [50, '#f57f17'],
  [65, '#ef6c00'],
  [80, '#e65100'],
  [90, '#e94560'],
  [100, '#ff1744'],
]

// Price colors (green → yellow → orange → red)
const PRICE_COLORS = [
  [3000, '#1b5e20'],
  [5000, '#4caf50'],
  [7000, '#ffc107'],
  [10000, '#ef6c00'],
  [13000, '#e65100'],
  [16000, '#e94560'],
]

// Map from colorBy property to its config
const METRIC_CONFIG = {
  chf_per_m2: {
    colors: PRICE_COLORS,
    gradient: 'linear-gradient(to right, #1b5e20, #4caf50, #ffc107, #ef6c00, #e94560)',
    lowLabel: '3k',
    highLabel: '16k+',
  },
}

function getColorExpression(property) {
  const config = METRIC_CONFIG[property]
  const colors = config ? config.colors : SCORE_COLORS
  const stops = colors.flatMap(([val, color]) => [val, color])
  return [
    'interpolate',
    ['linear'],
    ['coalesce', ['get', property], 0],
    ...stops,
  ]
}

// Fixed radius that only scales with zoom — no data-driven sizing
// Scaled down for PLZ-level density (~3,181 points vs old 2,128 municipalities)
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

// Legend labels for each metric
const LEGEND_LABELS = {
  autonomy_score: 'Compound Score',
  chf_per_m2: 'Property Price (CHF/m²)',
  score_status_quo: 'Status-Quo Accessibility',
  score_attractiveness: 'Inherent Attractiveness',
  score_post_av: 'Post-Autonomy Accessibility',
  score_delta: 'Accessibility Delta',
  score_accessibility: 'Accessibility Gain',
}

export default function MapView({
  data,
  colorBy,
  filterCity,
  weights,
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

  // Add layers to map (called when data first arrives or after basemap switch)
  function addLayersToMap(map, geojsonData, colorProp, lMode, selId) {
    if (map.getLayer('municipalities-heat')) map.removeLayer('municipalities-heat')
    if (map.getLayer('municipalities-circles')) map.removeLayer('municipalities-circles')
    if (map.getLayer('municipalities-selected')) map.removeLayer('municipalities-selected')
    if (map.getSource('municipalities')) map.removeSource('municipalities')

    map.addSource('municipalities', { type: 'geojson', data: geojsonData })

    map.addLayer({
      id: 'municipalities-heat',
      type: 'heatmap',
      source: 'municipalities',
      maxzoom: 10,
      layout: { visibility: lMode === 'heatmap' ? 'visible' : 'none' },
      paint: {
        'heatmap-weight': ['interpolate', ['linear'], ['coalesce', ['get', colorProp], 0], 0, 0, 100, 1],
        'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 6, 1, 10, 3],
        'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 6, 15, 10, 30],
        'heatmap-color': [
          'interpolate', ['linear'], ['heatmap-density'],
          0, 'rgba(0,0,0,0)', 0.2, '#1a237e', 0.4, '#4527a0',
          0.6, '#f57f17', 0.8, '#e65100', 1, '#e94560',
        ],
        'heatmap-opacity': ['interpolate', ['linear'], ['zoom'], 7, 0.8, 10, 0],
      },
    })

    map.addLayer({
      id: 'municipalities-circles',
      type: 'circle',
      source: 'municipalities',
      paint: {
        'circle-radius': getRadiusExpression(),
        'circle-color': [
          'case',
          ['==', ['get', 'excluded'], true],
          'rgba(100,100,120,0.5)',  // grey for excluded
          getColorExpression(colorProp),
        ],
        'circle-opacity': [
          'case',
          ['==', ['get', 'excluded'], true],
          0.15,                     // very faint for excluded
          ['==', ['get', colorProp], null],
          0.2,
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
    map.on('mousemove', 'municipalities-circles', (e) => {
      map.getCanvas().style.cursor = 'pointer'
      if (e.features?.length) onHoverRef.current(e.features[0], e.point)
    })
    map.on('mouseleave', 'municipalities-circles', () => {
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
    if (!map || !map.getLayer('municipalities-circles')) return

    map.setPaintProperty(
      'municipalities-circles',
      'circle-color',
      [
        'case',
        ['==', ['get', 'excluded'], true],
        'rgba(100,100,120,0.5)',
        getColorExpression(colorBy),
      ]
    )
  }, [colorBy])

  // Update layer visibility for mode toggle
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (map.getLayer('municipalities-heat')) {
      map.setLayoutProperty(
        'municipalities-heat',
        'visibility',
        layerMode === 'heatmap' ? 'visible' : 'none'
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

  const config = METRIC_CONFIG[colorBy]
  const gradientBg = config
    ? config.gradient
    : 'linear-gradient(to right, #1a237e, #4527a0, #f57f17, #e65100, #e94560)'

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
          <span>{config ? config.lowLabel : 'Low'}</span>
          <span>{config ? config.highLabel : 'High'}</span>
        </div>
      </div>
    </>
  )
}
