// Core Application Controller for CamCounter AI Dashboard

let currentCameraId = null;
let allCameras = [];
let ws = null;
let editor = null;
let browserCamStream = null; // Active browser camera stream

// =============================================
// Backend connectivity helpers
// =============================================

/**
 * fetch() + JSON parse with clear, specific error messages instead of the
 * cryptic "Unexpected end of JSON input" / WebKit "did not match the
 * expected pattern" you get from calling res.json() on a non-OK or empty
 * response (e.g. the API route 404ing because the backend isn't deployed
 * behind this host).
 */
async function apiFetchJson(url, options) {
  let res;
  try {
    res = await fetch(url, options);
  } catch (networkErr) {
    throw new Error(`Cannot reach the server at ${url} (${networkErr.message})`);
  }
  if (!res.ok) {
    const bodyText = await res.text().catch(() => '');
    throw new Error(`Server returned ${res.status} ${res.statusText} for ${url}${bodyText ? ': ' + bodyText.slice(0, 200) : ' (empty response)'}`);
  }
  const text = await res.text();
  if (!text) {
    return null; // e.g. a 204/empty-body success response
  }
  try {
    return JSON.parse(text);
  } catch (parseErr) {
    throw new Error(`Server response from ${url} wasn't valid JSON: ${text.slice(0, 200)}`);
  }
}

function showBackendError(message) {
  const banner = document.getElementById('backend-error-banner');
  const detail = document.getElementById('backend-error-detail');
  if (detail) detail.textContent = message || "The dashboard can't load data from the backend right now.";
  if (banner) banner.style.display = 'flex';
}

function hideBackendError() {
  const banner = document.getElementById('backend-error-banner');
  if (banner) banner.style.display = 'none';
}

/**
 * Races a promise against a timeout so a hung browser API (e.g. a camera
 * permission prompt that never resolves or rejects) fails loudly instead of
 * leaving the UI stuck silently forever.
 */
function withTimeout(promise, ms, timeoutMessage) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(timeoutMessage)), ms);
    promise.then(
      (val) => { clearTimeout(timer); resolve(val); },
      (err) => { clearTimeout(timer); reject(err); }
    );
  });
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
  window.ChartsManager.initCharts();
  editor = new window.CanvasEditor('interactive-editor-canvas', 'stream-viewport-container');

  setupEventListeners();
  loadCameras().then(() => {
    detectSystemCameras();
  });
  connectWebSocket();
});

// =============================================
// System Camera Auto-Detection
// =============================================

let detectedCameraDevices = []; // Populated by detectSystemCameras(): every videoinput on this device

/**
 * Detects every camera available on the device the dashboard is open on
 * (phone, laptop, desktop webcam -- anything getUserMedia can see) and
 * shows a one-tap banner to start streaming, so users don't have to
 * manually add a camera through the modal. Works identically on mobile
 * and desktop; the "Phone Camera" framing was misleading, this isn't
 * mobile-specific.
 */
async function detectSystemCameras() {
  try {
    // Skip if not a secure context (getUserMedia won't work anyway)
    if (!window.isSecureContext) return;

    // Skip if getUserMedia is not supported
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;

    // Skip if there's already a browser-type camera configured
    const hasBrowserCam = allCameras.some(c => c.source_type === 'browser');
    if (hasBrowserCam) return;

    // Enumerate available video input devices. Note: device.label is only
    // populated once permission has been granted at least once in this
    // origin -- before that browsers return devices with blank labels for
    // privacy, so we fall back to "Camera N".
    let cameras = [];
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      cameras = devices.filter(d => d.kind === 'videoinput');
    } catch {
      // enumerateDevices itself failed; assume a camera might still be
      // available on touch-capable devices (phones/tablets) and let
      // getUserMedia be the real source of truth when the user taps Start.
      if ('ontouchstart' in window || navigator.maxTouchPoints > 0) {
        cameras = [{ deviceId: '', label: '' }];
      }
    }

    if (cameras.length === 0) return;
    detectedCameraDevices = cameras;

    // Show the auto-detect banner
    const banner = document.getElementById('mobile-cam-banner');
    const subtitle = document.getElementById('mobile-cam-banner-subtitle');
    const select = document.getElementById('mobile-cam-device-select');
    banner.style.display = 'flex';

    if (cameras.length > 1) {
      subtitle.textContent = `${cameras.length} cameras found on this device -- pick one and tap Start`;
      select.innerHTML = cameras.map((cam, i) =>
        `<option value="${i}">${escapeHtml(cam.label || `Camera ${i + 1}`)}</option>`
      ).join('');
      select.style.display = '';
    } else {
      subtitle.textContent = 'Tap to start live AI people counting with your camera';
      select.style.display = 'none';
    }

    // Wire up the one-tap start button
    document.getElementById('btn-auto-start-cam').addEventListener('click', async () => {
      await autoStartSystemCamera();
    }, { once: true });

  } catch (err) {
    console.warn('System camera auto-detection failed:', err);
  }
}

/**
 * Auto-creates a browser camera feed from the detected/selected device and
 * starts streaming -- one tap, no modal.
 */
async function autoStartSystemCamera() {
  const banner = document.getElementById('mobile-cam-banner');
  const btn = document.getElementById('btn-auto-start-cam');
  const select = document.getElementById('mobile-cam-device-select');

  // Show loading state
  btn.disabled = true;
  btn.innerHTML = `
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="spin-icon">
      <circle cx="12" cy="12" r="10" stroke-dasharray="31.4 31.4" stroke-linecap="round"/>
    </svg>
    Starting...
  `;

  try {
    // If the user picked a specific device from the dropdown, target it
    // directly. Otherwise fall back to a facingMode guess (back camera,
    // unless there's only one camera on the device -- e.g. a laptop).
    let sourceSpec = 'environment';
    if (select.style.display !== 'none' && select.value !== '') {
      const chosen = detectedCameraDevices[parseInt(select.value, 10)];
      if (chosen && chosen.deviceId) {
        sourceSpec = 'device:' + chosen.deviceId;
      }
    } else if (detectedCameraDevices.length === 1) {
      sourceSpec = 'user';
    }

    // Create the camera on the backend
    const payload = {
      id: 'cam_' + Date.now().toString(36),
      name: 'This Device Camera',
      source_type: 'browser',
      source_url: sourceSpec,
      enabled: true,
      alert_max_occupancy: 20,
      lines: [],
      zones: []
    };

    const saved = await apiFetchJson('/api/cameras', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    hideBackendError();

    // Hide the banner
    banner.style.display = 'none';

    // Reload cameras and select the new one
    await loadCameras();
    selectCamera(saved.id);

  } catch (err) {
    console.error('Failed to auto-start system camera:', err);
    showBackendError(err.message);
    btn.disabled = false;
    btn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polygon points="5 3 19 12 5 21 5 3"></polygon>
      </svg>
      Retry
    `;
    // Re-attach the click handler for retry
    btn.addEventListener('click', async () => {
      await autoStartSystemCamera();
    }, { once: true });
  }
}

// =============================================
// Browser Camera Stream (any camera on this device: phone, laptop, desktop webcam)
// =============================================

class BrowserCameraStream {
  constructor(cameraId, sourceSpec = 'environment') {
    this.cameraId = cameraId;
    // sourceSpec is either a facingMode ('environment'/'user') or a specific
    // device pick encoded as 'device:<deviceId>' (see detectSystemCameras()).
    if (typeof sourceSpec === 'string' && sourceSpec.startsWith('device:')) {
      this.deviceId = sourceSpec.slice('device:'.length);
      this.facingMode = null;
    } else {
      this.facingMode = sourceSpec || 'environment';
      this.deviceId = null;
    }
    this.video = document.getElementById('browser-cam-video');
    this.captureCanvas = document.getElementById('browser-cam-canvas');
    this.captureCtx = this.captureCanvas.getContext('2d');
    this.ws = null;
    this.mediaStream = null;
    this.running = false;
    this.sendInterval = null;
    this.targetFps = 8; // Send 8 frames/sec to backend
  }

  async start() {
    try {
      // Check for secure context (getUserMedia requires HTTPS on mobile)
      if (!window.isSecureContext) {
        const msg = 'Camera access requires a secure connection (HTTPS). ' +
          'You are currently on an insecure connection. ' +
          'Please access this page via HTTPS or localhost.';
        console.error(msg);
        alert(msg);
        throw new Error(msg);
      }

      // Check for getUserMedia support
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        const msg = 'Your browser does not support camera access (getUserMedia). ' +
          'Please use a modern browser like Chrome, Safari, or Firefox.';
        console.error(msg);
        alert(msg);
        throw new Error(msg);
      }

      // Request the specific device the user picked (if any), otherwise an
      // ideal (not exact) facingMode to prevent OverconstrainedError on
      // devices that can't satisfy it. Falls back to any available camera
      // if the preferred one isn't available.
      const primaryConstraint = this.deviceId
        ? { deviceId: { exact: this.deviceId }, width: { ideal: 640 }, height: { ideal: 480 } }
        : { facingMode: { ideal: this.facingMode }, width: { ideal: 640 }, height: { ideal: 480 } };

      // getUserMedia's returned promise can hang indefinitely if the
      // permission prompt gets stuck (blocked by browser/OS policy,
      // dismissed without a proper reject, camera locked by another app,
      // etc.) -- race it against a timeout so that shows up as a clear
      // error instead of a silently-stuck "Starting camera..." forever.
      const GUM_TIMEOUT_MS = 15000;
      const GUM_TIMEOUT_MSG = "Camera permission prompt didn't respond in time. " +
        "Check your browser's address bar for a blocked-camera icon, make sure no " +
        "other app or tab is using the camera, then try again.";

      let stream = null;
      try {
        stream = await withTimeout(
          navigator.mediaDevices.getUserMedia({ video: primaryConstraint, audio: false }),
          GUM_TIMEOUT_MS,
          GUM_TIMEOUT_MSG
        );
      } catch (firstErr) {
        const target = this.deviceId ? `device '${this.deviceId}'` : `facingMode '${this.facingMode}'`;
        console.warn(`Camera with ${target} failed: ${firstErr.message}. Trying any camera...`);
        // Fallback: request any available camera without facingMode constraint
        try {
          stream = await withTimeout(
            navigator.mediaDevices.getUserMedia({
              video: { width: { ideal: 640 }, height: { ideal: 480 } },
              audio: false
            }),
            GUM_TIMEOUT_MS,
            GUM_TIMEOUT_MSG
          );
        } catch (fallbackErr) {
          console.error('All camera access attempts failed:', fallbackErr);
          if (fallbackErr.name === 'NotAllowedError') {
            alert('Camera permission was denied. Please allow camera access in your browser settings and try again.');
          } else if (fallbackErr.name === 'NotFoundError') {
            alert('No camera found on this device. Please ensure a camera is available.');
          } else {
            alert('Could not access the camera: ' + fallbackErr.message);
          }
          throw fallbackErr;
        }
      }

      this.mediaStream = stream;
      this.video.srcObject = this.mediaStream;
      this.video.setAttribute('playsinline', 'true'); // Required for iOS Safari
      this.video.setAttribute('autoplay', 'true');
      this.video.muted = true;

      // Play the video - wrapped in user gesture handling for mobile
      try {
        await this.video.play();
      } catch (playErr) {
        console.warn('Auto-play failed, retrying:', playErr);
        // On some mobile browsers, play() requires a user gesture
        await new Promise(resolve => setTimeout(resolve, 100));
        await this.video.play();
      }

      // Wait for video to actually have dimensions (not just loadeddata)
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
          // After 5 seconds, proceed with whatever we have
          console.warn('Timed out waiting for video dimensions, using defaults');
          resolve();
        }, 5000);

        const checkDimensions = () => {
          if (this.video.videoWidth > 0 && this.video.videoHeight > 0) {
            clearTimeout(timeout);
            resolve();
          }
        };

        // Check immediately
        checkDimensions();
        // Also listen for metadata/data events
        this.video.addEventListener('loadedmetadata', checkDimensions, { once: true });
        this.video.addEventListener('loadeddata', checkDimensions, { once: true });
        // Poll as a final fallback
        const poll = setInterval(() => {
          if (this.video.videoWidth > 0 && this.video.videoHeight > 0) {
            clearInterval(poll);
            clearTimeout(timeout);
            resolve();
          }
        }, 100);
        // Clear poll on timeout too
        setTimeout(() => clearInterval(poll), 5100);
      });

      // Set canvas to match actual video dimensions
      const vw = this.video.videoWidth || 640;
      const vh = this.video.videoHeight || 480;
      this.captureCanvas.width = vw;
      this.captureCanvas.height = vh;
      console.log(`Browser camera ready: ${vw}x${vh}, facingMode=${this.facingMode}`);

      // Open WebSocket to backend
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws/browser-cam/${encodeURIComponent(this.cameraId)}`;
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        console.log(`Browser camera WebSocket connected for ${this.cameraId}`);
        this.running = true;
        this._startSendingFrames();
      };

      this.ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'result') {
            // Display annotated frame from backend
            if (msg.frame) {
              const streamImg = document.getElementById('main-stream-img');
              streamImg.src = msg.frame;
            }
            // Update analytics if this is the current camera
            if (msg.analytics && this.cameraId === currentCameraId) {
              updateDashboardFromAnalytics(msg.analytics);
            }
          } else if (msg.type === 'error') {
            console.error('Browser cam error:', msg.message);
          }
        } catch (err) {
          console.error('Error parsing browser cam WS message:', err);
        }
      };

      this.ws.onclose = () => {
        console.log('Browser camera WebSocket closed');
        this.running = false;
      };

      this.ws.onerror = (err) => {
        console.error('Browser camera WebSocket error:', err);
      };

    } catch (err) {
      console.error('Failed to start browser camera:', err);
      // Clean up any partially acquired resources
      if (this.mediaStream) {
        this.mediaStream.getTracks().forEach(t => t.stop());
        this.mediaStream = null;
      }
      this.video.srcObject = null;
      throw err;
    }
  }

  _startSendingFrames() {
    const interval = 1000 / this.targetFps;
    this.sendInterval = setInterval(() => {
      if (!this.running || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      // Don't send if a previous frame is still buffered
      if (this.ws.bufferedAmount > 50000) return;

      // Capture frame from video
      this.captureCtx.drawImage(this.video, 0, 0, this.captureCanvas.width, this.captureCanvas.height);
      const dataUrl = this.captureCanvas.toDataURL('image/jpeg', 0.7);

      // Send to backend
      this.ws.send(JSON.stringify({
        type: 'frame',
        data: dataUrl
      }));
    }, interval);
  }

  stop() {
    this.running = false;

    if (this.sendInterval) {
      clearInterval(this.sendInterval);
      this.sendInterval = null;
    }

    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }

    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach(track => track.stop());
      this.mediaStream = null;
    }

    this.video.srcObject = null;
    console.log('Browser camera stream stopped');
  }
}


// Update dashboard KPIs from analytics data (used by both WS telemetry and browser cam)
function updateDashboardFromAnalytics(data) {
  document.getElementById('val-current-occupancy').textContent = data.current_occupancy;
  document.getElementById('val-total-in').textContent = data.total_in;
  document.getElementById('val-total-out').textContent = data.total_out;
  document.getElementById('val-net-flow').textContent = data.net_flow >= 0 ? `+${data.net_flow}` : data.net_flow;
  document.getElementById('val-peak-occupancy').textContent = data.peak_occupancy;
  document.getElementById('val-fps').textContent = data.fps ? data.fps.toFixed(1) : '0.0';

  // Update Chart.js
  window.ChartsManager.updateLiveOccupancyChart(data.current_occupancy);
  window.ChartsManager.updateHourlyChart(data.total_in, data.total_out);

  // Update Zones Table
  renderZonesTable(data.zones);

  // Update Tripwires Table
  renderLinesTable(data.lines);

  // Update Demographics (Age / Gender / Emotion) - only present when enabled on this camera
  renderDemographicsCard(data.demographics);

  // Update Event Stream Log
  if (data.events && data.events.length > 0) {
    data.events.forEach(addEventLog);
  }
}


// =============================================
// Setup Event Listeners
// =============================================

function setupEventListeners() {
  // Hamburger menu (mobile)
  const hamburgerBtn = document.getElementById('btn-hamburger');
  const sidebar = document.getElementById('sidebar');
  const sidebarOverlay = document.getElementById('sidebar-overlay');

  hamburgerBtn.addEventListener('click', () => {
    sidebar.classList.toggle('open');
    sidebarOverlay.classList.toggle('open');
  });

  sidebarOverlay.addEventListener('click', () => {
    sidebar.classList.remove('open');
    sidebarOverlay.classList.remove('open');
  });

  // Backend connectivity error banner retry
  const retryBackendBtn = document.getElementById('btn-retry-backend');
  if (retryBackendBtn) {
    retryBackendBtn.addEventListener('click', () => {
      loadCameras();
    });
  }

  // Add Camera Modal
  const modal = document.getElementById('modal-add-camera');
  const closeBtn = document.getElementById('btn-close-camera-modal');
  const cancelBtn = document.getElementById('btn-cancel-camera-modal');
  const camTypeSelect = document.getElementById('input-cam-type');
  const camForm = document.getElementById('form-add-camera');

  const openAddCameraModal = () => {
    modal.classList.add('open');
    sidebar.classList.remove('open');
    sidebarOverlay.classList.remove('open');
  };

  document.querySelectorAll('.btn-trigger-add-cam').forEach(btn => {
    btn.addEventListener('click', openAddCameraModal);
  });

  closeBtn.addEventListener('click', () => modal.classList.remove('open'));
  cancelBtn.addEventListener('click', () => modal.classList.remove('open'));

  // Camera facing selector buttons
  document.querySelectorAll('.facing-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.facing-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });

  // Toggle form fields based on camera source type
  camTypeSelect.addEventListener('change', (e) => {
    const val = e.target.value;
    const urlGroup = document.getElementById('group-source-url');
    const fileGroup = document.getElementById('group-upload-file');
    const facingGroup = document.getElementById('group-camera-facing');
    const urlLabel = document.getElementById('label-source-url');
    const urlInput = document.getElementById('input-cam-url');

    // Reset visibility
    urlGroup.style.display = 'block';
    fileGroup.style.display = 'none';
    facingGroup.style.display = 'none';

    if (val === 'webcam') {
      urlLabel.textContent = 'Device Index (0, 1, 2...)';
      urlInput.value = '0';
    } else if (val === 'browser') {
      urlGroup.style.display = 'none';
      facingGroup.style.display = 'block';
    } else if (val === 'rtsp') {
      urlLabel.textContent = 'RTSP URL (rtsp://admin:pass@ip:554/stream1)';
      urlInput.value = 'rtsp://192.168.1.100:554/live';
    } else if (val === 'http') {
      urlLabel.textContent = 'HTTP / MJPEG Stream URL';
      urlInput.value = 'http://192.168.1.100:8080/video';
    } else if (val === 'file') {
      urlGroup.style.display = 'none';
      fileGroup.style.display = 'block';
    } else if (val === 'synthetic') {
      urlGroup.style.display = 'none';
    }
  });

  // Handle Add Camera Form Submit
  camForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('input-cam-name').value;
    const type = document.getElementById('input-cam-type').value;
    const alertThresh = parseInt(document.getElementById('input-cam-alert-thresh').value) || 20;

    if (type === 'file') {
      const fileInput = document.getElementById('input-cam-file');
      if (fileInput.files.length > 0) {
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('name', name);
        let newCam;
        try {
          const uploadUrl = new URL('/api/cameras/upload-video', window.location.href).toString();
          newCam = await apiFetchJson(uploadUrl, { method: 'POST', body: formData });
          hideBackendError();
        } catch (err) {
          console.error('Failed to upload video camera:', err);
          showBackendError(err.message);
          alert('Error uploading video: ' + err);
          return;
        }

        try {
          modal.classList.remove('open');
          await loadCameras();
          selectCamera(newCam.id);
        } catch (uiErr) {
          console.error('Camera was created, but refreshing the UI failed:', uiErr);
          modal.classList.remove('open');
        }
      }
    } else {
      let url = '';
      if (type === 'synthetic') {
        url = 'demo';
      } else if (type === 'browser') {
        const activeFacing = document.querySelector('.facing-btn.active');
        url = activeFacing ? activeFacing.dataset.facing : 'environment';
      } else {
        url = document.getElementById('input-cam-url').value;
      }

      const enableFaceAnalysis = document.getElementById('input-cam-face-analysis').checked;
      const payload = {
        id: 'cam_' + Date.now().toString(36),
        name: name,
        source_type: type,
        source_url: url,
        enabled: true,
        alert_max_occupancy: alertThresh,
        enable_face_analysis: enableFaceAnalysis,
        lines: [],
        zones: []
      };

      let saved;
      try {
        // Build an absolute URL explicitly rather than relying on the browser's
        // relative-URL resolution against window.location -- some in-app
        // webviews (e.g. WhatsApp's) load pages through a wrapped/rewritten
        // location that can make plain relative fetch() calls fail.
        const apiUrl = new URL('/api/cameras', window.location.href).toString();
        saved = await apiFetchJson(apiUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        hideBackendError();
      } catch (err) {
        // Nothing was created (or we can't tell) -- this is a real failure, show it.
        showBackendError(err.message);
        console.error('Failed to create camera:', err);
        alert('Error adding camera: ' + err);
        return;
      }

      // Camera was successfully created on the server at this point. Any error
      // from here on is just a UI-refresh glitch, not a creation failure -- log
      // it instead of showing a misleading "Error adding camera" alert.
      try {
        modal.classList.remove('open');
        await loadCameras();
        selectCamera(saved.id);
      } catch (uiErr) {
        console.error('Camera was created, but refreshing the UI failed:', uiErr);
        modal.classList.remove('open');
      }
    }
  });

  // Delete Camera
  document.getElementById('btn-delete-camera').addEventListener('click', async () => {
    if (!currentCameraId) return;
    if (confirm('Are you sure you want to remove this camera?')) {
      // Stop browser cam if active
      if (browserCamStream && browserCamStream.cameraId === currentCameraId) {
        browserCamStream.stop();
        browserCamStream = null;
      }
      await fetch(`/api/cameras/${currentCameraId}`, { method: 'DELETE' });
      await loadCameras();
    }
  });

  // Snapshot
  document.getElementById('btn-take-snapshot').addEventListener('click', () => {
    if (!currentCameraId) return;
    window.open(`/api/snapshot/${currentCameraId}`, '_blank');
  });

  // Export CSV
  document.getElementById('btn-export-csv').addEventListener('click', () => {
    if (!currentCameraId) return;
    window.location.href = `/api/stats/${currentCameraId}/export-csv`;
  });

  // Interactive Drawing
  document.getElementById('btn-draw-line').addEventListener('click', () => {
    editor.startDrawLine(async (shapeType, shapeData) => {
      const cam = allCameras.find(c => c.id === currentCameraId);
      if (cam) {
        cam.lines.push(shapeData);
        await updateCameraConfig(cam);
      }
    });
  });

  document.getElementById('btn-draw-zone').addEventListener('click', () => {
    editor.startDrawZone(async (shapeType, shapeData) => {
      const cam = allCameras.find(c => c.id === currentCameraId);
      if (cam) {
        cam.zones.push(shapeData);
        await updateCameraConfig(cam);
      }
    });
  });

  document.getElementById('btn-clear-drawings').addEventListener('click', () => {
    editor.cancelDrawing();
  });

  // Overlay Toggles
  ['chk-show-boxes', 'chk-show-labels', 'chk-show-trails', 'chk-show-lines', 'chk-show-zones', 'chk-show-face-attrs'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('change', async () => {
        const cam = allCameras.find(c => c.id === currentCameraId);
        if (cam) {
          cam.show_boxes = document.getElementById('chk-show-boxes').checked;
          cam.show_labels = document.getElementById('chk-show-labels').checked;
          cam.show_trails = document.getElementById('chk-show-trails').checked;
          cam.show_lines = document.getElementById('chk-show-lines').checked;
          cam.show_zones = document.getElementById('chk-show-zones').checked;
          cam.show_face_attributes = document.getElementById('chk-show-face-attrs').checked;
          await updateCameraConfig(cam);
        }
      });
    }
  });
}

// Fetch all cameras
async function loadCameras() {
  try {
    allCameras = await apiFetchJson('/api/cameras') || [];
    hideBackendError();
    renderCameraList();

    if (allCameras.length > 0) {
      if (!currentCameraId || !allCameras.some(c => c.id === currentCameraId)) {
        selectCamera(allCameras[0].id);
      }
    }
  } catch (err) {
    console.error('Failed to load cameras:', err);
    showBackendError(err.message);
  }
}

// Render camera cards in sidebar
function renderCameraList() {
  const container = document.getElementById('camera-list-container');
  const countBadge = document.getElementById('camera-count-badge');
  container.innerHTML = '';
  countBadge.textContent = `${allCameras.length} Feed${allCameras.length === 1 ? '' : 's'}`;

  allCameras.forEach(cam => {
    const card = document.createElement('div');
    card.className = `camera-card ${cam.id === currentCameraId ? 'active' : ''}`;
    card.onclick = () => {
      selectCamera(cam.id);
      // Close sidebar on mobile after selection
      document.getElementById('sidebar').classList.remove('open');
      document.getElementById('sidebar-overlay').classList.remove('open');
    };

    const typeLabel = cam.source_type === 'browser' ? '📷 DEVICE CAM' : cam.source_type.toUpperCase();

    card.innerHTML = `
      <div class="cam-header">
        <span class="cam-title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M23 7l-7 5 7 5V7z"></path>
            <rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect>
          </svg>
          ${escapeHtml(cam.name)}
        </span>
        <span class="cam-pill pill-live">LIVE</span>
      </div>
      <div class="cam-meta">
        <span>${typeLabel}</span>
        <span class="cam-occ-badge" id="sidebar-occ-${cam.id}">0 Inside</span>
      </div>
    `;
    container.appendChild(card);
  });
}

// Switch active camera
function selectCamera(camId) {
  // Stop previous browser camera if switching away
  if (browserCamStream && browserCamStream.cameraId !== camId) {
    browserCamStream.stop();
    browserCamStream = null;
  }

  currentCameraId = camId;
  const cam = allCameras.find(c => c.id === camId);
  if (!cam) return;

  // Update UI headers
  document.getElementById('current-camera-title').textContent = cam.name;
  document.getElementById('label-alert-cap').textContent = cam.alert_max_occupancy || 20;

  // Update Checkboxes
  document.getElementById('chk-show-boxes').checked = cam.show_boxes;
  document.getElementById('chk-show-labels').checked = cam.show_labels;
  document.getElementById('chk-show-trails').checked = cam.show_trails;
  document.getElementById('chk-show-lines').checked = cam.show_lines;
  document.getElementById('chk-show-zones').checked = cam.show_zones;
  document.getElementById('chk-show-face-attrs').checked = cam.show_face_attributes;

  const demoCard = document.getElementById('demographics-card');
  if (demoCard) demoCard.style.display = cam.enable_face_analysis ? '' : 'none';

  if (cam.source_type === 'browser') {
    // Start browser camera stream (source_url holds either a facingMode
    // string or a 'device:<id>' pick from detectSystemCameras())
    const sourceSpec = cam.source_url || 'environment';
    browserCamStream = new BrowserCameraStream(camId, sourceSpec);
    browserCamStream.start().catch(err => {
      console.error('Failed to start browser camera:', err);
    });
    // Set a placeholder while the camera starts
    const img = document.getElementById('main-stream-img');
    img.src = '';
    img.alt = 'Starting phone camera...';
  } else {
    // Standard MJPEG stream from backend
    const img = document.getElementById('main-stream-img');
    img.src = `/api/stream/${camId}?t=${Date.now()}`;
    img.alt = 'Camera Stream';
  }

  // Update active card styling
  document.querySelectorAll('.camera-card').forEach(card => card.classList.remove('active'));
  renderCameraList();
}

// Update camera config via API
async function updateCameraConfig(camConfig) {
  try {
    await fetch(`/api/cameras/${camConfig.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(camConfig)
    });
  } catch (err) {
    console.error('Failed to update camera config:', err);
  }
}

// Connect to WebSocket for real-time telemetry
let wsConsecutiveFailures = 0;

function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log('Telemetry WebSocket connected.');
    wsConsecutiveFailures = 0;
    hideBackendError();
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'telemetry') {
        handleTelemetry(msg.data);
      }
    } catch (err) {
      console.error('Error parsing WS message:', err);
    }
  };

  ws.onclose = () => {
    console.log('WS disconnected. Reconnecting in 2s...');
    wsConsecutiveFailures += 1;
    // Only surface the banner after a few consecutive drops so a single
    // transient disconnect doesn't flash an alarming message.
    if (wsConsecutiveFailures >= 3) {
      showBackendError("Can't reach the live telemetry connection (WebSocket). The server may be unreachable.");
    }
    setTimeout(connectWebSocket, 2000);
  };
}

// Process live telemetry
function handleTelemetry(allData) {
  for (const [camId, data] of Object.entries(allData)) {
    // Update sidebar badge
    const badge = document.getElementById(`sidebar-occ-${camId}`);
    if (badge) {
      badge.textContent = `${data.current_occupancy} Inside`;
    }

    // If current camera (and not a browser cam — those update via their own WS)
    if (camId === currentCameraId) {
      const cam = allCameras.find(c => c.id === camId);
      if (cam && cam.source_type !== 'browser') {
        updateDashboardFromAnalytics(data);
      }
    }
  }
}

function renderDemographicsCard(demographics) {
  const card = document.getElementById('demographics-card');
  if (!card) return;

  if (!demographics || Object.keys(demographics).length === 0) {
    card.style.display = 'none';
    return;
  }
  card.style.display = '';

  document.getElementById('demo-analyzed-count').textContent = demographics.analyzed_count || 0;
  document.getElementById('demo-avg-age').textContent = demographics.avg_age != null ? demographics.avg_age : '-';

  const genderEl = document.getElementById('demo-gender-breakdown');
  const emotionEl = document.getElementById('demo-emotion-breakdown');

  const renderChips = (container, breakdown) => {
    container.innerHTML = '';
    const entries = Object.entries(breakdown || {});
    if (entries.length === 0) {
      container.innerHTML = '<span style="color: var(--text-muted); font-size: 0.75rem;">No data yet</span>';
      return;
    }
    for (const [label, count] of entries) {
      const chip = document.createElement('span');
      chip.className = 'demo-chip';
      chip.textContent = `${escapeHtml(label)}: ${count}`;
      container.appendChild(chip);
    }
  };

  renderChips(genderEl, demographics.gender_breakdown);
  renderChips(emotionEl, demographics.emotion_breakdown);
}

function renderZonesTable(zones) {
  const tbody = document.getElementById('zones-tbody');
  tbody.innerHTML = '';
  if (!zones || Object.keys(zones).length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color: var(--text-muted);">No zones defined</td></tr>';
    return;
  }

  for (const [zid, z] of Object.entries(zones)) {
    const tr = document.createElement('tr');
    const isWarning = z.current_count >= z.max_capacity;
    tr.innerHTML = `
      <td><strong>${escapeHtml(z.name)}</strong></td>
      <td><span style="color: ${isWarning ? 'var(--accent-rose)' : 'var(--accent-cyan)'}; font-weight:700;">${z.current_count}</span></td>
      <td>
        ${z.max_capacity}
        <div class="progress-bar-container">
          <div class="progress-bar-fill ${isWarning ? 'warning' : ''}" style="width: ${Math.min(100, z.occupancy_rate)}%;"></div>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  }
}

function renderLinesTable(lines) {
  const tbody = document.getElementById('lines-tbody');
  tbody.innerHTML = '';
  if (!lines || Object.keys(lines).length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color: var(--text-muted);">No tripwires defined</td></tr>';
    return;
  }

  for (const [lid, l] of Object.entries(lines)) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>${escapeHtml(l.name)}</strong></td>
      <td style="color: var(--accent-green); font-weight:700;">+${l.in_count}</td>
      <td style="color: var(--accent-amber); font-weight:700;">-${l.out_count}</td>
    `;
    tbody.appendChild(tr);
  }
}

function addEventLog(ev) {
  const container = document.getElementById('events-log-container');
  const div = document.createElement('div');
  div.style.padding = '6px 8px';
  div.style.borderRadius = '6px';
  div.style.background = 'rgba(255, 255, 255, 0.04)';
  div.style.borderLeft = `3px solid ${ev.direction === 'IN' ? 'var(--accent-green)' : 'var(--accent-amber)'}`;
  
  const timeStr = new Date(ev.timestamp * 1000).toLocaleTimeString();
  div.innerHTML = `
    <span style="color: var(--text-muted);">${timeStr}</span>
    <strong style="color: ${ev.direction === 'IN' ? 'var(--accent-green)' : 'var(--accent-amber)'}; margin: 0 4px;">[${ev.direction}]</strong>
    <span>Person #${ev.track_id} crossed ${escapeHtml(ev.line_name)}</span>
  `;

  container.prepend(div);
  if (container.children.length > 20) {
    container.removeChild(container.lastChild);
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
