/**
 * Centauri Carbon Filament Dryer - Interactive Web Dashboard
 */

document.addEventListener('DOMContentLoaded', () => {
  // --- State ---
  let selectedPreset = 'petg';
  let soundEnabled = true;
  let chart = null;
  let ws = null;
  let presetsData = {};
  let currentCycleState = 'idle';
  let lightState = false;

  // --- Audio Notification (Web Audio API) ---
  const audioCtx = new (window.AudioContext || window.webkitAudioContext)();

  function playAlertChime(freq1 = 587.33, freq2 = 880.0) {
    if (!soundEnabled) return;
    try {
      if (audioCtx.state === 'suspended') {
        audioCtx.resume();
      }
      const now = audioCtx.currentTime;
      const osc1 = audioCtx.createOscillator();
      const osc2 = audioCtx.createOscillator();
      const gain = audioCtx.createGain();

      osc1.type = 'sine';
      osc1.frequency.setValueAtTime(freq1, now);
      osc1.frequency.exponentialRampToValueAtTime(freq2, now + 0.2);

      osc2.type = 'triangle';
      osc2.frequency.setValueAtTime(freq2, now + 0.2);
      osc2.frequency.exponentialRampToValueAtTime(freq2 * 1.25, now + 0.4);

      gain.gain.setValueAtTime(0.2, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.8);

      osc1.connect(gain);
      osc2.connect(gain);
      gain.connect(audioCtx.destination);

      osc1.start(now);
      osc2.start(now + 0.15);
      osc1.stop(now + 0.6);
      osc2.stop(now + 0.8);
    } catch (e) {
      console.warn('Audio playback not allowed yet:', e);
    }
  }

  // --- DOM Elements ---
  const connectionPill = document.getElementById('connection-pill');
  const connectionText = document.getElementById('connection-text');
  const btnLight = document.getElementById('btn-light');
  const lightLabel = document.getElementById('light-label');
  const btnSound = document.getElementById('btn-sound');

  const bedTempVal = document.getElementById('bed-temp-val');
  const bedTargetVal = document.getElementById('bed-target-val');
  const bedStatusBadge = document.getElementById('bed-status-badge');
  const bedBar = document.getElementById('bed-bar');

  const chamberTempVal = document.getElementById('chamber-temp-val');
  const chamberBar = document.getElementById('chamber-bar');

  const fanVal = document.getElementById('fan-val');
  const fanBadge = document.getElementById('fan-badge');
  const fanBar = document.getElementById('fan-bar');
  const auxFanVal = document.getElementById('aux-fan-val');
  const modelFanVal = document.getElementById('model-fan-val');

  const nozzleTempVal = document.getElementById('nozzle-temp-val');
  const nozzleBar = document.getElementById('nozzle-bar');

  const presetsContainer = document.getElementById('presets-container');
  const customDrawer = document.getElementById('custom-params-drawer');
  const customTempInput = document.getElementById('input-custom-temp');
  const customHoursInput = document.getElementById('input-custom-hours');

  const fanSlider = document.getElementById('fan-slider');
  const ventValDisplay = document.getElementById('vent-val-display');
  const ventChips = document.querySelectorAll('.btn-chip');

  const btnStart = document.getElementById('btn-start');
  const btnPause = document.getElementById('btn-pause');
  const btnResume = document.getElementById('btn-resume');
  const btnCancel = document.getElementById('btn-cancel');
  const cancelLabel = document.getElementById('cancel-label');
  const btnClearHistory = document.getElementById('btn-clear-history');
  const btnExportPng = document.getElementById('btn-export-png');

  const radialBar = document.getElementById('radial-bar');
  const cycleStatePill = document.getElementById('cycle-state-pill');
  const remainingTimer = document.getElementById('remaining-timer');
  const progressPercent = document.getElementById('progress-percent');
  const elapsedMeta = document.getElementById('elapsed-meta');

  const statMaterial = document.getElementById('stat-material');
  const statTargetTemp = document.getElementById('stat-target-temp');
  const statTotalDuration = document.getElementById('stat-total-duration');

  const spoolAlertBanner = document.getElementById('spool-alert-banner');
  const btnDismissAlert = document.getElementById('btn-dismiss-alert');
  const printerBusyBanner = document.getElementById('printer-busy-banner');
  const thermalAnomalyBanner = document.getElementById('thermal-anomaly-banner');
  const watchdogBadge = document.getElementById('watchdog-badge');

  const historyTableBody = document.getElementById('history-table-body');

  // --- Restore Saved Options (localStorage) ---
  const savedSound = localStorage.getItem('centauri_sound');
  if (savedSound !== null) {
    soundEnabled = savedSound === 'true';
    btnSound.classList.toggle('active', soundEnabled);
  }

  const savedCustomTemp = localStorage.getItem('centauri_custom_temp');
  if (savedCustomTemp) customTempInput.value = savedCustomTemp;

  const savedCustomHours = localStorage.getItem('centauri_custom_hours');
  if (savedCustomHours) customHoursInput.value = savedCustomHours;

  const savedFan = localStorage.getItem('centauri_fan_speed');
  if (savedFan !== null) {
    fanSlider.value = savedFan;
    updateVentDisplay(savedFan);
  }

  const savedPreset = localStorage.getItem('centauri_preset');
  if (savedPreset) {
    selectedPreset = savedPreset;
  }

  // --- Initial Setup ---
  initChart();
  fetchPresets();
  fetchHistory();
  initWebSocket();

  // --- Preset Grid Rendering ---
  async function fetchPresets() {
    try {
      const res = await fetch('/api/presets');
      presetsData = await res.json();
      renderPresets();
    } catch (err) {
      console.error('Error fetching presets:', err);
    }
  }

  function renderPresets() {
    presetsContainer.innerHTML = '';
    const presetKeys = Object.keys(presetsData);

    presetKeys.forEach((key) => {
      const p = presetsData[key];
      const card = document.createElement('div');
      card.className = `preset-card ${key === selectedPreset ? 'active' : ''}`;
      card.dataset.preset = key;
      card.innerHTML = `
        <div class="preset-card-header">
          <span class="preset-name">${p.name}</span>
        </div>
        <div class="preset-temp">${p.target_bed_temp} <span style="font-size: 0.75rem">°C</span></div>
        <div class="preset-duration">${p.duration_hours} hours</div>
      `;
      card.addEventListener('click', () => selectPreset(key));
      presetsContainer.appendChild(card);
    });

    // Add Custom Card
    const customCard = document.createElement('div');
    customCard.className = `preset-card ${selectedPreset === 'custom' ? 'active' : ''}`;
    customCard.dataset.preset = 'custom';
    customCard.innerHTML = `
      <div class="preset-card-header">
        <span class="preset-name">Custom</span>
      </div>
      <div class="preset-temp">-- <span style="font-size: 0.75rem">°C</span></div>
      <div class="preset-duration">Custom time</div>
    `;
    customCard.addEventListener('click', () => selectPreset('custom'));
    presetsContainer.appendChild(customCard);
  }

  function selectPreset(key) {
    if (currentCycleState === 'drying' || currentCycleState === 'preheating') {
      return; // Locked during active cycle
    }
    selectedPreset = key;
    localStorage.setItem('centauri_preset', key);

    document.querySelectorAll('.preset-card').forEach((c) => {
      c.classList.toggle('active', c.dataset.preset === key);
    });

    if (key === 'custom') {
      customDrawer.classList.remove('hidden');
      statMaterial.textContent = 'Custom';
      statTargetTemp.textContent = `${customTempInput.value} °C`;
      statTotalDuration.textContent = `${customHoursInput.value} h`;
    } else {
      customDrawer.classList.add('hidden');
      const p = presetsData[key];
      if (p) {
        statMaterial.textContent = p.name;
        statTargetTemp.textContent = `${p.target_bed_temp} °C`;
        statTotalDuration.textContent = `${p.duration_hours} h`;
        fanSlider.value = p.fan_box_speed;
        updateVentDisplay(p.fan_box_speed);
        localStorage.setItem('centauri_fan_speed', String(p.fan_box_speed));
      }
    }
  }

  // --- Ventilation Controls ---
  fanSlider.addEventListener('input', (e) => {
    updateVentDisplay(e.target.value);
    localStorage.setItem('centauri_fan_speed', e.target.value);
  });

  fanSlider.addEventListener('change', async (e) => {
    localStorage.setItem('centauri_fan_speed', e.target.value);
    if (currentCycleState === 'drying' || currentCycleState === 'preheating') {
      try {
        await fetch('/api/fans', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ box_fan: parseInt(e.target.value, 10) }),
        });
      } catch (err) {
        console.error('Failed to adjust fan:', err);
      }
    }
  });

  function updateVentDisplay(val) {
    ventValDisplay.textContent = `${val}%`;
    ventChips.forEach((chip) => {
      chip.classList.toggle('active', parseInt(chip.dataset.vent, 10) === parseInt(val, 10));
    });
  }

  ventChips.forEach((chip) => {
    chip.addEventListener('click', () => {
      const val = parseInt(chip.dataset.vent, 10);
      fanSlider.value = val;
      updateVentDisplay(val);
      localStorage.setItem('centauri_fan_speed', String(val));
      fanSlider.dispatchEvent(new Event('change'));
    });
  });

  customTempInput.addEventListener('input', () => {
    localStorage.setItem('centauri_custom_temp', customTempInput.value);
    if (selectedPreset === 'custom') {
      statTargetTemp.textContent = `${customTempInput.value} °C`;
    }
  });

  customHoursInput.addEventListener('input', () => {
    localStorage.setItem('centauri_custom_hours', customHoursInput.value);
    if (selectedPreset === 'custom') {
      statTotalDuration.textContent = `${customHoursInput.value} h`;
    }
  });

  // --- Sound Toggle ---
  btnSound.addEventListener('click', () => {
    soundEnabled = !soundEnabled;
    btnSound.classList.toggle('active', soundEnabled);
    localStorage.setItem('centauri_sound', String(soundEnabled));
  });

  // --- Light Toggle ---
  btnLight.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/light/toggle', { method: 'POST' });
      const data = await res.json();
      updateLightUI(data.light_on);
    } catch (e) {
      console.error('Failed to toggle light:', e);
    }
  });

  function updateLightUI(isOn) {
    lightState = isOn;
    btnLight.classList.toggle('active', isOn);
    lightLabel.textContent = `Light: ${isOn ? 'ON' : 'OFF'}`;
  }

  // --- Cycle Action Handlers ---
  btnStart.addEventListener('click', async () => {
    const payload = {
      preset_id: selectedPreset,
      fan_box_speed: parseInt(fanSlider.value, 10),
    };
    if (selectedPreset === 'custom') {
      payload.custom_temp = parseFloat(customTempInput.value);
      payload.custom_duration_hours = parseFloat(customHoursInput.value);
    }

    try {
      btnStart.disabled = true;
      const res = await fetch('/api/cycle/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        alert(data.detail || 'Error starting cycle');
      } else {
        playAlertChime(440, 660);
      }
    } catch (e) {
      alert(`Connection error: ${e.message}`);
    } finally {
      btnStart.disabled = false;
    }
  });

  btnPause.addEventListener('click', async () => {
    try {
      await fetch('/api/cycle/pause', { method: 'POST' });
    } catch (e) {
      console.error('Pause failed:', e);
    }
  });

  btnResume.addEventListener('click', async () => {
    try {
      await fetch('/api/cycle/resume', { method: 'POST' });
    } catch (e) {
      console.error('Resume failed:', e);
    }
  });

  btnCancel.addEventListener('click', async () => {
    if (currentCycleState === 'drying' || currentCycleState === 'preheating' || currentCycleState === 'paused') {
      if (confirm('Are you sure you want to cancel the drying cycle and cool down the bed?')) {
        try {
          await fetch('/api/cycle/cancel', { method: 'POST' });
          playAlertChime(400, 250);
        } catch (e) {
          console.error('Cancel failed:', e);
        }
      }
    } else {
      if (confirm('Do you want to turn off the heated bed and ensure heaters are at 0 °C?')) {
        try {
          await fetch('/api/heaters/off', { method: 'POST' });
          playAlertChime(400, 250);
        } catch (e) {
          console.error('Turn off failed:', e);
        }
      }
    }
  });

  if (btnClearHistory) {
    btnClearHistory.addEventListener('click', async () => {
      if (confirm('Are you sure you want to clear all recorded drying history?')) {
        try {
          await fetch('/api/history', { method: 'DELETE' });
          fetchHistory();
        } catch (e) {
          console.error('Failed to clear history:', e);
        }
      }
    });
  }

  if (btnExportPng) {
    btnExportPng.addEventListener('click', () => {
      if (!chart) return;
      const a = document.createElement('a');
      a.href = chart.toBase64Image();
      const nowStr = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
      a.download = `centauri_thermal_evolution_${nowStr}.png`;
      a.click();
    });
  }

  btnDismissAlert.addEventListener('click', () => {
    spoolAlertBanner.classList.add('hidden');
  });

  // --- History Table ---
  async function fetchHistory() {
    try {
      const res = await fetch('/api/history');
      const records = await res.json();
      renderHistory(records);
    } catch (e) {
      console.error('Error fetching history:', e);
    }
  }

  function renderHistory(records) {
    if (!records || records.length === 0) {
      historyTableBody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No history recorded yet.</td></tr>';
      return;
    }
    historyTableBody.innerHTML = '';
    records.slice(0, 10).forEach((rec) => {
      const tr = document.createElement('tr');
      const stateBadgeClass =
        rec.state === 'completed'
          ? 'badge-green'
          : rec.state === 'cancelled'
          ? 'badge-neutral'
          : 'badge-cyan';

      const stateLabel =
        rec.state === 'completed'
          ? 'Completed'
          : rec.state === 'cancelled'
          ? 'Cancelled'
          : rec.state.toUpperCase();

      const durationStr = formatSeconds(rec.elapsed_seconds || 0);

      tr.innerHTML = `
        <td>${rec.recorded_at || '--'}</td>
        <td><strong>${rec.preset_name || 'Unknown'}</strong></td>
        <td>${rec.target_bed_temp} °C</td>
        <td>${durationStr}</td>
        <td><span class="badge ${stateBadgeClass}">${stateLabel}</span></td>
      `;
      historyTableBody.appendChild(tr);
    });
  }

  // --- WebSocket Telemetry Stream ---
  function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/telemetry`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      connectionPill.className = 'status-pill online';
      connectionText.textContent = 'ONLINE';
    };

    ws.onclose = () => {
      connectionPill.className = 'status-pill offline';
      connectionText.textContent = 'Offline (Retrying...)';
      setTimeout(initWebSocket, 2000);
    };

    ws.onerror = () => {
      ws.close();
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleTelemetryUpdate(data);
      } catch (e) {
        console.error('Error parsing WS frame:', e);
      }
    };
  }

  function handleTelemetryUpdate(payload) {
    const { printer, telemetry, session, temp_history } = payload;

    const printerIp = printer?.ip || '';
    const mainboardId = printer?.mainboard_id || '--';

    // Update dynamic footer elements
    const footerIp = document.getElementById('footer-printer-ip');
    const footerId = document.getElementById('footer-printer-id');
    if (footerIp && printerIp) footerIp.textContent = printerIp;
    if (footerId && mainboardId) footerId.textContent = mainboardId;

    // 1. Connection status
    if (telemetry.connected) {
      connectionPill.className = 'status-pill online';
      connectionText.textContent = printerIp ? `ONLINE (${printerIp})` : 'ONLINE';
    } else {
      connectionPill.className = 'status-pill offline';
      connectionText.textContent = 'No Response';
    }

    // 2. Printer Busy Check
    if (telemetry.print_status === 13) {
      printerBusyBanner.classList.remove('hidden');
      btnStart.disabled = true;
    } else {
      printerBusyBanner.classList.add('hidden');
    }

    // 3. Bed Readings
    bedTempVal.textContent = telemetry.bed_temp.toFixed(1);
    bedTargetVal.textContent = `${telemetry.bed_target.toFixed(1)} °C`;
    const bedPct = Math.min(100, Math.max(0, (telemetry.bed_temp / 100) * 100));
    bedBar.style.width = `${bedPct}%`;

    if (telemetry.bed_target > 0) {
      const diff = Math.abs(telemetry.bed_temp - telemetry.bed_target);
      if (diff <= 2.0) {
        bedStatusBadge.className = 'badge badge-green';
        bedStatusBadge.textContent = 'Stable';
      } else {
        bedStatusBadge.className = 'badge badge-orange';
        bedStatusBadge.textContent = 'Heating';
      }
    } else {
      bedStatusBadge.className = 'badge badge-neutral';
      bedStatusBadge.textContent = 'Standby';
    }

    // 4. Chamber (Box) Readings
    chamberTempVal.textContent = telemetry.chamber_temp.toFixed(1);
    const chamberPct = Math.min(100, Math.max(0, (telemetry.chamber_temp / 60) * 100));
    chamberBar.style.width = `${chamberPct}%`;

    // 5. Fan Readings
    fanVal.textContent = telemetry.box_fan;
    fanBadge.textContent = `${telemetry.box_fan}%`;
    fanBar.style.width = `${telemetry.box_fan}%`;
    auxFanVal.textContent = `${telemetry.aux_fan}%`;
    modelFanVal.textContent = `${telemetry.model_fan}%`;

    // 6. Nozzle Readings
    nozzleTempVal.textContent = telemetry.nozzle_temp.toFixed(1);
    const nozzlePct = Math.min(100, Math.max(0, (telemetry.nozzle_temp / 280) * 100));
    nozzleBar.style.width = `${nozzlePct}%`;

    // 7. Light
    updateLightUI(telemetry.light_on);

    // 8. Session / Timer / State Machine UI
    updateSessionUI(session);

    // 9. Update Real-time Chart
    if (temp_history && temp_history.length > 0) {
      updateChart(temp_history);
    }
  }

  function updateSessionUI(session) {
    if (!session) return;
    currentCycleState = session.state;

    // Progress circle math (radius = 86, circumference = 2 * PI * 86 = 540.35)
    const circumference = 540.35;
    const progress = session.progress_percent || 0.0;
    const offset = circumference - (progress / 100.0) * circumference;
    radialBar.style.strokeDashoffset = offset;

    // Remaining timer
    const remSec = session.remaining_seconds || 0;
    remainingTimer.textContent = formatSeconds(remSec);
    progressPercent.textContent = `${progress.toFixed(1)}% elapsed`;

    if (session.start_time) {
      const date = new Date(session.start_time * 1000);
      elapsedMeta.textContent = `Started: ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
    } else {
      elapsedMeta.textContent = 'Started: --:--';
    }

    // State Pill & Action Buttons
    cycleStatePill.className = `cycle-pill ${session.state}`;
    switch (session.state) {
      case 'preheating':
        cycleStatePill.textContent = 'PREHEATING';
        btnStart.classList.add('hidden');
        btnPause.classList.remove('hidden');
        btnPause.disabled = false;
        btnResume.classList.add('hidden');
        btnCancel.classList.remove('hidden');
        btnCancel.disabled = false;
        cancelLabel.textContent = 'Stop / Cool Down';
        break;

      case 'drying':
        cycleStatePill.textContent = 'DRYING';
        btnStart.classList.add('hidden');
        btnPause.classList.remove('hidden');
        btnPause.disabled = false;
        btnResume.classList.add('hidden');
        btnCancel.classList.remove('hidden');
        btnCancel.disabled = false;
        cancelLabel.textContent = 'Stop / Cool Down';
        break;

      case 'paused':
        cycleStatePill.textContent = 'PAUSED';
        btnStart.classList.add('hidden');
        btnPause.classList.add('hidden');
        btnResume.classList.remove('hidden');
        btnResume.disabled = false;
        btnCancel.classList.remove('hidden');
        btnCancel.disabled = false;
        cancelLabel.textContent = 'Stop / Cool Down';
        break;

      case 'completed':
        cycleStatePill.textContent = 'COMPLETED';
        btnStart.classList.remove('hidden');
        btnStart.disabled = false;
        btnPause.classList.add('hidden');
        btnResume.classList.add('hidden');
        btnCancel.classList.remove('hidden');
        btnCancel.disabled = false;
        cancelLabel.textContent = 'Turn Off Bed';
        fetchHistory(); // Refresh history
        break;

      case 'cancelled':
        cycleStatePill.textContent = 'CANCELLED';
        btnStart.classList.remove('hidden');
        btnStart.disabled = false;
        btnPause.classList.add('hidden');
        btnResume.classList.add('hidden');
        btnCancel.classList.remove('hidden');
        btnCancel.disabled = false;
        cancelLabel.textContent = 'Turn Off Bed';
        fetchHistory();
        break;

      default: // idle
        cycleStatePill.textContent = 'IDLE';
        btnStart.classList.remove('hidden');
        btnStart.disabled = false;
        btnPause.classList.add('hidden');
        btnResume.classList.add('hidden');
        btnCancel.classList.remove('hidden');
        btnCancel.disabled = false;
        cancelLabel.textContent = 'Turn Off Bed';
        break;
    }

    // Watchdog status pill
    if (session.state === 'drying' || session.state === 'preheating') {
      watchdogBadge.className = 'badge badge-green';
      if (session.thermal_watchdog_triggers > 0) {
        watchdogBadge.textContent = `Watchdog: ${session.thermal_watchdog_triggers}x Triggered`;
      } else {
        watchdogBadge.textContent = 'Watchdog: Active';
      }
    } else {
      watchdogBadge.className = 'badge badge-neutral';
      watchdogBadge.textContent = 'Anti-Timeout';
    }

    // Thermal anomaly alert banner
    if (session.temp_anomaly_detected && (session.state === 'drying' || session.state === 'preheating')) {
      if (thermalAnomalyBanner.classList.contains('hidden')) {
        thermalAnomalyBanner.classList.remove('hidden');
        playAlertChime(493.88, 369.99);
      }
    } else {
      thermalAnomalyBanner.classList.add('hidden');
    }

    // Spool flip reminder banner
    if (session.flip_spool_reminded && (session.state === 'drying' || session.state === 'preheating')) {
      if (spoolAlertBanner.classList.contains('hidden')) {
        spoolAlertBanner.classList.remove('hidden');
        playAlertChime(659.25, 880.0);
      }
    } else {
      spoolAlertBanner.classList.add('hidden');
    }
  }

  function formatSeconds(sec) {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  // --- Real-Time Thermal Chart ---
  function initChart() {
    const ctx = document.getElementById('thermalChart').getContext('2d');
    chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          {
            label: 'Bed (°C)',
            data: [],
            borderColor: '#ff3d71',
            backgroundColor: 'rgba(255, 61, 113, 0.1)',
            borderWidth: 2,
            tension: 0.3,
            pointRadius: 0,
          },
          {
            label: 'Target (°C)',
            data: [],
            borderColor: '#ff9100',
            borderDash: [4, 4],
            borderWidth: 1.5,
            tension: 0,
            pointRadius: 0,
          },
          {
            label: 'Chamber (°C)',
            data: [],
            borderColor: '#00e5ff',
            backgroundColor: 'rgba(0, 229, 255, 0.05)',
            borderWidth: 2,
            tension: 0.3,
            pointRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        scales: {
          x: {
            display: false,
          },
          y: {
            min: 20,
            max: 100,
            ticks: {
              color: '#8c9ba8',
              font: { family: 'JetBrains Mono', size: 10 },
              callback: (val) => `${val}°C`,
            },
            grid: {
              color: 'rgba(255, 255, 255, 0.05)',
            },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            mode: 'index',
            intersect: false,
            backgroundColor: 'rgba(17, 23, 38, 0.9)',
            titleColor: '#fff',
            bodyColor: '#8c9ba8',
            borderColor: 'rgba(255, 255, 255, 0.1)',
            borderWidth: 1,
          },
        },
      },
    });
  }

  function updateChart(history) {
    if (!chart) return;
    const windowPoints = history.slice(-60); // Last ~60 points
    chart.data.labels = windowPoints.map((p) => {
      const d = new Date(p.timestamp * 1000);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    });
    chart.data.datasets[0].data = windowPoints.map((p) => p.bed);
    chart.data.datasets[1].data = windowPoints.map((p) => p.bed_target);
    chart.data.datasets[2].data = windowPoints.map((p) => p.chamber);
    chart.update();
  }
});
