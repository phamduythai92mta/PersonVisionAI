(() => {
  "use strict";

  const state = {
    activeTab: "scan",
    historyView: "videos",
    selectedVideoId: null,
    scanPollTimer: null,
    wasScanning: false,
    previewTimer: null,
    player: {
      videoId: null,
      video: null,
      detections: [],
      activeDetectionId: null,
      pendingSeek: null,
    },
  };

  // ------------------------------------------------------------------
  // Utilities
  // ------------------------------------------------------------------
  function escapeHtml(text) {
    if (text === null || text === undefined) return "";
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatTime(sec) {
    const s = Math.max(0, Math.floor(Number(sec) || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const rem = s % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h > 0 ? `${pad(h)}:${pad(m)}:${pad(rem)}` : `${pad(m)}:${pad(rem)}`;
  }

  function formatDateTime(iso) {
    if (!iso) return "-";
    try {
      const d = new Date(iso);
      return d.toLocaleString("vi-VN", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return iso;
    }
  }

  function formatFileSize(bytes) {
    const b = Number(bytes) || 0;
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`;
    if (b < 1024 * 1024 * 1024) return `${(b / (1024 * 1024)).toFixed(1)} MB`;
    return `${(b / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  }

  async function api(url, options = {}) {
    const resp = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`;
      try {
        const data = await resp.json();
        if (data && data.detail) msg = data.detail;
      } catch {
        // ignore parse error
      }
      throw new Error(msg);
    }
    return resp.json();
  }

  function toast(message, isError = false) {
    const container = document.getElementById("toast-container");
    const el = document.createElement("div");
    el.className = `toast${isError ? " error" : ""}`;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => {
      el.remove();
    }, 4200);
  }

  // ------------------------------------------------------------------
  // Navigation
  // ------------------------------------------------------------------
  function switchTab(tabName) {
    state.activeTab = tabName;
    document.querySelectorAll(".nav-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tabName);
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.toggle("active", panel.id === `tab-${tabName}`);
    });
    if (tabName === "scan") {
      resetScanViewIfIdle();
    } else if (tabName === "history") {
      refreshHistoryAll();
    } else if (tabName === "settings") {
      loadAppInfo();
    }
  }

  async function resetScanViewIfIdle(keepFolder = false) {
    try {
      const st = await api("/api/scan/status");
      if (st && st.active) {
        renderScanStatus(st);
        ensureScanPolling();
        return;
      }
      await api("/api/scan/reset", { method: "POST" });
    } catch {
      // ignore transient error
    }

    state.wasScanning = false;
    stopScanPolling();

    if (!keepFolder) {
      document.getElementById("folder-input").value = "";
      const preview = document.getElementById("folder-preview");
      preview.classList.add("hidden");
      preview.textContent = "";
    } else {
      previewFolder();
    }

    const skipBanner = document.getElementById("scan-skip-banner");
    skipBanner.classList.add("hidden");
    skipBanner.textContent = "";

    document.getElementById("scan-progress-card").classList.add("hidden");
    document.getElementById("live-feed-wrap").classList.add("hidden");
    document.getElementById("live-feed-grid").innerHTML = "";
    document.getElementById("btn-reset-scan").classList.add("hidden");
    document.getElementById("btn-start-scan").disabled = false;
    document.getElementById("scan-running-controls").classList.add("hidden");
    document.getElementById("nav-scan-dot").classList.add("hidden");

    document.getElementById("progress-current-video").textContent = "Đang chuẩn bị...";
    document.getElementById("progress-current-path").textContent = "";
    document.getElementById("progress-elapsed").textContent = "00:00";
    document.getElementById("progress-video-count").textContent = "0 / 0";
    document.getElementById("progress-overall-pct").textContent = "0%";
    document.getElementById("progress-overall-fill").style.width = "0%";
    document.getElementById("progress-video-pct").textContent = "0%";
    document.getElementById("progress-video-fill").style.width = "0%";
    document.getElementById("live-processed").textContent = "0";
    document.getElementById("live-with-person").textContent = "0";
    document.getElementById("live-detections").textContent = "0";

    await loadAppInfo();
  }

  function switchHistoryView(viewName) {
    state.historyView = viewName;
    document.querySelectorAll(".view-tab").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.view === viewName);
    });
    document.getElementById("history-view-videos").classList.toggle("hidden", viewName !== "videos");
    document.getElementById("history-view-gallery").classList.toggle("hidden", viewName !== "gallery");
    document.getElementById("history-view-sessions").classList.toggle("hidden", viewName !== "sessions");
    loadCurrentHistorySubView();
  }

  // ------------------------------------------------------------------
  // App Info & Summary Stats
  // ------------------------------------------------------------------
  async function loadAppInfo() {
    try {
      const info = await api("/api/app-info");
      updateSidebarStats(info.stats);

      const portableText = info.portable_mode
        ? "Portable (Lưu ngay trong thư mục ứng dụng)"
        : "AppData (Thư mục ứng dụng chỉ đọc)";
      document.getElementById("info-portable-mode").textContent = portableText;
      document.getElementById("info-data-dir").textContent = info.data_dir;
      document.getElementById("portable-pill").textContent = info.portable_mode
        ? "Portable Mode · Offline AI"
        : "Desktop Mode · Offline AI";
      document.getElementById("info-model-status").textContent = info.model_ready
        ? "YOLO11n ONNX · Sẵn sàng (Offline)"
        : "Thiếu file model YOLO11n!";

      const s = info.settings || {};
      if (s.sample_interval_sec !== undefined) {
        document.getElementById("opt-interval").value = String(s.sample_interval_sec);
      }
      if (s.confidence_threshold !== undefined) {
        const pct = Math.round(Number(s.confidence_threshold) * 100);
        document.getElementById("opt-confidence").value = String(pct);
        document.getElementById("opt-confidence-val").textContent = `${pct}%`;
      }
      if (s.merge_gap_sec !== undefined) {
        document.getElementById("opt-merge-gap").value = String(s.merge_gap_sec);
      }
      if (s.recursive !== undefined) {
        document.getElementById("opt-recursive").checked = Boolean(s.recursive);
      }
      if (s.skip_scanned !== undefined) {
        document.getElementById("opt-skip-scanned").checked = Boolean(s.skip_scanned);
      }
    } catch (err) {
      toast(err.message, true);
    }
  }

  function updateSidebarStats(stats) {
    if (!stats) return;
    document.getElementById("side-total-videos").textContent = stats.total_videos ?? 0;
    document.getElementById("side-person-videos").textContent = stats.videos_with_person ?? 0;
    document.getElementById("side-total-detections").textContent = stats.total_detections ?? 0;
    document.getElementById("nav-history-badge").textContent = stats.total_detections ?? 0;
  }

  // ------------------------------------------------------------------
  // Folder Picker & Scan Controls
  // ------------------------------------------------------------------
  async function browseFolder() {
    const btn = document.getElementById("btn-browse-folder");
    const current = document.getElementById("folder-input").value.trim();
    btn.disabled = true;
    try {
      const res = await api(`/api/dialog/folder?initial_dir=${encodeURIComponent(current)}`, {
        method: "POST",
      });
      if (res.selected && res.folder_path) {
        document.getElementById("folder-input").value = res.folder_path;
        await previewFolder();
      }
    } catch (err) {
      toast("Không thể mở hộp thoại chọn thư mục: " + err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  async function previewFolder() {
    const folder = document.getElementById("folder-input").value.trim();
    const recursive = document.getElementById("opt-recursive").checked;
    const skipScanned = document.getElementById("opt-skip-scanned").checked;
    const box = document.getElementById("folder-preview");
    if (!folder) {
      box.classList.add("hidden");
      return;
    }
    try {
      const res = await api(
        `/api/folder/preview?folder_path=${encodeURIComponent(folder)}&recursive=${recursive}`
      );
      box.classList.remove("hidden");
      box.classList.remove("invalid", "info");
      if (!res.valid) {
        box.classList.add("invalid");
        box.textContent = "Thư mục không tồn tại hoặc không thể truy cập.";
      } else if (res.count === 0) {
        box.classList.add("invalid");
        box.textContent = "Không tìm thấy file video nào (.mp4, .avi, .mov, .mkv, .wmv...) trong thư mục này.";
      } else if (skipScanned && res.already_scanned_count > 0 && res.new_count === 0) {
        box.classList.add("info");
        box.innerHTML = `Tìm thấy <strong>${res.count} video</strong> — Cả <strong>${res.already_scanned_count} video</strong> đã được quét trước đó (trùng tên trong lịch sử), sẽ không quét lại.`;
      } else if (skipScanned && res.already_scanned_count > 0) {
        const sampleNames = (res.sample || []).slice(0, 4).join(", ");
        const more = res.new_count > 4 ? ` ... (+${res.new_count - 4} video mới khác)` : "";
        box.innerHTML = `Tìm thấy <strong>${res.count} video</strong>: đã có <strong>${res.already_scanned_count} video</strong> được quét rồi (sẽ bỏ qua) · Còn <strong>${res.new_count} video mới</strong> sẽ quét: ${escapeHtml(sampleNames + more)}`;
      } else {
        const namesSource = skipScanned ? (res.sample || []) : [...(res.sample || []), ...(res.skipped_sample || [])];
        const sampleNames = namesSource.slice(0, 4).join(", ");
        const more = res.count > 4 ? ` ... (+${res.count - 4} video khác)` : "";
        const alreadyHint =
          !skipScanned && res.already_scanned_count > 0
            ? ` (trong đó ${res.already_scanned_count} video đã có trong lịch sử nhưng đang tắt bỏ qua)`
            : "";
        box.innerHTML = `Tìm thấy <strong>${res.count} video</strong>${alreadyHint}: ${escapeHtml(sampleNames + more)}`;
      }
    } catch {
      box.classList.add("hidden");
    }
  }

  async function startScan() {
    const folder = document.getElementById("folder-input").value.trim();
    if (!folder) {
      toast("Vui lòng chọn thư mục chứa video trước khi quét.", true);
      return;
    }

    const payload = {
      folder_path: folder,
      recursive: document.getElementById("opt-recursive").checked,
      sample_interval_sec: parseFloat(document.getElementById("opt-interval").value),
      confidence_threshold: parseInt(document.getElementById("opt-confidence").value, 10) / 100,
      merge_gap_sec: parseFloat(document.getElementById("opt-merge-gap").value),
      skip_scanned: document.getElementById("opt-skip-scanned").checked,
    };

    try {
      const status = await api("/api/scan/start", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (status.status === "all_skipped") {
        state.wasScanning = false;
        renderScanStatus(status);
        toast(status.skip_notice || `Đã có ${status.skipped_videos} video được quét trước đó.`);
        return;
      }
      state.wasScanning = true;
      renderScanStatus(status);
      ensureScanPolling();
      if (status.skipped_videos > 0) {
        toast(`Đã bỏ qua ${status.skipped_videos} video đã quét rồi. Bắt đầu quét ${status.total_videos} video mới!`);
      } else {
        toast("Đã bắt đầu quét thư mục video!");
      }
    } catch (err) {
      toast(err.message, true);
    }
  }

  function ensureScanPolling() {
    if (state.scanPollTimer) return;
    state.scanPollTimer = setInterval(pollScanStatus, 500);
  }

  function stopScanPolling() {
    if (state.scanPollTimer) {
      clearInterval(state.scanPollTimer);
      state.scanPollTimer = null;
    }
  }

  async function pollScanStatus() {
    try {
      const st = await api("/api/scan/status");
      renderScanStatus(st);
      if (!st.active) {
        stopScanPolling();
        if (state.wasScanning) {
          state.wasScanning = false;
          const summary = await api("/api/history/summary");
          updateSidebarStats(summary);
          await previewFolder();
          if (st.status === "completed") {
            const skipPart =
              st.skipped_videos > 0 ? ` (đã bỏ qua ${st.skipped_videos} video đã quét trước đó)` : "";
            toast(
              `Hoàn tất quét! Phát hiện ${st.total_detections} khoảnh khắc có người trong ${st.videos_with_person}/${st.total_videos} video mới${skipPart}.`
            );
          } else if (st.status === "cancelled") {
            toast("Đã dừng quét. Các kết quả trước đó đã được lưu vào lịch sử.");
          } else if (st.status === "error") {
            toast("Lỗi khi quét: " + (st.error_message || "Không xác định"), true);
          }
        }
      }
    } catch {
      // ignore transient poll glitch
    }
  }

  function renderScanStatus(st) {
    const startBtn = document.getElementById("btn-start-scan");
    const runCtrl = document.getElementById("scan-running-controls");
    const pauseBtn = document.getElementById("btn-pause-scan");
    const card = document.getElementById("scan-progress-card");
    const dot = document.getElementById("nav-scan-dot");
    const skipBanner = document.getElementById("scan-skip-banner");
    const resetBtn = document.getElementById("btn-reset-scan");

    startBtn.disabled = st.active;
    runCtrl.classList.toggle("hidden", !st.active);
    dot.classList.toggle("hidden", !st.active);
    resetBtn.classList.toggle("hidden", st.active || st.status === "idle" || st.status === "all_skipped");

    if (st.skip_notice) {
      skipBanner.classList.remove("hidden");
      skipBanner.classList.toggle("warning", st.status === "all_skipped");
      skipBanner.textContent = st.skip_notice;
    } else {
      skipBanner.classList.add("hidden");
      skipBanner.textContent = "";
    }

    if (st.status === "all_skipped" || st.status === "idle") {
      card.classList.add("hidden");
      return;
    }

    card.classList.remove("hidden");

    if (st.active) {
      pauseBtn.textContent = st.paused ? "Tiếp tục" : "Tạm dừng";
      state.wasScanning = true;
    }

    const pill = document.getElementById("scan-status-pill");
    const statusLabels = {
      running: ["Đang quét AI...", "status-running"],
      paused: ["Đang tạm dừng", "status-paused"],
      stopping: ["Đang dừng...", "status-paused"],
      completed: ["Đã hoàn tất", "status-completed"],
      cancelled: ["Đã dừng giữa chừng", "status-cancelled"],
      error: ["Có lỗi xảy ra", "status-error"],
    };
    const [label, cls] = statusLabels[st.status] || ["Chờ", "status-cancelled"];
    pill.textContent = label;
    pill.className = `status-pill ${cls}`;

    document.getElementById("progress-current-video").textContent =
      st.current_video_name || (st.status === "completed" ? "Đã quét xong tất cả video" : "Đang chuẩn bị...");
    document.getElementById("progress-current-path").textContent = st.current_video_path || st.folder_path || "";
    document.getElementById("progress-elapsed").textContent = formatTime(st.elapsed_sec || 0);

    document.getElementById("progress-video-count").textContent = `${st.processed_videos} / ${st.total_videos}`;
    document.getElementById("progress-overall-pct").textContent = `${(st.overall_progress_pct || 0).toFixed(0)}%`;
    document.getElementById("progress-overall-fill").style.width = `${Math.min(100, st.overall_progress_pct || 0)}%`;

    document.getElementById("progress-video-pct").textContent = `${(st.video_progress_pct || 0).toFixed(0)}%`;
    document.getElementById("progress-video-fill").style.width = `${Math.min(100, st.video_progress_pct || 0)}%`;

    document.getElementById("live-processed").textContent = st.processed_videos ?? 0;
    document.getElementById("live-with-person").textContent = st.videos_with_person ?? 0;
    document.getElementById("live-detections").textContent = st.total_detections ?? 0;

    // Live feed snapshots
    const feedWrap = document.getElementById("live-feed-wrap");
    const feedGrid = document.getElementById("live-feed-grid");
    const items = st.recent_detections || [];
    if (items.length > 0) {
      feedWrap.classList.remove("hidden");
      feedGrid.innerHTML = items
        .map(
          (d) => `
          <div class="det-card" data-open-video="${d.video_id}" data-seek="${d.timestamp_sec}">
            <div class="det-img-wrap">
              <img src="/snapshots/${encodeURI(d.snapshot_path)}" alt="Ảnh phát hiện" loading="lazy" />
              <span class="det-time-badge">⏱ ${escapeHtml(d.formatted_time)}</span>
              <div class="det-play-overlay"><div class="det-play-circle">▶</div></div>
            </div>
            <div class="det-card-body">
              <div class="det-card-top">
                <strong class="text-emerald">${d.person_count} người</strong>
                <span class="muted">${Math.round((d.max_confidence || 0) * 100)}%</span>
              </div>
              <div class="det-video-sub" title="${escapeHtml(d.video_name)}">${escapeHtml(d.video_name)}</div>
            </div>
          </div>`
        )
        .join("");
    } else {
      feedWrap.classList.add("hidden");
    }
  }

  // ------------------------------------------------------------------
  // History Views
  // ------------------------------------------------------------------
  async function refreshHistoryAll() {
    try {
      const [summary, sessions] = await Promise.all([
        api("/api/history/summary"),
        api("/api/history/sessions?limit=100"),
      ]);
      updateSidebarStats(summary);

      const sel = document.getElementById("history-session-filter");
      const prevVal = sel.value;
      sel.innerHTML = `<option value="">Tất cả các lần quét (${sessions.length})</option>`;
      sessions.forEach((s) => {
        const folderShort = s.folder_path.split(/[\\/]/).filter(Boolean).pop() || s.folder_path;
        const opt = document.createElement("option");
        opt.value = String(s.id);
        opt.textContent = `#${s.id} · ${folderShort} (${s.videos_with_person}/${s.total_videos} video có người) · ${formatDateTime(s.started_at)}`;
        sel.appendChild(opt);
      });
      if (prevVal && Array.from(sel.options).some((o) => o.value === prevVal)) {
        sel.value = prevVal;
      }

      await loadCurrentHistorySubView(sessions);
    } catch (err) {
      toast("Không thể tải lịch sử: " + err.message, true);
    }
  }

  function getHistoryFilterParams() {
    const sessionId = document.getElementById("history-session-filter").value;
    const onlyPerson = document.getElementById("history-only-person").checked;
    const search = document.getElementById("history-search").value.trim();
    return { sessionId, onlyPerson, search };
  }

  async function loadCurrentHistorySubView(cachedSessions = null) {
    if (state.historyView === "videos") {
      await loadVideosList();
    } else if (state.historyView === "gallery") {
      await loadGallery();
    } else if (state.historyView === "sessions") {
      const sessions = cachedSessions || (await api("/api/history/sessions?limit=100"));
      renderSessionsList(sessions);
    }
  }

  async function loadVideosList() {
    const { sessionId, onlyPerson, search } = getHistoryFilterParams();
    const params = new URLSearchParams();
    if (sessionId) params.set("session_id", sessionId);
    if (onlyPerson) params.set("only_with_person", "true");
    if (search) params.set("search", search);

    const videos = await api(`/api/history/videos?${params.toString()}`);
    document.getElementById("video-list-count").textContent = `${videos.length} video`;

    const container = document.getElementById("video-list-container");
    if (videos.length === 0) {
      container.innerHTML = `<div class="empty-state" style="padding: 36px 14px;"><p>Chưa có video nào khớp bộ lọc.</p></div>`;
      if (!state.selectedVideoId) {
        showEmptyVideoDetail();
      }
      return;
    }

    container.innerHTML = videos
      .map((v) => {
        const selected = v.id === state.selectedVideoId ? "selected" : "";
        const thumbHtml = v.preview_snapshot
          ? `<img src="/snapshots/${encodeURI(v.preview_snapshot)}" alt="thumb" loading="lazy" />`
          : `<span>🎬</span>`;
        let badgeHtml = `<span class="badge-none">Không có người</span>`;
        if (v.status === "error") {
          badgeHtml = `<span class="badge-error">Lỗi đọc video</span>`;
        } else if (v.person_detected) {
          badgeHtml = `<span class="badge-person">Có người · ${v.detection_count} mốc</span>`;
        }
        const dur = v.duration_sec > 0 ? formatTime(v.duration_sec) : "--:--";
        return `
          <button class="video-item-card ${selected}" data-select-video="${v.id}">
            <div class="video-thumb-box">${thumbHtml}</div>
            <div class="video-item-info">
              <div class="video-item-name" title="${escapeHtml(v.video_path)}">${escapeHtml(v.video_name)}</div>
              <div class="video-item-meta">
                ${badgeHtml}
                <span>⏱ ${dur}</span>
              </div>
              <div class="video-item-meta">
                <span>${formatDateTime(v.scanned_at)}</span>
                ${!v.exists_on_disk ? `<span class="text-amber">· File đã di chuyển</span>` : ""}
              </div>
            </div>
          </button>`;
      })
      .join("");

    // Auto-select first video if none selected or current no longer in list
    if (!state.selectedVideoId || !videos.some((v) => v.id === state.selectedVideoId)) {
      selectVideoDetail(videos[0].id);
    } else {
      selectVideoDetail(state.selectedVideoId);
    }
  }

  function showEmptyVideoDetail() {
    document.getElementById("video-detail-empty").classList.remove("hidden");
    document.getElementById("video-detail-content").classList.add("hidden");
  }

  async function selectVideoDetail(videoId) {
    state.selectedVideoId = videoId;
    document.querySelectorAll(".video-item-card").forEach((el) => {
      el.classList.toggle("selected", Number(el.dataset.selectVideo) === videoId);
    });

    try {
      const data = await api(`/api/history/videos/${videoId}`);
      const v = data.video;
      const dets = data.detections || [];

      document.getElementById("video-detail-empty").classList.add("hidden");
      const content = document.getElementById("video-detail-content");
      content.classList.remove("hidden");

      const firstSeek = dets.length > 0 ? dets[0].timestamp_sec : 0;
      const resInfo = v.width > 0 ? `${v.width}×${v.height}` : "-";

      const detCardsHtml =
        dets.length > 0
          ? `<div class="detections-grid">
              ${dets
                .map((d) => {
                  const timeSpan =
                    d.end_sec > d.start_sec + 0.5
                      ? `${formatTime(d.start_sec)} → ${formatTime(d.end_sec)}`
                      : `Tại ${formatTime(d.timestamp_sec)}`;
                  return `
                    <div class="det-card">
                      <div class="det-img-wrap" data-open-video="${v.id}" data-seek="${d.timestamp_sec}">
                        <img src="/snapshots/${encodeURI(d.snapshot_path)}" alt="Ảnh phát hiện lúc ${formatTime(d.timestamp_sec)}" loading="lazy" />
                        <span class="det-time-badge">⏱ ${formatTime(d.timestamp_sec)}</span>
                        <div class="det-play-overlay"><div class="det-play-circle">▶</div></div>
                      </div>
                      <div class="det-card-body">
                        <div class="det-card-top">
                          <strong class="text-emerald">${d.person_count} người · ${Math.round(d.max_confidence * 100)}%</strong>
                          <span class="muted">${timeSpan}</span>
                        </div>
                        <div class="det-card-actions">
                          <button class="btn btn-primary btn-sm" data-open-video="${v.id}" data-seek="${d.timestamp_sec}">
                            ▶ Xem từ ${formatTime(d.timestamp_sec)}
                          </button>
                          ${
                            d.crop_path
                              ? `<button class="btn btn-secondary btn-sm" data-toggle-crop="1" data-full="/snapshots/${encodeURI(d.snapshot_path)}" data-crop="/snapshots/${encodeURI(d.crop_path)}">Cận cảnh</button>`
                              : ""
                          }
                        </div>
                      </div>
                    </div>`;
                })
                .join("")}
            </div>`
          : `<div class="empty-state" style="margin-top: 48px;">
              <h3>Không phát hiện người trong video này</h3>
              <p>${v.error_message ? escapeHtml(v.error_message) : "AI đã quét toàn bộ video nhưng không ghi nhận khung hình nào có người đạt ngưỡng tin cậy."}</p>
            </div>`;

      content.innerHTML = `
        <div class="detail-header">
          <div style="min-width:0;">
            <h2>${escapeHtml(v.video_name)}</h2>
            <p class="muted-path mt-sm">${escapeHtml(v.video_path)}</p>
            <div class="detail-meta-pills">
              <span class="meta-chip">⏱ Thời lượng: ${formatTime(v.duration_sec)}</span>
              <span class="meta-chip">📐 ${resInfo}</span>
              <span class="meta-chip">💾 ${formatFileSize(v.file_size)}</span>
              <span class="meta-chip">👤 ${dets.length} mốc phát hiện</span>
              <span class="meta-chip">🕒 Quét: ${formatDateTime(v.scanned_at)}</span>
            </div>
          </div>
          <div class="row-gap" style="flex-shrink:0;">
            <button class="btn btn-primary btn-sm" data-open-video="${v.id}" data-seek="${firstSeek}">▶ Phát video</button>
            <button class="btn btn-secondary btn-sm" data-open-folder="${v.id}">Mở thư mục</button>
            <button class="btn btn-danger-outline btn-sm" data-delete-video="${v.id}">Xóa</button>
          </div>
        </div>
        ${detCardsHtml}
      `;
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function loadGallery() {
    const { sessionId, search } = getHistoryFilterParams();
    const params = new URLSearchParams();
    if (sessionId) params.set("session_id", sessionId);
    if (search) params.set("search", search);

    const dets = await api(`/api/history/detections?${params.toString()}`);
    const grid = document.getElementById("gallery-grid");

    if (dets.length === 0) {
      grid.innerHTML = `<div class="card empty-state" style="grid-column: 1 / -1; padding: 48px;"><h3>Chưa có ảnh phát hiện người nào</h3><p>Hãy quét một thư mục video để xem thư viện ảnh tại đây.</p></div>`;
      return;
    }

    grid.innerHTML = dets
      .map(
        (d) => `
        <div class="det-card">
          <div class="det-img-wrap" data-open-video="${d.video_id}" data-seek="${d.timestamp_sec}">
            <img src="/snapshots/${encodeURI(d.snapshot_path)}" alt="${escapeHtml(d.video_name)}" loading="lazy" />
            <span class="det-time-badge">⏱ ${formatTime(d.timestamp_sec)}</span>
            <div class="det-play-overlay"><div class="det-play-circle">▶</div></div>
          </div>
          <div class="det-card-body">
            <div class="det-card-top">
              <strong class="text-emerald">${d.person_count} người · ${Math.round(d.max_confidence * 100)}%</strong>
              <span class="muted">${formatTime(d.duration_sec)}</span>
            </div>
            <div class="det-video-sub" title="${escapeHtml(d.video_path)}">${escapeHtml(d.video_name)}</div>
            <div class="det-card-actions">
              <button class="btn btn-primary btn-sm" data-open-video="${d.video_id}" data-seek="${d.timestamp_sec}">
                ▶ Xem lúc ${formatTime(d.timestamp_sec)}
              </button>
            </div>
          </div>
        </div>`
      )
      .join("");
  }

  function renderSessionsList(sessions) {
    const list = document.getElementById("sessions-list");
    if (!sessions || sessions.length === 0) {
      list.innerHTML = `<div class="card empty-state" style="padding: 48px;"><h3>Chưa có lịch sử lần quét nào</h3></div>`;
      return;
    }

    list.innerHTML = sessions
      .map(
        (s) => `
        <div class="session-row-card">
          <div class="session-main">
            <h4>#${s.id} · ${escapeHtml(s.folder_path)}</h4>
            <div class="session-stats">
              <span>🕒 ${formatDateTime(s.started_at)}</span>
              <span>🎬 ${s.processed_videos}/${s.total_videos} video mới</span>
              ${s.skipped_videos > 0 ? `<span>⏭ Đã bỏ qua ${s.skipped_videos} video đã quét</span>` : ""}
              <span class="text-emerald">👤 ${s.videos_with_person} video có người (${s.total_detections} khoảnh khắc)</span>
              <span>⚙️ Lấy mẫu ${s.sample_interval_sec}s · Ngưỡng ${Math.round(s.confidence_threshold * 100)}%</span>
            </div>
          </div>
          <div class="row-gap">
            <button class="btn btn-secondary btn-sm" data-filter-session="${s.id}">Xem kết quả</button>
            <button class="btn btn-danger-outline btn-sm" data-delete-session="${s.id}">Xóa lần quét</button>
          </div>
        </div>`
      )
      .join("");
  }

  // ------------------------------------------------------------------
  // Built-in Video Player Modal with Seek-to-Timestamp
  // ------------------------------------------------------------------
  async function openPlayerAt(videoId, seekSec = 0) {
    const modal = document.getElementById("player-modal");
    const videoEl = document.getElementById("video-player");
    const loadingEl = document.getElementById("player-loading");
    const loadingText = document.getElementById("player-loading-text");
    const missingBanner = document.getElementById("player-missing-banner");

    modal.classList.remove("hidden");
    missingBanner.classList.add("hidden");
    loadingEl.classList.remove("hidden");
    loadingText.textContent = "Đang tải thông tin và chuẩn bị video...";

    try {
      const detail = await api(`/api/history/videos/${videoId}`);
      state.player.videoId = videoId;
      state.player.video = detail.video;
      state.player.detections = detail.detections || [];
      state.player.pendingSeek = Number(seekSec) || 0;

      document.getElementById("player-video-title").textContent = detail.video.video_name;
      document.getElementById("player-video-meta").textContent = detail.video.video_path;
      renderPlayerDetectionsList();
      renderTimelineMarkers();

      // Same video already loaded? Jump straight to timestamp!
      if (videoEl.dataset.videoId === String(videoId) && videoEl.readyState >= 1) {
        loadingEl.classList.add("hidden");
        seekAndPlay(state.player.pendingSeek);
        return;
      }

      videoEl.pause();
      videoEl.removeAttribute("src");
      videoEl.load();
      videoEl.dataset.videoId = "";

      loadingText.textContent = "Đang kiểm tra định dạng video (tự chuyển H.264 nếu là AVI/MKV)...";
      const prep = await api(`/api/video/${videoId}/prepare`);

      if (prep.missing) {
        loadingEl.classList.add("hidden");
        missingBanner.classList.remove("hidden");
        return;
      }

      videoEl.dataset.videoId = String(videoId);
      const speedVal = parseFloat(document.getElementById("player-speed").value) || 1.0;

      const onReady = () => {
        loadingEl.classList.add("hidden");
        videoEl.playbackRate = speedVal;
        seekAndPlay(state.player.pendingSeek);
      };

      videoEl.addEventListener("loadedmetadata", onReady, { once: true });
      videoEl.src = prep.stream_url;
      videoEl.load();
    } catch (err) {
      loadingEl.classList.add("hidden");
      toast("Không thể mở video: " + err.message, true);
    }
  }

  function seekAndPlay(targetSec) {
    const videoEl = document.getElementById("video-player");
    const clamped = Math.max(0, Number(targetSec) || 0);
    if (videoEl.readyState >= 1) {
      const maxDur = Number.isFinite(videoEl.duration) ? videoEl.duration : clamped + 1;
      videoEl.currentTime = Math.min(clamped, Math.max(0, maxDur - 0.1));
      videoEl.play().catch(() => {});
      highlightClosestDetection(clamped);
    } else {
      state.player.pendingSeek = clamped;
    }
  }

  function closePlayer() {
    const modal = document.getElementById("player-modal");
    const videoEl = document.getElementById("video-player");
    videoEl.pause();
    modal.classList.add("hidden");
  }

  function renderPlayerDetectionsList() {
    const list = document.getElementById("player-det-list");
    const dets = state.player.detections;
    document.getElementById("player-det-count").textContent = dets.length;

    if (dets.length === 0) {
      list.innerHTML = `<div class="muted" style="padding: 16px;">Video này chưa ghi nhận khoảnh khắc có người.</div>`;
      return;
    }

    list.innerHTML = dets
      .map(
        (d) => `
        <button class="player-det-item" data-player-det-id="${d.id}" data-player-seek="${d.timestamp_sec}">
          <img src="/snapshots/${encodeURI(d.snapshot_path)}" alt="snapshot" loading="lazy" />
          <div style="min-width:0; flex:1;">
            <div class="text-emerald" style="font-weight:700; font-variant-numeric:tabular-nums;">⏱ ${formatTime(d.timestamp_sec)}</div>
            <div style="font-size:12px;">${d.person_count} người · ${Math.round(d.max_confidence * 100)}%</div>
            <div class="muted" style="font-size:11.5px;">${formatTime(d.start_sec)} → ${formatTime(d.end_sec)}</div>
          </div>
        </button>`
      )
      .join("");
  }

  function renderTimelineMarkers() {
    const container = document.getElementById("player-timeline-markers");
    const videoEl = document.getElementById("video-player");
    const dur =
      (Number.isFinite(videoEl.duration) && videoEl.duration > 0
        ? videoEl.duration
        : state.player.video?.duration_sec) || 1;

    container.innerHTML = state.player.detections
      .map((d) => {
        const pct = Math.max(0, Math.min(100, (d.timestamp_sec / dur) * 100));
        return `<div class="timeline-pin" style="left:${pct}%" title="Nhảy tới ${formatTime(d.timestamp_sec)} (${d.person_count} người)" data-pin-seek="${d.timestamp_sec}" data-pin-id="${d.id}"></div>`;
      })
      .join("");
  }

  function highlightClosestDetection(curTime) {
    const dets = state.player.detections;
    let activeId = null;
    for (const d of dets) {
      if (curTime >= d.start_sec - 0.8 && curTime <= d.end_sec + 1.2) {
        activeId = d.id;
        break;
      }
    }
    state.player.activeDetectionId = activeId;
    document.querySelectorAll(".player-det-item").forEach((el) => {
      el.classList.toggle("active", Number(el.dataset.playerDetId) === activeId);
    });
    document.querySelectorAll(".timeline-pin").forEach((el) => {
      el.classList.toggle("active", Number(el.dataset.pinId) === activeId);
    });
  }

  async function relocateMissingVideo() {
    const video = state.player.video;
    if (!video) return;
    try {
      const picked = await api(`/api/dialog/video-file?initial_dir=${encodeURIComponent(video.folder_path)}`, {
        method: "POST",
      });
      if (!picked.selected || !picked.file_path) return;

      await api(`/api/history/videos/${video.id}/relocate`, {
        method: "POST",
        body: JSON.stringify({ new_path: picked.file_path }),
      });
      toast("Đã cập nhật đường dẫn video thành công!");
      const videoEl = document.getElementById("video-player");
      videoEl.dataset.videoId = "";
      await openPlayerAt(video.id, state.player.pendingSeek || 0);
      if (state.activeTab === "history") {
        refreshHistoryAll();
      }
    } catch (err) {
      toast("Không thể cập nhật đường dẫn: " + err.message, true);
    }
  }

  // ------------------------------------------------------------------
  // Event Wiring
  // ------------------------------------------------------------------
  function bindEvents() {
    // Sidebar navigation
    document.querySelectorAll(".nav-btn").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });

    // History view switcher
    document.querySelectorAll(".view-tab").forEach((btn) => {
      btn.addEventListener("click", () => switchHistoryView(btn.dataset.view));
    });

    // Folder input & options
    document.getElementById("btn-browse-folder").addEventListener("click", browseFolder);
    document.getElementById("folder-input").addEventListener("input", () => {
      clearTimeout(state.previewTimer);
      state.previewTimer = setTimeout(previewFolder, 350);
    });
    document.getElementById("opt-recursive").addEventListener("change", previewFolder);
    document.getElementById("opt-skip-scanned").addEventListener("change", previewFolder);
    document.getElementById("opt-confidence").addEventListener("input", (e) => {
      document.getElementById("opt-confidence-val").textContent = `${e.target.value}%`;
    });

    // Scan controls
    document.getElementById("btn-start-scan").addEventListener("click", startScan);
    document.getElementById("btn-reset-scan").addEventListener("click", () => resetScanViewIfIdle(true));
    document.getElementById("btn-pause-scan").addEventListener("click", async () => {
      const st = await api("/api/scan/status");
      const next = await api(st.paused ? "/api/scan/resume" : "/api/scan/pause", { method: "POST" });
      renderScanStatus(next);
    });
    document.getElementById("btn-stop-scan").addEventListener("click", async () => {
      const next = await api("/api/scan/stop", { method: "POST" });
      renderScanStatus(next);
    });

    // History filters
    document.getElementById("btn-refresh-history").addEventListener("click", refreshHistoryAll);
    document.getElementById("history-session-filter").addEventListener("change", () => loadCurrentHistorySubView());
    document.getElementById("history-only-person").addEventListener("change", () => loadCurrentHistorySubView());
    let searchTimer = null;
    document.getElementById("history-search").addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => loadCurrentHistorySubView(), 250);
    });

    // Settings buttons
    document.getElementById("btn-open-data-dir").addEventListener("click", async () => {
      await api("/api/app/open-data-dir", { method: "POST" });
    });
    document.getElementById("btn-clear-history").addEventListener("click", async () => {
      if (!confirm("Bạn có chắc chắn muốn xóa TOÀN BỘ lịch sử quét và tất cả ảnh đã chụp?")) return;
      try {
        await api("/api/history/all", { method: "DELETE" });
        state.selectedVideoId = null;
        await loadAppInfo();
        toast("Đã xóa sạch toàn bộ lịch sử.");
      } catch (err) {
        toast(err.message, true);
      }
    });

    // Global delegated clicks (open video at timestamp, select video, delete, crop toggle, etc.)
    document.addEventListener("click", async (e) => {
      const openBtn = e.target.closest("[data-open-video]");
      if (openBtn) {
        const vid = Number(openBtn.dataset.openVideo);
        const seek = Number(openBtn.dataset.seek || 0);
        openPlayerAt(vid, seek);
        return;
      }

      const selectVidBtn = e.target.closest("[data-select-video]");
      if (selectVidBtn) {
        selectVideoDetail(Number(selectVidBtn.dataset.selectVideo));
        return;
      }

      const cropBtn = e.target.closest("[data-toggle-crop]");
      if (cropBtn) {
        const card = cropBtn.closest(".det-card");
        const img = card.querySelector("img");
        const showingCrop = cropBtn.dataset.showingCrop === "1";
        img.src = showingCrop ? cropBtn.dataset.full : cropBtn.dataset.crop;
        cropBtn.dataset.showingCrop = showingCrop ? "0" : "1";
        cropBtn.textContent = showingCrop ? "Cận cảnh" : "Toàn khung";
        return;
      }

      const openFolderBtn = e.target.closest("[data-open-folder]");
      if (openFolderBtn) {
        try {
          await api(`/api/video/${openFolderBtn.dataset.openFolder}/open-folder`, { method: "POST" });
        } catch (err) {
          toast(err.message, true);
        }
        return;
      }

      const delVidBtn = e.target.closest("[data-delete-video]");
      if (delVidBtn) {
        if (!confirm("Xóa lịch sử và ảnh chụp của video này?")) return;
        const vid = Number(delVidBtn.dataset.deleteVideo);
        await api(`/api/history/videos/${vid}`, { method: "DELETE" });
        if (state.selectedVideoId === vid) state.selectedVideoId = null;
        await refreshHistoryAll();
        toast("Đã xóa bản ghi video.");
        return;
      }

      const filterSessBtn = e.target.closest("[data-filter-session]");
      if (filterSessBtn) {
        document.getElementById("history-session-filter").value = filterSessBtn.dataset.filterSession;
        state.selectedVideoId = null;
        switchHistoryView("videos");
        return;
      }

      const delSessBtn = e.target.closest("[data-delete-session]");
      if (delSessBtn) {
        if (!confirm("Xóa toàn bộ video và ảnh chụp thuộc lần quét này?")) return;
        await api(`/api/history/sessions/${delSessBtn.dataset.deleteSession}`, { method: "DELETE" });
        state.selectedVideoId = null;
        await refreshHistoryAll();
        toast("Đã xóa lần quét.");
        return;
      }

      const pinEl = e.target.closest("[data-pin-seek]");
      if (pinEl) {
        seekAndPlay(Number(pinEl.dataset.pinSeek));
        return;
      }

      const playerDetBtn = e.target.closest("[data-player-seek]");
      if (playerDetBtn) {
        seekAndPlay(Number(playerDetBtn.dataset.playerSeek));
      }
    });

    // Player modal controls
    document.getElementById("btn-player-close").addEventListener("click", closePlayer);
    document.getElementById("player-backdrop").addEventListener("click", closePlayer);
    document.getElementById("btn-relocate-video").addEventListener("click", relocateMissingVideo);
    document.getElementById("btn-player-open-external").addEventListener("click", async () => {
      if (!state.player.videoId) return;
      try {
        await api(`/api/video/${state.player.videoId}/open-external`, { method: "POST" });
      } catch (err) {
        toast(err.message, true);
      }
    });
    document.getElementById("btn-player-open-folder").addEventListener("click", async () => {
      if (!state.player.videoId) return;
      try {
        await api(`/api/video/${state.player.videoId}/open-folder`, { method: "POST" });
      } catch (err) {
        toast(err.message, true);
      }
    });

    const videoEl = document.getElementById("video-player");
    videoEl.addEventListener("loadedmetadata", renderTimelineMarkers);
    videoEl.addEventListener("timeupdate", () => {
      const dur = Number.isFinite(videoEl.duration) && videoEl.duration > 0 ? videoEl.duration : 0;
      const cur = videoEl.currentTime || 0;
      document.getElementById("player-current-time-label").textContent = `${formatTime(cur)} / ${formatTime(dur)}`;
      if (dur > 0) {
        document.getElementById("player-timeline-progress").style.width = `${Math.min(100, (cur / dur) * 100)}%`;
      }
      highlightClosestDetection(cur);
    });

    document.getElementById("player-timeline-bar").addEventListener("click", (e) => {
      if (e.target.closest(".timeline-pin")) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const dur = Number.isFinite(videoEl.duration) && videoEl.duration > 0
        ? videoEl.duration
        : (state.player.video?.duration_sec || 0);
      if (dur > 0) seekAndPlay(ratio * dur);
    });

    document.getElementById("btn-seek-back").addEventListener("click", () => {
      seekAndPlay((videoEl.currentTime || 0) - 5);
    });
    document.getElementById("btn-seek-fwd").addEventListener("click", () => {
      seekAndPlay((videoEl.currentTime || 0) + 5);
    });
    document.getElementById("btn-prev-det").addEventListener("click", () => {
      const cur = videoEl.currentTime || 0;
      const prev = [...state.player.detections].reverse().find((d) => d.timestamp_sec < cur - 1.2);
      if (prev) seekAndPlay(prev.timestamp_sec);
    });
    document.getElementById("btn-next-det").addEventListener("click", () => {
      const cur = videoEl.currentTime || 0;
      const next = state.player.detections.find((d) => d.timestamp_sec > cur + 1.0);
      if (next) seekAndPlay(next.timestamp_sec);
    });
    document.getElementById("player-speed").addEventListener("change", (e) => {
      videoEl.playbackRate = parseFloat(e.target.value) || 1.0;
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !document.getElementById("player-modal").classList.contains("hidden")) {
        closePlayer();
      }
    });
  }

  // ------------------------------------------------------------------
  // Bootstrap
  // ------------------------------------------------------------------
  async function init() {
    bindEvents();
    await loadAppInfo();
    const st = await api("/api/scan/status").catch(() => null);
    if (st && st.active) {
      renderScanStatus(st);
      ensureScanPolling();
    }
  }

  init();
})();
