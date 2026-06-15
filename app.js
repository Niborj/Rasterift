/**
 * Rasterift ENGINE - Pure & Performant Logic
 * =========================================
 * No decorative animations. Pure WebSocket streaming
 * and high-performance canvas rendering.
 * Includes an "Invisible Selection Layer" for text selection.
 */

const player    = document.getElementById('ascii-player');
const canvas    = document.getElementById('ascii-canvas');
const ctx       = canvas.getContext('2d');
const statusEl  = document.getElementById('status');
const container = document.getElementById('player-container');
const overlay   = document.getElementById('play-overlay');
const audioEl   = document.getElementById('ascii-audio');
const sourceVideo = document.getElementById('source-video');
const volumeSlider = document.getElementById('volume-slider');
const videoUpload = document.getElementById('video-upload');
const uploadButton = document.getElementById('upload-button');
const uploadStatus = document.getElementById('upload-status');
const uploadMenu = document.getElementById('upload-menu');
const modeButtons = Array.from(document.querySelectorAll('.mode-button'));
const videoList = document.getElementById('video-list');
const libraryCount = document.getElementById('library-count');
const activeSummary = document.getElementById('active-summary');
const technicalTrigger = document.getElementById('technical-trigger');
const riskCard = document.getElementById('risk-card');
const technicalModal = document.getElementById('technical-modal');
const riskModal = document.getElementById('risk-modal');
const throughputCards = Array.from(document.querySelectorAll('[data-throughput-card]'));
const throughputEls = {
    originalValue: document.getElementById('throughput-original'),
    originalMeta: document.getElementById('throughput-original-meta'),
    asciiValue: document.getElementById('throughput-ascii'),
    asciiMeta: document.getElementById('throughput-ascii-meta'),
    pixelValue: document.getElementById('throughput-pixel'),
    pixelMeta: document.getElementById('throughput-pixel-meta'),
};
const metricExplainerEls = {
    videoName: document.getElementById('metric-video-name'),
    rawRate: document.getElementById('metric-raw-rate'),
    glyphRate: document.getElementById('metric-glyph-rate'),
    pixelRate: document.getElementById('metric-pixel-rate'),
    originalRate: document.getElementById('metric-original-rate'),
    relationship: document.getElementById('metric-relationship'),
};

// ── STATE ──
let state = 'IDLE'; // IDLE | PLAYING
let ws = null;
const frameBuffer = [];
const BUFFER_SIZE = 4;
let codecDecoder = null; // Adaptive codec decoder (codec.js)
let targetFps = 24;
let frameInterval = 1000 / targetFps;
let renderMode = 1;
let pixelMode = false;
let readyToRender = false;
let activeVideoId = null;
let activeMode = 'ascii';
const DEMO_DEFAULT_MODE = 'ascii';
let videoLibrary = [];
let preparedSelection = null;
let throughputVideoId = null;
let originalSourceRate = 0;
let rawRgbRate = 0;
let throughputStats = {
    ascii: createThroughputStat(),
    pixel: createThroughputStat(),
};

// Grid & Dimensions
let gridCols = 0, gridRows = 0;
let charWidth = 0, charHeight = 0;
let xPos = null, yPos = null;

// Pixel Mode (--pixel) — ImageData pixel buffer
let dotImageData = null;

// Selection Layer optimization
const textDecoder = new TextDecoder();
const throughputTextEncoder = new TextEncoder();
let selectionBuffer = null;

// Timing & Metrics
let lastRenderTime = 0;
let frameCount = 0, currentFps = 0, lastFpsUpdate = 0;
let streamStartTime = 0;

const CHAR_LUT = new Array(128);
for (let i = 0; i < 128; i++) CHAR_LUT[i] = String.fromCharCode(i);

// ═══════════════════════════════════════
//  VIDEO LIBRARY + MODE CONTROL
// ═══════════════════════════════════════

function formatDuration(seconds) {
    if (!seconds || seconds < 1) return '';
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60).toString().padStart(2, '0');
    return `${mins}:${secs}`;
}

function createThroughputStat() {
    return {
        totalBytes: 0,
        intervalBytes: 0,
        frames: 0,
        startTime: 0,
        intervalStart: 0,
        currentRate: 0,
        averageRate: 0,
        sourceRate: 0,
        meanFrameBytes: 0,
        lastRender: 0,
    };
}

function formatByteCount(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return '--';

    const units = ['B', 'kB', 'MB', 'GB'];
    let value = bytes;
    let unitIndex = 0;
    while (value >= 1000 && unitIndex < units.length - 1) {
        value /= 1000;
        unitIndex++;
    }

    const precision = value >= 100 || unitIndex === 0 ? 0 : value >= 10 ? 2 : 2;
    return `${value.toFixed(precision)} ${units[unitIndex]}`;
}

function formatByteRate(bytesPerSecond) {
    if (!Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) return '--';

    const units = ['B/s', 'kB/s', 'MB/s', 'GB/s'];
    let value = bytesPerSecond;
    let unitIndex = 0;
    while (value >= 1000 && unitIndex < units.length - 1) {
        value /= 1000;
        unitIndex++;
    }

    const precision = value >= 100 || unitIndex === 0 ? 1 : value >= 10 ? 2 : 2;
    return `${value.toFixed(precision)} ${units[unitIndex]}`;
}

function formatImpact(rate, baseline) {
    if (!Number.isFinite(rate) || rate <= 0 || !Number.isFinite(baseline) || baseline <= 0) {
        return 'No sample yet';
    }

    const ratio = rate / baseline;
    if (ratio >= 1) {
        return `${ratio.toFixed(ratio >= 10 ? 1 : 2)}x Original`;
    }
    const percent = ratio * 100;
    return `${percent.toFixed(percent < 10 ? 2 : 1)}% of Original`;
}

function displayRateForMode(mode) {
    const stat = throughputStats[mode];
    if (!stat) return 0;
    return stat.sourceRate || stat.averageRate;
}

function resetMeasuredThroughput() {
    throughputStats = {
        ascii: createThroughputStat(),
        pixel: createThroughputStat(),
    };
}

function updateThroughputBaseline() {
    const video = activeVideo();
    if (!video) {
        throughputVideoId = null;
        originalSourceRate = 0;
        rawRgbRate = 0;
        resetMeasuredThroughput();
        updateThroughputPanel();
        return;
    }

    if (throughputVideoId !== video.id) {
        throughputVideoId = video.id;
        resetMeasuredThroughput();
    }

    originalSourceRate = video.duration > 0 ? video.size / video.duration : 0;
    const fps = video.fps || targetFps || 0;
    rawRgbRate = video.width && video.height && fps ? video.width * video.height * 3 * fps : 0;
    updateThroughputPanel();
}

function throughputMetaForMode(mode) {
    const stat = throughputStats[mode];
    if (!stat || stat.totalBytes <= 0) {
        return activeMode === mode && state === 'PLAYING' ? 'Measuring...' : 'No sample yet';
    }

    const rawRatio = rawRgbRate > 0 ? stat.sourceRate / rawRgbRate : 0;
    const rawImpact = rawRatio > 0 && rawRatio < 1
        ? `${(100 * (1 - rawRatio)).toFixed(1)}% under Raw RGB`
        : rawRatio >= 1
            ? `${rawRatio.toFixed(rawRatio >= 10 ? 1 : 2)}x Raw RGB`
            : 'Raw RGB unavailable';

    return `${formatByteCount(stat.meanFrameBytes)}/frame / ${formatImpact(stat.sourceRate, originalSourceRate)} / ${rawImpact}`;
}

function measuredExplainerRate(mode, modeLabel) {
    const stat = throughputStats[mode];
    if (!stat || stat.totalBytes <= 0 || stat.sourceRate <= 0) {
        return activeMode === mode && state === 'PLAYING'
            ? `measuring ${modeLabel}...`
            : `not measured yet (play ${modeLabel})`;
    }
    return `about ${formatByteRate(stat.sourceRate)}`;
}

function updateMetricExplainer(video) {
    const hasVideo = !!video;
    const glyphRate = displayRateForMode('ascii');
    const pixelRate = displayRateForMode('pixel');

    if (metricExplainerEls.videoName) {
        metricExplainerEls.videoName.textContent = hasVideo ? video.name : 'the selected clip';
    }
    if (metricExplainerEls.rawRate) {
        metricExplainerEls.rawRate.textContent = hasVideo ? `about ${formatByteRate(rawRgbRate)}` : '--';
    }
    if (metricExplainerEls.originalRate) {
        metricExplainerEls.originalRate.textContent = hasVideo ? `about ${formatByteRate(originalSourceRate)}` : '--';
    }
    if (metricExplainerEls.glyphRate) {
        metricExplainerEls.glyphRate.textContent = measuredExplainerRate('ascii', 'Glyph');
    }
    if (metricExplainerEls.pixelRate) {
        metricExplainerEls.pixelRate.textContent = measuredExplainerRate('pixel', 'Pixel');
    }
    if (!metricExplainerEls.relationship) return;

    if (!hasVideo) {
        metricExplainerEls.relationship.textContent = 'Choose a video to calculate the comparison.';
        return;
    }

    if (pixelRate > 0 && rawRgbRate > 0 && originalSourceRate > 0) {
        const rawReduction = 100 * (1 - pixelRate / rawRgbRate);
        const originalRatio = pixelRate / originalSourceRate;
        const originalText = originalRatio >= 1
            ? `${originalRatio.toFixed(originalRatio >= 10 ? 1 : 2)}x larger than`
            : `${(originalRatio * 100).toFixed(originalRatio < 0.1 ? 2 : 1)}% of`;
        metricExplainerEls.relationship.textContent = `Pixel is ${rawReduction.toFixed(1)}% smaller than raw RGB, but ${originalText} the compressed Original for this file.`;
        return;
    }

    if (glyphRate > 0 && rawRgbRate > 0 && originalSourceRate > 0) {
        const rawReduction = 100 * (1 - glyphRate / rawRgbRate);
        metricExplainerEls.relationship.textContent = `Glyph is ${rawReduction.toFixed(1)}% smaller than raw RGB. Play Pixel to complete the comparison for this file.`;
        return;
    }

    metricExplainerEls.relationship.textContent = 'Play Glyph and Pixel to fill in transformed payload rates for this file.';
}

function updateThroughputPanel() {
    throughputCards.forEach((card) => {
        card.classList.toggle('active', card.dataset.throughputCard === activeMode);
    });

    const video = activeVideo();
    if (throughputEls.originalValue) {
        throughputEls.originalValue.textContent = formatByteRate(originalSourceRate);
    }
    if (throughputEls.originalMeta) {
        throughputEls.originalMeta.textContent = video
            ? `Source container / Raw RGB ${formatByteRate(rawRgbRate)}`
            : 'No video selected';
    }
    if (throughputEls.asciiValue) {
        throughputEls.asciiValue.textContent = formatByteRate(displayRateForMode('ascii'));
    }
    if (throughputEls.asciiMeta) {
        throughputEls.asciiMeta.textContent = throughputMetaForMode('ascii');
    }
    if (throughputEls.pixelValue) {
        throughputEls.pixelValue.textContent = formatByteRate(displayRateForMode('pixel'));
    }
    if (throughputEls.pixelMeta) {
        throughputEls.pixelMeta.textContent = throughputMetaForMode('pixel');
    }
    updateMetricExplainer(video);
}

function beginThroughputSession(mode) {
    if (!throughputStats[mode]) return;
    const now = performance.now();
    throughputStats[mode] = {
        ...createThroughputStat(),
        startTime: now,
        intervalStart: now,
    };
    updateThroughputPanel();
}

function recordStreamBytes(mode, bytes) {
    const stat = throughputStats[mode];
    if (!stat || !bytes) return;

    const now = performance.now();
    if (!stat.startTime) {
        stat.startTime = now;
        stat.intervalStart = now;
    }

    stat.totalBytes += bytes;
    stat.intervalBytes += bytes;
    stat.frames++;

    const totalSeconds = Math.max((now - stat.startTime) / 1000, 0.001);
    stat.averageRate = stat.totalBytes / totalSeconds;
    stat.meanFrameBytes = stat.totalBytes / stat.frames;
    stat.sourceRate = stat.meanFrameBytes * Math.max(targetFps, 1);

    const intervalSeconds = (now - stat.intervalStart) / 1000;
    if (intervalSeconds >= 1) {
        stat.currentRate = stat.intervalBytes / intervalSeconds;
        stat.intervalBytes = 0;
        stat.intervalStart = now;
    }

    if (now - stat.lastRender >= 250) {
        stat.lastRender = now;
        updateThroughputPanel();
    }
}

function currentPlayLabel() {
    if (!activeVideo()) return 'Upload a video to start';
    if (activeMode === 'original') return 'Play Rasterift Original';
    if (activeMode === 'pixel') return 'Play Rasterift Pixel';
    return 'Play Rasterift Glyph';
}

function setOverlayLabel(label = currentPlayLabel()) {
    const playLabel = overlay.querySelector('.play-label');
    if (playLabel) playLabel.textContent = label;
}

function setModeButtons() {
    modeButtons.forEach((button) => {
        button.classList.toggle('active', button.dataset.mode === activeMode);
    });
    updateThroughputPanel();
}

function activeVideo() {
    return videoLibrary.find((video) => video.id === activeVideoId);
}

function updateActiveSummary() {
    const video = activeVideo();
    const modeLabel = activeMode === 'original' ? 'Rasterift Original' : activeMode === 'pixel' ? 'Rasterift Pixel' : 'Rasterift Glyph';
    if (activeSummary) {
        activeSummary.textContent = video ? `${modeLabel} / ${video.name}` : 'No video selected';
    }
    updateThroughputBaseline();
}

function isPreparedSelectionCurrent(selection = preparedSelection) {
    return Boolean(
        selection &&
        selection.video &&
        selection.video.id === activeVideoId &&
        selection.mode === activeMode
    );
}

function renderLibrary() {
    if (!videoList) return;
    videoList.textContent = '';
    if (libraryCount) libraryCount.textContent = String(videoLibrary.length);

    if (videoLibrary.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'video-empty';
        const title = document.createElement('strong');
        title.textContent = 'Upload a video to begin';
        const copy = document.createElement('span');
        copy.textContent = 'MP4, WebM, MOV, MKV, and AVI files will appear here.';
        const upload = document.createElement('label');
        upload.className = 'empty-upload-choice';
        upload.htmlFor = 'video-upload';
        upload.textContent = 'Choose video files';
        empty.append(title, copy, upload);
        videoList.appendChild(empty);
        updateActiveSummary();
        return;
    }

    videoLibrary.forEach((video) => {
        const row = document.createElement('div');
        row.className = 'video-item';
        row.dataset.videoId = video.id;
        row.classList.toggle('active', video.id === activeVideoId);

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'video-select';

        const name = document.createElement('span');
        name.className = 'video-name';
        name.textContent = video.name;

        const meta = document.createElement('span');
        meta.className = 'video-meta';
        const duration = formatDuration(video.duration);
        const fps = video.fps ? `${Math.round(video.fps)} FPS` : '';
        meta.textContent = [
            video.kind === 'upload' ? 'Uploaded' : 'Source',
            `${video.width}x${video.height}`,
            duration,
            fps,
            formatFileSize(video.size),
        ].filter(Boolean).join(' / ');

        button.append(name, meta);
        button.addEventListener('click', () => chooseVideo(video.id));

        row.appendChild(button);
        if (video.kind === 'upload') {
            const deleteButton = document.createElement('button');
            deleteButton.type = 'button';
            deleteButton.className = 'delete-video';
            deleteButton.textContent = 'Delete';
            deleteButton.addEventListener('click', () => deleteVideo(video.id, video.name));
            row.appendChild(deleteButton);
        } else {
            const sourceBadge = document.createElement('span');
            sourceBadge.className = 'source-badge';
            sourceBadge.textContent = 'Rasterift Original';
            row.appendChild(sourceBadge);
        }
        videoList.appendChild(row);
    });

    updateActiveSummary();
}

async function loadLibrary(preferredId = null) {
    const response = await fetch('/videos');
    if (!response.ok) throw new Error('Could not load video list.');
    const data = await response.json();
    videoLibrary = data.videos || [];
    activeMode = data.mode || activeMode;
    activeVideoId = preferredId || activeVideoId || data.active_id || (videoLibrary[0] && videoLibrary[0].id);

    if (activeVideoId && !videoLibrary.some((video) => video.id === activeVideoId)) {
        activeVideoId = videoLibrary[0] && videoLibrary[0].id;
    }
    if (videoLibrary.length === 0) {
        activeVideoId = null;
        preparedSelection = null;
    }

    setModeButtons();
    renderLibrary();
}

async function selectOnServer() {
    if (!activeVideoId) throw new Error('Upload a video to start Rasterift.');

    const response = await fetch('/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_id: activeVideoId, mode: activeMode }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || 'Could not select that video.');

    activeVideoId = result.video.id;
    activeMode = result.mode;
    preparedSelection = result;
    setModeButtons();
    renderLibrary();
    updateActiveSummary();
    return result;
}

function hideOriginalSource(clearSource = false) {
    if (!sourceVideo) return;
    sourceVideo.pause();
    if (clearSource) {
        sourceVideo.removeAttribute('src');
        delete sourceVideo.dataset.videoId;
    }
    sourceVideo.style.display = 'none';
}

function cacheBustedSourceUrl(url) {
    return `${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`;
}

function prepareOriginalSource(selection, force = false) {
    if (!sourceVideo || !selection || !selection.video) return;

    const needsSource = force || sourceVideo.dataset.videoId !== selection.video.id || !sourceVideo.src;
    if (needsSource) {
        sourceVideo.src = cacheBustedSourceUrl(selection.source_url);
        sourceVideo.dataset.videoId = selection.video.id;
        sourceVideo.load();
    }

    sourceVideo.loop = true;
    sourceVideo.controls = true;
    sourceVideo.volume = volumeSlider ? volumeSlider.value : 1.0;
    sourceVideo.style.display = 'block';
}

function prepareSelectedMode(selection) {
    frameBuffer.length = 0;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    player.textContent = '';
    player.style.display = 'none';
    canvas.style.display = 'none';
    setOverlayLabel();
    overlay.classList.remove('hidden');

    if (activeMode === 'original') {
        prepareOriginalSource(selection, true);
        statusEl.textContent = 'Rasterift Original Ready';
    } else {
        hideOriginalSource();
        statusEl.textContent = activeMode === 'pixel' ? 'Rasterift Pixel Ready' : 'Rasterift Glyph Ready';
    }
    updateActiveSummary();
    statusEl.style.color = 'var(--accent-secondary)';
}

function showEmptyLibraryState(message = 'Upload a video to begin') {
    state = 'IDLE';
    preparedSelection = null;
    frameBuffer.length = 0;
    if (ws) { ws.onclose = null; ws.close(); ws = null; }
    if (audioEl) { audioEl.pause(); audioEl.src = ''; }
    hideOriginalSource(true);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    player.textContent = '';
    player.style.display = 'none';
    canvas.style.display = 'none';
    overlay.classList.remove('hidden');
    setOverlayLabel('Upload a video to start');
    statusEl.textContent = message;
    statusEl.style.color = 'var(--accent-color)';
    setUploadMessage('Choose a video file to start Rasterift.', 'idle');
    updateActiveSummary();
}

async function applySelection() {
    const selection = await selectOnServer();
    prepareSelectedMode(selection);
    return selection;
}

async function chooseVideo(videoId) {
    if (videoId === activeVideoId && state === 'IDLE') return;
    finishStream();
    activeVideoId = videoId;
    try {
        const selection = await applySelection();
        setUploadMessage(`${selection.video.name} selected`, 'ready');
    } catch (error) {
        setUploadMessage(error.message, 'error');
        statusEl.textContent = 'Selection Error';
        statusEl.style.color = '#ff4d4d';
    }
}

async function chooseMode(mode) {
    if (!['original', 'ascii', 'pixel'].includes(mode)) return;
    if (mode === activeMode && state === 'IDLE') return;
    finishStream();
    activeMode = mode;
    setModeButtons();
    if (!activeVideoId) {
        showEmptyLibraryState();
        return;
    }
    try {
        await applySelection();
    } catch (error) {
        setUploadMessage(error.message, 'error');
        statusEl.textContent = 'Mode Error';
        statusEl.style.color = '#ff4d4d';
    }
}

async function deleteVideo(videoId, name) {
    if (!videoId || !videoId.startsWith('upload:')) return;
    const ok = window.confirm(`Delete "${name}" from Rasterift?`);
    if (!ok) return;

    finishStream();
    setUploadMessage(`Deleting ${name}...`, 'busy');

    try {
        const response = await fetch('/videos', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ video_id: videoId }),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(result.detail || 'Could not delete that video.');
        }

        videoLibrary = result.videos || [];
        activeVideoId = result.active_id || (videoLibrary[0] && videoLibrary[0].id);
        activeMode = result.mode || activeMode;
        setModeButtons();
        renderLibrary();
        if (!activeVideoId) {
            showEmptyLibraryState(`${name} deleted`);
            setUploadMessage(`${name} deleted / upload a video to continue`, 'ready');
            return;
        }
        const selection = await applySelection();
        setUploadMessage(`${name} deleted`, 'ready');
        statusEl.textContent = selection.mode === 'pixel' ? 'Rasterift Pixel Ready' : selection.mode === 'original' ? 'Rasterift Original Ready' : 'Rasterift Glyph Ready';
    } catch (error) {
        setUploadMessage(error.message, 'error');
        statusEl.textContent = 'Delete Error';
        statusEl.style.color = '#ff4d4d';
    }
}

// ═══════════════════════════════════════
//  CANVAS SETUP
// ═══════════════════════════════════════

function buildCanvas(cols, rows) {
    gridCols = cols;
    gridRows = rows;

    // Sizing and positioning for both layers
    const syncSize = (el) => {
        el.style.width  = container.clientWidth + 'px';
        el.style.height = container.clientHeight + 'px';
        el.style.objectFit = 'contain';
        el.style.position = 'absolute';
        el.style.top = '0';
        el.style.left = '0';
    };

    if (pixelMode) {
        // ── DOT MODE: 1 canvas pixel = 1 grid cell ──
        canvas.width  = cols;
        canvas.height = rows;
        canvas.style.display = 'block';
        canvas.style.imageRendering = 'pixelated';
        dotImageData = ctx.createImageData(cols, rows);
        // Pre-fill alpha channel to 255 (fully opaque)
        const d = dotImageData.data;
        for (let i = 3; i < d.length; i += 4) d[i] = 255;
        syncSize(canvas);
        // Hide selection layer — no text to select in dot mode
        player.style.display = 'none';
    } else {
        // ── STANDARD ASCII MODES (1-5) ──
        canvas.style.imageRendering = '';
        dotImageData = null;
        ctx.font = 'bold 8px Courier New';
        charWidth = ctx.measureText('M').width;
        charHeight = 8;
        canvas.width  = cols * charWidth;
        canvas.height = rows * charHeight;
        canvas.style.display = 'block';

        // Selection Layer Buffer
        selectionBuffer = new Uint8Array((cols + 1) * rows);
        for (let r = 0; r < rows; r++) selectionBuffer[r * (cols + 1) + cols] = 10;

        syncSize(canvas);

        // Selection layer: match canvas object-fit:contain position exactly
        const containerW = container.clientWidth;
        const containerH = container.clientHeight;
        const fitScaleX = containerW / canvas.width;
        const fitScaleY = containerH / canvas.height;
        const fitScale  = Math.min(fitScaleX, fitScaleY);
        const renderedW = canvas.width  * fitScale;
        const renderedH = canvas.height * fitScale;
        const offsetX   = (containerW - renderedW) / 2;
        const offsetY   = (containerH - renderedH) / 2;

        player.style.width  = canvas.width + 'px';
        player.style.height = canvas.height + 'px';
        player.style.position = 'absolute';
        player.style.top = '0';
        player.style.left = '0';
        player.style.transformOrigin = 'top left';
        player.style.transform = `translate(${offsetX}px, ${offsetY}px) scale(${fitScale})`;
        player.style.fontSize = '8px';
        player.style.lineHeight = '8px';

        ctx.font = 'bold 8px Courier New';
        ctx.textBaseline = 'top';
        xPos = new Float32Array(cols);
        yPos = new Float32Array(rows);
        for (let c = 0; c < cols; c++) xPos[c] = c * charWidth;
        for (let r = 0; r < rows; r++) yPos[r] = r * charHeight;
    }
}

// ═══════════════════════════════════════
//  STREAM CONTROL
// ═══════════════════════════════════════

async function startStream() {
    if (state !== 'IDLE') return;
    if (!activeVideoId) {
        showEmptyLibraryState();
        if (videoUpload && !videoUpload.disabled) videoUpload.click();
        return;
    }
    statusEl.textContent = 'Connecting...';
    statusEl.style.color = 'var(--accent-color)';

    try {
        const selection = isPreparedSelectionCurrent() ? preparedSelection : await selectOnServer();
        if (activeMode === 'original') {
            startOriginalSource(selection);
        } else {
            hideOriginalSource();
            overlay.classList.add('hidden');
            connectWebSocket();
        }
    } catch (error) {
        statusEl.textContent = error.message;
        statusEl.style.color = '#ff4d4d';
        overlay.classList.remove('hidden');
    }
}

async function startOriginalSource(selection) {
    if (!sourceVideo) return;

    prepareOriginalSource(selection);
    player.style.display = 'none';
    canvas.style.display = 'none';
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    frameBuffer.length = 0;

    try {
        updateThroughputPanel();
        await sourceVideo.play();
        state = 'PLAYING';
        overlay.classList.add('hidden');
        statusEl.textContent = 'Rasterift Original Source';
        statusEl.style.color = 'var(--accent-color)';
        updateThroughputPanel();
    } catch (error) {
        state = 'IDLE';
        const mediaError = sourceVideo.error;
        const isPolicyBlock = error && error.name === 'NotAllowedError';
        overlay.classList.toggle('hidden', isPolicyBlock);
        statusEl.textContent = isPolicyBlock
            ? 'Press play on the video controls'
            : mediaError
                ? 'Original source could not load'
                : 'Original playback unavailable';
        statusEl.style.color = '#ff4d4d';
        updateThroughputPanel();
    }
}

function connectWebSocket() {
    frameBuffer.length = 0;
    frameCount = 0;
    currentFps = 0;
    beginThroughputSession(activeMode);
    hideOriginalSource();

    // Audio is loaded later in INIT handler (Audio Ready Gate).
    // Don't preload here — causes race conditions with vol=0 (204 response).

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws?codec=adaptive`);
    ws.binaryType = 'arraybuffer';

    ws.onmessage = (event) => {
        if (typeof event.data === 'string') {
            if (event.data.startsWith('Error:')) {
                statusEl.textContent = event.data;
                statusEl.style.color = '#ff0000';
                if (ws) ws.close();
                setTimeout(() => finishStream(), 3000);
                return;
            }
            if (event.data.startsWith('INIT:')) {
                const p = event.data.split(':');
                targetFps = parseFloat(p[1]);
                frameInterval = 1000 / targetFps;
                renderMode = parseInt(p[2]);
                pixelMode = (p.length > 5 && parseInt(p[5]) === 1);
                buildCanvas(parseInt(p[3]), parseInt(p[4]));

                // Initialize adaptive codec decoder (pixel=3 bytes, ASCII color=4 bytes)
                if (typeof RasteriftCodec !== 'undefined' && renderMode > 1) {
                    codecDecoder = RasteriftCodec.makeDecoder(pixelMode ? 3 : 4);
                } else {
                    codecDecoder = null;
                }

                // ── AUDIO READY GATE ──
                // Buffer video frames but don't render until audio is ready.
                // This prevents the 0.5s initial stutter.
                readyToRender = false;
                state = 'PLAYING';

                const beginRendering = () => {
                    readyToRender = true;
                    streamStartTime = performance.now();
                    lastRenderTime = performance.now();
                    lastFpsUpdate = lastRenderTime;
                    requestAnimationFrame(renderFrame);
                };

                if (audioEl) {
                    audioEl.pause();
                    audioEl.src = '/audio?' + Date.now();
                    audioEl.volume = volumeSlider ? volumeSlider.value : 1.0;
                    audioEl.load();
                    audioEl.play().catch(() => {});

                    // Wait for audio to actually start playing
                    if (audioEl.readyState >= 3) {
                        beginRendering();
                    } else {
                        audioEl.addEventListener('playing', beginRendering, { once: true });
                        // Fallback: if audio fails to load (vol=0 / 204), start after 500ms
                        setTimeout(() => {
                            if (!readyToRender) beginRendering();
                        }, 500);
                    }
                } else {
                    // No audio element at all → start immediately
                    beginRendering();
                }
                return;
            }
            
            // Mode 1: Text Frame with Timestamp
            const text = event.data;
            recordStreamBytes('ascii', throughputTextEncoder.encode(text).length);
            const newlineIdx = text.indexOf('\n');
            const frameIndex = parseInt(text.substring(0, newlineIdx));
            const frameTime = frameIndex / targetFps;
            const frameData = text.substring(newlineIdx + 1);
            frameBuffer.push({ data: frameData, time: frameTime });
        } else {
            // Binary Frames — decoded via adaptive codec (raw/zlib/delta)
            recordStreamBytes(activeMode === 'pixel' ? 'pixel' : 'ascii', event.data.byteLength);
            if (codecDecoder) {
                codecDecoder.decode(event.data).then(({ frameIndex, frame }) => {
                    const frameTime = frameIndex / targetFps;
                    frameBuffer.push({ data: frame, time: frameTime });
                });
            } else {
                // Fallback: legacy 4-byte header
                const buffer = event.data;
                const view = new DataView(buffer);
                const frameIndex = view.getUint32(0, false);
                const frameTime = frameIndex / targetFps;
                const frameData = new Uint8Array(buffer, 4);
                frameBuffer.push({ data: frameData, time: frameTime });
            }
        }

        while (frameBuffer.length > BUFFER_SIZE * 5) frameBuffer.shift();
    };

    ws.onopen = () => { statusEl.textContent = 'Buffering...'; };

    ws.onclose = () => {
        if (state === 'PLAYING') {
            statusEl.textContent = 'Stream Ended.';
            statusEl.style.color = '#888';
            if (audioEl) audioEl.pause();
            setTimeout(() => finishStream(), 800);
        }
    };

    ws.onerror = () => {
        statusEl.textContent = 'Connection Error!';
        statusEl.style.color = '#ff0000';
        setTimeout(() => finishStream(), 2000);
    };
}

// ═══════════════════════════════════════
//  RENDER LOOP
// ═══════════════════════════════════════

function renderFrame(now) {
    if (state !== 'PLAYING' || !readyToRender) return;
    requestAnimationFrame(renderFrame);

    // ── MASTER CLOCK LOGIC ──
    let masterClock;
    if (audioEl && audioEl.readyState >= 1 && !audioEl.paused) {
        masterClock = audioEl.currentTime;
    } else {
        masterClock = (now - streamStartTime) / 1000.0;
    }

    if (frameBuffer.length === 0) return;

    // A/V Sync: Drop frames that are too far behind the master clock (catch up)
    while (frameBuffer.length > 1 && frameBuffer[0].time < masterClock - 0.1) {
        frameBuffer.shift();
    }

    // A/V Sync: Wait if the frame is in the future
    if (frameBuffer[0].time > masterClock + 0.05) {
        return;
    }

    const frameObj = frameBuffer.shift();
    const frame = frameObj.data;

    frameCount++;
    if (now - lastFpsUpdate >= 1000) {
        currentFps = frameCount;
        frameCount = 0;
        lastFpsUpdate = now;
        const modes = { 2: '512 Color', 3: '32K Color', 4: '262K Color', 5: '16M Ultra' };
        const label = (modes[renderMode] || 'B&W') + (pixelMode ? ' PIXEL' : '');
        statusEl.textContent = `FPS: ${currentFps}/${Math.round(targetFps)} | Buf: ${frameBuffer.length} | ${label}`;
    }

    lastRenderTime = now;

    if (renderMode === 1) {
        player.style.display = 'block';
        player.style.color = '#fff';
        player.textContent = frame;
    } else if (pixelMode) {
        // ── ZERO-COPY PIXEL MODE ──
        // Server sends raw BGR (3 bytes/pixel). We swap B↔R here.
        const view = frame; // Already a Uint8Array
        const data = dotImageData.data;
        // view: [B,G,R, B,G,R, ...] → data: [R,G,B,A, R,G,B,A, ...]
        for (let src = 0, dst = 0; src < view.length; src += 3, dst += 4) {
            data[dst]     = view[src + 2]; // R (from BGR)
            data[dst + 1] = view[src + 1]; // G
            data[dst + 2] = view[src];     // B
            // Alpha already set to 255 in buildCanvas
        }
        ctx.putImageData(dotImageData, 0, 0);
    } else {
        // ── STANDARD COLOR MODES (2-5): fillText per character ──
        const view = frame; // Already a Uint8Array
        
        // 1. Draw Canvas (Background)
        ctx.fillStyle = '#050505';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.font = 'bold 8px Courier New';
        ctx.textBaseline = 'top';

        let col = 0, row = 0, prevPacked = -1;
        for (let idx = 0; idx < view.length; idx += 4) {
            const packed = (view[idx+1] << 16) | (view[idx+2] << 8) | view[idx+3];
            if (packed !== prevPacked) {
                ctx.fillStyle = `rgb(${view[idx+1]},${view[idx+2]},${view[idx+3]})`;
                prevPacked = packed;
            }
            ctx.fillText(CHAR_LUT[view[idx]], xPos[col], yPos[row]);
            
            // Fill Selection Buffer (char code is at view[idx])
            selectionBuffer[row * (gridCols + 1) + col] = view[idx];

            col++;
            if (col >= gridCols) { col = 0; row++; }
        }

        // 2. Update Selection Layer (Foreground)
        player.style.display = 'block';
        player.style.color = 'transparent';
        player.textContent = textDecoder.decode(selectionBuffer);
    }
}

// ═══════════════════════════════════════
//  CLEANUP
// ═══════════════════════════════════════

function finishStream() {
    state = 'IDLE';
    if (ws) { ws.onclose = null; ws.close(); ws = null; }
    if (audioEl) { audioEl.pause(); audioEl.src = ''; }
    hideOriginalSource();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    player.textContent = '';
    player.style.display = 'none';
    overlay.classList.remove('hidden');
    setOverlayLabel();
    statusEl.textContent = 'Ready';
    statusEl.style.color = 'rgba(255,255,255,0.6)';
    readyToRender = false;
    frameBuffer.length = 0;
    updateThroughputPanel();
}

function setUploadMessage(message, tone = 'idle') {
    if (!uploadStatus) return;
    uploadStatus.textContent = message;
    uploadStatus.dataset.tone = tone;
}

function formatFileSize(bytes) {
    if (bytes < 1000 * 1000) return `${Math.max(1, Math.round(bytes / 1000))} kB`;
    return `${(bytes / (1000 * 1000)).toFixed(1)} MB`;
}

async function uploadVideos(files) {
    const queue = Array.from(files || []);
    if (queue.length === 0) return;

    finishStream();
    statusEl.textContent = 'Uploading...';
    statusEl.style.color = 'var(--accent-color)';
    setUploadMessage(`Uploading ${queue.length} video${queue.length === 1 ? '' : 's'}...`, 'busy');
    if (uploadButton) uploadButton.classList.add('busy');
    if (videoUpload) videoUpload.disabled = true;

    try {
        let lastResult = null;
        for (const file of queue) {
            setUploadMessage(`Uploading ${file.name}...`, 'busy');
            const formData = new FormData();
            formData.append('file', file);
            const response = await fetch('/upload', { method: 'POST', body: formData });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(result.detail || `Upload failed for ${file.name}.`);
            }
            lastResult = result;
        }

        activeVideoId = lastResult && lastResult.video && lastResult.video.id;
        await loadLibrary(activeVideoId);
        const selection = await applySelection();
        const fps = selection.video.fps ? `${Math.round(selection.video.fps)} FPS` : 'video';
        setUploadMessage(`${queue.length} uploaded / ${selection.video.name} selected (${selection.video.width}x${selection.video.height}, ${fps}, ${formatFileSize(selection.video.size)})`, 'ready');
        if (uploadMenu) uploadMenu.open = false;
    } catch (error) {
        setUploadMessage(error.message, 'error');
        statusEl.textContent = 'Upload Error';
        statusEl.style.color = '#ff4d4d';
    } finally {
        if (uploadButton) uploadButton.classList.remove('busy');
        if (videoUpload) {
            videoUpload.disabled = false;
            videoUpload.value = '';
        }
    }
}

// ── EVENT LISTENERS ──
overlay.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!activeVideoId) {
        showEmptyLibraryState();
        if (videoUpload && !videoUpload.disabled) videoUpload.click();
        return;
    }
    startStream();
});

if (volumeSlider) {
    volumeSlider.addEventListener('input', () => {
        if (audioEl) audioEl.volume = volumeSlider.value;
        if (sourceVideo) sourceVideo.volume = volumeSlider.value;
    });
}

if (videoUpload) {
    videoUpload.addEventListener('change', () => {
        uploadVideos(videoUpload.files);
    });
}

modeButtons.forEach((button) => {
    button.addEventListener('click', () => chooseMode(button.dataset.mode));
});

if (sourceVideo) {
    sourceVideo.addEventListener('play', () => {
        if (activeMode !== 'original') return;
        state = 'PLAYING';
        overlay.classList.add('hidden');
        statusEl.textContent = 'Rasterift Original Source';
        statusEl.style.color = 'var(--accent-color)';
        updateThroughputPanel();
    });

    sourceVideo.addEventListener('error', () => {
        if (activeMode !== 'original' || sourceVideo.style.display === 'none') return;
        state = 'IDLE';
        overlay.classList.remove('hidden');
        statusEl.textContent = 'Original source could not load';
        statusEl.style.color = '#ff4d4d';
    });

    sourceVideo.addEventListener('ended', () => {
        if (!sourceVideo.loop) finishStream();
    });
}

function openDialog(dialog) {
    if (dialog) dialog.showModal();
}

if (technicalTrigger) {
    technicalTrigger.addEventListener('click', () => openDialog(technicalModal));
}

if (riskCard) {
    riskCard.addEventListener('click', () => openDialog(riskModal));
}

document.querySelectorAll('[data-close-dialog]').forEach((button) => {
    button.addEventListener('click', () => {
        const dialog = button.closest('dialog');
        if (dialog) dialog.close();
    });
});

[technicalModal, riskModal].forEach((dialog) => {
    if (!dialog) return;
    dialog.addEventListener('click', (event) => {
        if (event.target === dialog) dialog.close();
    });
});

window.addEventListener('resize', () => {
    const syncSize = (el) => {
        if (!el) return;
        el.style.width  = container.clientWidth + 'px';
        el.style.height = container.clientHeight + 'px';
    };
    syncSize(canvas);
    syncSize(player);
    syncSize(sourceVideo);
});

loadLibrary()
    .then(() => {
        activeMode = DEMO_DEFAULT_MODE;
        setModeButtons();
        if (!activeVideoId) {
            showEmptyLibraryState();
            return null;
        }
        return applySelection();
    })
    .catch((error) => {
        setUploadMessage(error.message, 'error');
        statusEl.textContent = 'Library Error';
        statusEl.style.color = '#ff4d4d';
    });
