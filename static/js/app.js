// ── Global State ─────────────────────────────────────────────────────────────
let currentView = 'sessions';
let currentSessionId = null;
let currentSessionData = null;
let sessions = [];
let galleryItems = [];
let modalIndex = 0;
let currentEditId = null;
let originalEditPromptText = '';
let originalEditConfig = {};
let currentEditHistory = [];
let editModalDbTags = [];
let lastSessionId = null;
let touchStartX = 0;
let touchStartY = 0;
let docTouchStartX = 0;
let docTouchStartY = 0;
let isMultiTouch = false;
let isDesktop = window.matchMedia('(min-width: 768px)').matches;

let loadedSessionId = null;
let newImageIds = new Set();
try {
  const saved = localStorage.getItem('new_image_ids');
  if (saved) {
    newImageIds = new Set(JSON.parse(saved));
  }
} catch (e) {
  console.error('Failed to load newImageIds:', e);
}

function saveNewImageIds() {
  try {
    localStorage.setItem('new_image_ids', JSON.stringify(Array.from(newImageIds)));
  } catch (e) {
    console.error('Failed to save newImageIds:', e);
  }
}

// Card animation tracking
const dealtCardIds = new Set();

// Bulk Selection
let isSelectionMode = false;
let selectedIds = new Set();

// AI follow-up tracking
let lastAISuggestion = null;
let lastAIInstruction = null;

// ── Toast ───────────────────────────────────────────────────────────────────
function showToast(message) {
  const existing = document.querySelector('.toast-notification');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.className = 'toast-notification';
  toast.innerHTML = `
    <svg width="14" height="14" fill="none" stroke="#10b981" viewBox="0 0 24 24" style="stroke-width: 3px;">
      <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>
    </svg>
    <span>${message}</span>`;
  document.body.appendChild(toast);
  toast.offsetHeight;
  toast.classList.add('show');
  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 300);
  }, 2000);
}

// ── DOM Refs ──────────────────────────────────────────────────────────────────
const imageModal  = document.getElementById('imageModal');
const batchModal  = document.getElementById('batchModal');
const editModal   = document.getElementById('editModal');
const viewImage   = document.getElementById('viewFullImage');
const viewPrompt  = document.getElementById('viewPrompt');
const viewTemplate = document.getElementById('viewTemplate');
const modalCounter = document.getElementById('modalCounter');
const btnPrev     = document.getElementById('btnPrev');
const btnNext     = document.getElementById('btnNext');

if (isDesktop) {
  if (btnPrev) btnPrev.style.display = 'flex';
  if (btnNext) btnNext.style.display = 'flex';
}

// ── Button listeners ─────────────────────────────────────────────────────────
const btnCloseModal = document.getElementById('btnCloseModal');
if (btnCloseModal) btnCloseModal.addEventListener('click', () => closeImageModal());

const btnOpenEdit = document.getElementById('btnOpenEdit');
if (btnOpenEdit) btnOpenEdit.addEventListener('click', openEditModal);

// Drawer toggle
let isDrawerOpen = false;
function toggleDrawer(forceState) {
  const drawer = document.getElementById('detailsDrawer');
  const btnText = document.getElementById('btnToggleDrawerText');
  const btnIcon = document.getElementById('btnToggleDrawerIcon');
  if (!drawer) return;

  if (typeof forceState === 'boolean') {
    isDrawerOpen = forceState;
  } else {
    isDrawerOpen = !isDrawerOpen;
  }

  drawer.style.transform = '';

  if (isDrawerOpen) {
    drawer.classList.add('open');
    if (btnText) btnText.textContent = 'Hide Details';
    if (btnIcon) btnIcon.textContent = '▼';
  } else {
    drawer.classList.remove('open');
    if (btnText) btnText.textContent = 'Show Details';
    if (btnIcon) btnIcon.textContent = '▲';
  }
}
const btnToggleDrawer = document.getElementById('btnToggleDrawer');
if (btnToggleDrawer) btnToggleDrawer.addEventListener('click', () => toggleDrawer());

// ── Data loading ─────────────────────────────────────────────────────────────
let isLoadingSessions = false;
let isLoadingItems = false;

async function loadState() {
  if (currentView === 'sessions') {
    await loadSessions();
  } else if (currentView === 'sessionDetail' && currentSessionId) {
    await loadSessionItems();
  }
}

async function loadSessions() {
  if (isLoadingSessions) return;
  isLoadingSessions = true;
  try {
    const res = await fetch('/api/sessions');
    sessions = await res.json();
    renderSessions();
  } catch(e) {
    console.error('loadSessions error:', e);
  } finally {
    isLoadingSessions = false;
  }
}

async function loadSessionItems() {
  if (isLoadingItems || !currentSessionId) return;
  isLoadingItems = true;
  try {
    const prevCompletedIds = new Set(
      galleryItems
        .filter(item => item.status === 'completed')
        .map(item => item.id)
    );
    const isFirstLoad = (loadedSessionId !== currentSessionId);

    const res = await fetch(`/api/state?session_id=${encodeURIComponent(currentSessionId)}&limit=200`);
    galleryItems = await res.json();

    if (!isFirstLoad) {
      galleryItems.forEach(item => {
        if (item.status === 'completed' && !prevCompletedIds.has(item.id)) {
          newImageIds.add(item.id);
        }
      });
      saveNewImageIds();
    }
    loadedSessionId = currentSessionId;

    if (currentSessionData) {
      let total = 0;
      let completed = 0;
      let pending = 0;
      let failed = 0;
      
      galleryItems.forEach(item => {
        if (item.parent_id === null || item.parent_id === undefined) {
          total++;
          const status = item.status || 'pending';
          if (status === 'completed') {
            completed++;
          } else if (status === 'pending') {
            pending++;
          } else if (status === 'failed') {
            failed++;
          }
        }
      });
      
      currentSessionData.total = total;
      currentSessionData.completed = completed;
      currentSessionData.pending = pending;
      currentSessionData.failed = failed;
    }

    renderGallery();
  } catch(e) {
    console.error('loadSessionItems error:', e);
  } finally {
    isLoadingItems = false;
  }
}

// ── Render Sessions Grid ───────────────────────────────────────────────────────
function renderSessions() {
  const btnSel = document.getElementById('btnToggleSelection');
  if (btnSel) btnSel.style.display = 'flex';
  const container = document.getElementById('gallery');
  if (!container) return;
  container.innerHTML = '';

  if (sessions.length === 0) {
    container.innerHTML = `
      <div class="col-span-full flex flex-col items-center justify-center py-20 gap-4">
        <p style="color:#6b7280;font-size:14px;">No sessions yet.</p>
        <p style="color:#4b5563;font-size:12px;">Click <b style="color:#e5e7eb;">+ New Batch</b> to generate a session.</p>
      </div>`;
    return;
  }

  sessions.forEach((s, idx) => {
    const card = document.createElement('div');
    card.className = 'gallery-card';
    card.style.cssText = 'cursor:pointer;';

    const cfg = s.config || {};
    const charName = cfg.subject || '?';
    const appearanceName = cfg.appearance || '?';

    let previewHTML = '';
    if (s.preview_id) {
      previewHTML = `<img src="/thumbnails/${s.preview_id}.jpg" loading="lazy" style="width:100%;height:100%;object-fit:cover;">`;
    } else if (s.pending > 0) {
      previewHTML = `
        <div style="width:100%;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;background:#0a0a0a;">
          <div class="spinner"></div>
          <p style="font-size:11px;color:#6b7280;">${s.completed}/${s.total} done</p>
        </div>`;
    } else {
      previewHTML = `
        <div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#0a0a0a;">
          <p style="font-size:12px;color:#ef4444;">${s.failed}/${s.total} failed</p>
        </div>`;
    }

    card.innerHTML = previewHTML;

    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:absolute;bottom:0;left:0;right:0;background:linear-gradient(transparent, rgba(0,0,0,0.85));padding:12px 10px 10px;pointer-events:none;';
    overlay.innerHTML = `
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px;">
        <span style="font-size:10px;font-weight:700;color:#fff;text-transform:uppercase;letter-spacing:0.05em;">${charName}</span>
        <span style="font-size:9px;color:#9ca3af;">· ${appearanceName}</span>
      </div>
      <div style="display:flex;align-items:center;gap:6px;">
        <div style="flex:1;height:3px;background:#374151;border-radius:2px;overflow:hidden;">
          <div style="height:100%;background:#3b82f6;border-radius:2px;width:${s.total > 0 ? (s.completed / s.total * 100) : 0}%;"></div>
        </div>
        <span style="font-size:10px;color:#d1d5db;font-weight:600;white-space:nowrap;">${s.completed}/${s.total}</span>
      </div>
      <p style="font-size:8px;color:#4b5563;font-family:monospace;margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${s.session_id}</p>`;
    card.appendChild(overlay);

    if (s.failed > 0) {
      const failBadge = document.createElement('div');
      failBadge.style.cssText = 'position:absolute;top:8px;right:8px;background:rgba(239,68,68,0.85);color:#fff;font-size:9px;font-weight:700;padding:2px 6px;border-radius:999px;z-index:5;display:flex;align-items:center;gap:4px;';
      failBadge.innerHTML = `<span>${s.failed} err</span>`;
      
      const retryBtn = document.createElement('button');
      retryBtn.style.cssText = 'background:#2563eb;color:#fff;border:none;border-radius:3px;padding:1px 4px;font-size:8px;font-weight:bold;cursor:pointer;line-height:1;margin-left:2px;';
      retryBtn.innerHTML = '&#9654;';
      retryBtn.title = 'Retry all failed';
      retryBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        retryAllFailed(s.session_id, e);
      });
      failBadge.appendChild(retryBtn);
      card.appendChild(failBadge);
    }

    if (isSelectionMode) {
      const isSelected = selectedIds.has(s.session_id);
      const selIndicator = document.createElement('div');
      selIndicator.style.cssText = `
        position:absolute; top:10px; left:10px;
        width:24px; height:24px; border-radius:50%;
        border:2px solid ${isSelected ? '#2563eb' : '#9ca3af'};
        background:${isSelected ? '#2563eb' : 'rgba(0,0,0,0.5)'};
        z-index:10; display:flex; align-items:center; justify-content:center;
        transition: all 0.15s ease;
      `;
      if (isSelected) {
        selIndicator.innerHTML = `
          <svg width="12" height="12" fill="none" stroke="white" viewBox="0 0 24 24" style="stroke-width: 4px;">
            <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>
          </svg>`;
      }
      card.appendChild(selIndicator);
      card.addEventListener('click', (e) => {
        e.preventDefault();
        toggleItemSelection(s.session_id);
      });
    } else {
      card.addEventListener('click', () => openSession(s.session_id));
    }
    container.appendChild(card);

    const cardId = s.session_id;
    if (dealtCardIds.has(cardId)) {
      card.style.transition = 'none';
      card.classList.add('dealt');
      requestAnimationFrame(() => { card.style.transition = ''; });
    } else {
      setTimeout(() => {
        card.classList.add('dealt');
        dealtCardIds.add(cardId);
      }, Math.min(idx * 30, 450));
    }
  });
}

// ── Session navigation ─────────────────────────────────────────────────────────
async function openSession(sid) {
  currentView = 'sessionDetail';
  currentSessionId = sid;
  loadedSessionId = null;
  currentSessionData = sessions.find(s => s.session_id === sid);
  await loadSessionItems();
}

async function backToSessions() {
  currentView = 'sessions';
  currentSessionId = null;
  currentSessionData = null;
  galleryItems = [];
  dealtCardIds.clear();
  isLoadingItems = false;
  disableSelectionMode();
  await loadSessions();
}

// ── Render Gallery Grid (session detail) ────────────────────────────────────────
function renderGallery() {
  const container = document.getElementById('gallery');
  if (!container) return;
  container.innerHTML = '';

  const btnSel = document.getElementById('btnToggleSelection');
  if (btnSel) btnSel.style.display = 'flex';

  if (currentView === 'sessionDetail' && currentSessionData) {
    const sd = currentSessionData;
    const cfg = sd.config || {};
    const header = document.createElement('div');
    header.className = 'col-span-full';
    header.style.cssText = 'display:flex;align-items:center;gap:12px;padding:4px 0 8px;';
    header.innerHTML = `
      <button id="btnBackToSessions"
        style="background:#1f2937;color:#fff;border:none;border-radius:50%;width:40px;height:40px;font-size:16px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
        ←
      </button>
      <div style="min-width:0;flex-grow:1;">
        <p style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.05em;">${cfg.subject || '?'} · ${cfg.appearance || '?'}</p>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <p style="font-size:12px;color:#9ca3af;margin:0;">${sd.completed}/${sd.total} completed · ${sd.pending} pending${sd.failed > 0 ? ` · <span style="color:#ef4444;font-weight:600;">${sd.failed} failed</span>` : ''}</p>
          ${sd.failed > 0 ? `
            <button onclick="retryAllFailed('${currentSessionId}', event)"
              style="background:#2563eb;color:#fff;border:none;border-radius:4px;padding:2px 8px;font-size:10px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:4px;transition:background 0.15s;"
              onmouseover="this.style.background='#1d4ed8'"
              onmouseout="this.style.background='#2563eb'">
              &#9654; Run Failed
            </button>
          ` : ''}
        </div>
        <p style="font-size:9px;color:#4b5563;font-family:monospace;margin-top:2px;user-select:text;">${currentSessionId}</p>
      </div>
    `;
    container.appendChild(header);
    container.querySelector('#btnBackToSessions').addEventListener('click', backToSessions);
  }

  if (galleryItems.length === 0 && currentView === 'sessionDetail') {
    container.innerHTML += '<div class="col-span-full" style="color:#6b7280;text-align:center;padding:40px;">No images in this session.</div>';
    return;
  }

  const total = galleryItems.length;

  galleryItems.forEach((item, idx) => {
    const card = document.createElement('div');
    card.className = 'gallery-card';
    card.setAttribute('data-id', item.id);

    const globalIndex = (item.segment_index != null ? item.segment_index : idx) + 1;

    if (item.status === 'pending') {
      const progress = item.progress !== undefined ? Math.round(item.progress * 100) : 0;
      card.innerHTML = `
        <div style="position:relative;width:100%;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;background:#0a0a0a;padding:16px;">
          <button onclick="deleteSingleItem('${item.id}', event)"
            style="position:absolute;top:8px;left:8px;background:rgba(239,68,68,0.25);color:#ef4444;border:none;border-radius:50%;width:26px;height:26px;font-size:12px;font-weight:bold;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background 0.15s;border:1px solid rgba(239,68,68,0.4);z-index:5;"
            onmouseover="this.style.background='rgba(239,68,68,0.4)'"
            onmouseout="this.style.background='rgba(239,68,68,0.25)'"
            title="Cancel">✕</button>
          <div class="spinner"></div>
          <p style="font-size:10px;color:#6b7280;">${globalIndex}/${total}</p>
          ${progress > 0 ? `
            <div style="width:80%;background:#1f2937;border-radius:4px;height:6px;overflow:hidden;margin-top:-4px;">
              <div style="width:${progress}%;background:linear-gradient(90deg, #3b82f6, #60a5fa);height:100%;transition:width 0.2s ease-out;"></div>
            </div>
            <p style="font-size:9px;color:#9ca3af;margin-top:-8px;font-family:monospace;">${progress}%</p>
          ` : ''}
        </div>`;

    } else if (item.status === 'failed') {
      card.innerHTML = `
        <div style="position:relative;width:100%;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;background:#0a0a0a;padding:16px;cursor:pointer;">
          <p style="font-size:12px;color:#ef4444;font-weight:600;">Failed</p>
          <p style="font-size:10px;color:#6b7280;">${globalIndex}/${total}</p>
          <p style="font-size:9px;color:#3b82f6;margin-top:4px;">Click to Retry</p>
        </div>`;
      card.addEventListener('click', () => {
        modalIndex = idx;
        openEditModal();
      });
    } else {
      const img = document.createElement('img');
      img.src = `/thumbnails/${item.id}.jpg?v=${item.prompt_id || '0'}`;
      img.loading = 'lazy';
      img.alt = `#${globalIndex}`;
      card.appendChild(img);
      card.addEventListener('click', () => openImageModal(idx));

      const idxBadge = document.createElement('div');
      idxBadge.style.cssText = 'position:absolute;bottom:8px;left:8px;background:rgba(0,0,0,0.7);color:#fff;font-size:10px;font-weight:700;padding:2px 7px;border-radius:999px;z-index:5;';
      idxBadge.textContent = `${globalIndex}/${total}`;
      card.appendChild(idxBadge);

      if (newImageIds.has(item.id)) {
        const newBadge = document.createElement('div');
        newBadge.className = 'badge-new';
        newBadge.textContent = 'NEW';
        card.appendChild(newBadge);
      }

      if (item.upscaled) {
        const badge = document.createElement('div');
        badge.style.cssText = 'position:absolute;top:8px;right:8px;background:#7c3aed;color:#fff;font-size:9px;font-weight:700;padding:2px 8px;border-radius:999px;z-index:5;text-transform:uppercase;letter-spacing:0.05em;';
        badge.textContent = '4x';
        card.appendChild(badge);
      }
    }

    if (isSelectionMode) {
      const isSelected = selectedIds.has(item.id);
      const selIndicator = document.createElement('div');
      selIndicator.style.cssText = `
        position:absolute; top:10px; right:10px;
        width:24px; height:24px; border-radius:50%;
        border:2px solid ${isSelected ? '#2563eb' : '#9ca3af'};
        background:${isSelected ? '#2563eb' : 'rgba(0,0,0,0.5)'};
        z-index:10; display:flex; align-items:center; justify-content:center;
        transition: all 0.15s ease;
      `;
      if (isSelected) {
        selIndicator.innerHTML = `
          <svg width="12" height="12" fill="none" stroke="white" viewBox="0 0 24 24" style="stroke-width: 4px;">
            <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>
          </svg>`;
      }
      card.appendChild(selIndicator);
      card.addEventListener('click', (e) => {
        e.preventDefault();
        toggleItemSelection(item.id);
      });
    }

    container.appendChild(card);

    const cardId = item.id;
    if (dealtCardIds.has(cardId)) {
      card.style.transition = 'none';
      card.classList.add('dealt');
      requestAnimationFrame(() => { card.style.transition = ''; });
    } else {
      setTimeout(() => {
        card.classList.add('dealt');
        dealtCardIds.add(cardId);
      }, Math.min(idx * 30, 450));
    }
  });
}

// ── Fullscreen modal ──────────────────────────────────────────────────────────
function hasNextImage() {
  let next = modalIndex + 1;
  while (next < galleryItems.length && galleryItems[next].status !== 'completed') next++;
  return next < galleryItems.length;
}

function hasPrevImage() {
  let prev = modalIndex - 1;
  while (prev >= 0 && galleryItems[prev].status !== 'completed') prev--;
  return prev >= 0;
}

let isTransitioning = false;

function openImageModal(idx) {
  if (galleryItems[idx] && galleryItems[idx].status !== 'completed') return;

  viewImage.className = '';
  viewImage.style.transform = '';
  viewImage.style.opacity = '0';
  viewImage.style.transition = 'none';

  modalIndex = idx;
  document.body.style.overflow = 'hidden';
  imageModal.classList.add('open');

  // Fullscreen mode disabled to prevent native browser fullscreen help overlays

  const item = galleryItems[idx];
  const preloadImg = new Image();
  preloadImg.src = `/api/image-data/${item.id}?v=${item.prompt_id || '0'}`;

  const showImage = () => {
    if (!imageModal.classList.contains('open')) return;
    updateModalContent();
    viewImage.style.transition = 'opacity 0.25s ease-out';
    viewImage.style.opacity = '1';
  };

  if (preloadImg.complete) {
    showImage();
  } else {
    preloadImg.onload = showImage;
    preloadImg.onerror = showImage;
  }
  toggleDrawer(false);
}

function closeImageModal(isSwipe = false) {
  imageModal.classList.remove('open');
  document.body.style.overflow = '';
  toggleDrawer(false);

  viewImage.style.transition = 'transform 0.35s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.35s ease';

  if (isSwipe) {
    viewImage.style.transform = 'translateY(100vh) scale(0.9)';
  } else {
    viewImage.style.transform = 'scale(0.96)';
  }
  viewImage.style.opacity = '0';

  setTimeout(() => {
    if (!imageModal.classList.contains('open')) {
      viewImage.className = '';
      viewImage.style.transform = '';
      viewImage.style.opacity = '0';
      viewImage.style.transition = 'none';
      viewImage.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
    }
  }, 350);

  // Exit fullscreen disabled
}

function copyTextToClipboard(text, successMessage) {
  if (!text || text === '—') return;
  
  function showSuccess() {
    if (typeof showToast === 'function') {
      showToast(successMessage || 'Copied to clipboard!');
    } else {
      alert(successMessage || 'Copied to clipboard!');
    }
  }

  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text)
      .then(showSuccess)
      .catch(err => {
        console.warn('navigator.clipboard failed, using fallback:', err);
        fallbackCopy(text);
      });
  } else {
    fallbackCopy(text);
  }

  function fallbackCopy(str) {
    const el = document.createElement('textarea');
    el.value = str;
    el.setAttribute('readonly', '');
    el.style.position = 'fixed';
    el.style.top = '0';
    el.style.left = '0';
    el.style.opacity = '0';
    el.style.pointerEvents = 'none';
    document.body.appendChild(el);

    const selected = document.getSelection().rangeCount > 0 
      ? document.getSelection().getRangeAt(0) 
      : false;

    el.select();
    el.setSelectionRange(0, 99999);

    let success = false;
    try {
      success = document.execCommand('copy');
    } catch (err) {
      console.error('fallbackCopy execCommand failed:', err);
    }

    document.body.removeChild(el);

    if (selected) {
      document.getSelection().removeAllRanges();
      document.getSelection().addRange(selected);
    }

    if (success) {
      showSuccess();
    } else {
      console.error('Fallback copy was not successful');
    }
  }
}

function updateModalContent() {
  const item = galleryItems[modalIndex];
  if (!item || item.status !== 'completed') return;

  if (newImageIds.has(item.id)) {
    newImageIds.delete(item.id);
    saveNewImageIds();
    const card = document.querySelector(`.gallery-card[data-id="${item.id}"]`);
    if (card) {
      const badge = card.querySelector('.badge-new');
      if (badge) badge.remove();
    }
  }

  viewImage.src = `/api/image-data/${item.id}?v=${item.prompt_id || '0'}`;
  viewTemplate.textContent = `${item.db_name || '?'} · seg ${item.segment_index != null ? item.segment_index + 1 : (item.image_index || 0) + 1}`;
  modalCounter.textContent = `${modalIndex + 1} / ${galleryItems.filter(i => i.status === 'completed').length}`;
  currentEditId = item.id;

  viewPrompt.textContent = item.prompt_resolved || '—';
  fetch(`/api/item-template/${encodeURIComponent(item.id)}`)
    .then(r => r.json())
    .then(data => {
      if (data.prompt_original) {
        viewPrompt.textContent = data.prompt_original;
      }
    }).catch(() => {});

  viewPrompt.title = 'Click to copy';
  viewPrompt.onclick = () => {
    const txt = viewPrompt.textContent;
    copyTextToClipboard(txt, 'Prompt copied!');
  };

  const upBtn = document.getElementById('btnUpscaleCurrent');
  if (upBtn) {
    if (item.upscaled) {
      upBtn.style.opacity = '0.4';
      upBtn.style.cursor = 'not-allowed';
      upBtn.querySelector('span').textContent = 'Upscaled';
      upBtn.onclick = null;
    } else {
      upBtn.style.opacity = '1';
      upBtn.style.cursor = 'pointer';
      upBtn.querySelector('span').textContent = 'Upscale 4x';
      upBtn.onclick = upscaleCurrent;
    }
  }
}

function slideNext() {
  if (isTransitioning) return;
  let next = modalIndex + 1;
  while (next < galleryItems.length && galleryItems[next].status !== 'completed') next++;
  if (next < galleryItems.length) {
    isTransitioning = true;

    const nextItem = galleryItems[next];
    const preloadImg = new Image();
    preloadImg.src = `/api/image-data/${nextItem.id}?v=${nextItem.prompt_id || '0'}`;

    viewImage.style.transition = 'transform 0.15s ease-in, opacity 0.15s ease-in';
    viewImage.style.transform = 'translateX(-120px) scale(0.95)';
    viewImage.style.opacity = '0';

    setTimeout(() => {
      const runSlideIn = () => {
        if (!imageModal.classList.contains('open')) {
          isTransitioning = false;
          return;
        }

        viewImage.style.transition = 'none';
        viewImage.style.transform = 'translateX(120px) scale(0.95)';
        viewImage.style.opacity = '0';
        viewImage.offsetHeight;

        modalIndex = next;
        updateModalContent();

        viewImage.offsetHeight;

        viewImage.style.transition = 'transform 0.22s cubic-bezier(0, 0, 0.2, 1), opacity 0.22s ease-out';
        viewImage.style.transform = 'none';
        viewImage.style.opacity = '1';

        setTimeout(() => { isTransitioning = false; }, 230);
      };

      if (preloadImg.complete) {
        runSlideIn();
      } else {
        preloadImg.onload = runSlideIn;
        preloadImg.onerror = runSlideIn;
      }
    }, 150);
  }
}

function slidePrev() {
  if (isTransitioning) return;
  let prev = modalIndex - 1;
  while (prev >= 0 && galleryItems[prev].status !== 'completed') prev--;
  if (prev >= 0) {
    isTransitioning = true;

    const prevItem = galleryItems[prev];
    const preloadImg = new Image();
    preloadImg.src = `/api/image-data/${prevItem.id}?v=${prevItem.prompt_id || '0'}`;

    viewImage.style.transition = 'transform 0.15s ease-in, opacity 0.15s ease-in';
    viewImage.style.transform = 'translateX(120px) scale(0.95)';
    viewImage.style.opacity = '0';

    setTimeout(() => {
      const runSlideIn = () => {
        if (!imageModal.classList.contains('open')) {
          isTransitioning = false;
          return;
        }

        viewImage.style.transition = 'none';
        viewImage.style.transform = 'translateX(-120px) scale(0.95)';
        viewImage.style.opacity = '0';
        viewImage.offsetHeight;

        modalIndex = prev;
        updateModalContent();

        viewImage.offsetHeight;

        viewImage.style.transition = 'transform 0.22s cubic-bezier(0, 0, 0.2, 1), opacity 0.22s ease-out';
        viewImage.style.transform = 'none';
        viewImage.style.opacity = '1';

        setTimeout(() => { isTransitioning = false; }, 230);
      };

      if (preloadImg.complete) {
        runSlideIn();
      } else {
        preloadImg.onload = runSlideIn;
        preloadImg.onerror = runSlideIn;
      }
    }, 150);
  }
}

// Keyboard controls
document.addEventListener('keydown', (e) => {
  if (!imageModal || !imageModal.classList.contains('open')) return;
  const tag = e.target.tagName.toLowerCase();
  if (tag === 'input' || tag === 'textarea') return;
  if (e.key === 'ArrowRight') slideNext();
  if (e.key === 'ArrowLeft')  slidePrev();
  if (e.key === 'Escape')     closeImageModal();
});

// ── Swipe touch gestures with live finger tracking ──────────────────────────
let gestureLocked = null;
let drawerHeight = 0;

const modalContainer = document.getElementById('modalImageContainer');
if (modalContainer) {
  modalContainer.addEventListener('touchstart', (e) => {
    if (e.touches.length > 1) {
      isMultiTouch = true;
      return;
    }
    isMultiTouch = false;
    touchStartX = e.touches[0].clientX;
    touchStartY = e.touches[0].clientY;
    gestureLocked = null;

    const drawer = document.getElementById('detailsDrawer');
    drawerHeight = drawer ? drawer.offsetHeight : 300;

    if (drawer) drawer.style.transition = 'none';
    viewImage.style.transition = 'none';
  }, { passive: true });

  modalContainer.addEventListener('touchmove', (e) => {
    if (e.touches.length > 1) {
      isMultiTouch = true;
      return;
    }
    if (isMultiTouch) return;

    const dx = e.touches[0].clientX - touchStartX;
    const dy = e.touches[0].clientY - touchStartY;
    const drawer = document.getElementById('detailsDrawer');
    const startedInDrawer = e.target.closest('#detailsDrawer') !== null;

    if (gestureLocked === null) {
      if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 10) {
        gestureLocked = startedInDrawer ? 'prevented' : 'horizontal';
      } else if (Math.abs(dy) > Math.abs(dx) && Math.abs(dy) > 10) {
        gestureLocked = 'vertical';
      }
    }

    if (gestureLocked === 'horizontal') {
      if (isTransitioning) return;

      let targetDx = dx;
      if ((dx > 0 && !hasPrevImage()) || (dx < 0 && !hasNextImage())) {
        targetDx = dx * 0.35;
      }

      viewImage.style.transform = `translateX(${targetDx}px) scale(${1 - Math.abs(targetDx) / (window.innerWidth * 3.5)})`;
      viewImage.style.opacity = `${1 - Math.abs(targetDx) / 380}`;
    }
    else if (gestureLocked === 'vertical') {
      if (drawer) {
        if (isDrawerOpen) {
          const offset = Math.max(0, Math.min(drawerHeight, dy));
          drawer.style.transform = `translateY(${offset}px)`;
        } else {
          if (!startedInDrawer) {
            if (dy < 0) {
              const offset = Math.max(0, Math.min(drawerHeight, drawerHeight + dy));
              drawer.style.transform = `translateY(${offset}px)`;
              viewImage.style.transform = 'none';
              viewImage.style.opacity = '1';
            } else {
              viewImage.style.transform = `translateY(${dy}px) scale(${1 - dy / (window.innerHeight * 3.5)})`;
              viewImage.style.opacity = `${1 - dy / 400}`;
              drawer.style.transform = `translateY(${drawerHeight}px)`;
            }
          }
        }
      }
    }
  }, { passive: true });

  modalContainer.addEventListener('touchend', (e) => {
    if (isMultiTouch || e.touches.length > 0) {
      const drawer = document.getElementById('detailsDrawer');
      if (drawer) {
        drawer.style.transition = 'transform 0.3s cubic-bezier(0.16, 1, 0.3, 1)';
        drawer.style.transform = '';
      }
      viewImage.style.transition = 'transform 0.3s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.3s ease';
      viewImage.style.transform = 'none';
      viewImage.style.opacity = '1';
      gestureLocked = null;
      if (e.touches.length === 0) {
        isMultiTouch = false;
      } else {
        isMultiTouch = true;
      }
      return;
    }

    const dx = e.changedTouches[0].clientX - touchStartX;
    const dy = e.changedTouches[0].clientY - touchStartY;

    const drawer = document.getElementById('detailsDrawer');
    const startedInDrawer = e.target.closest('#detailsDrawer') !== null;

    if (drawer) drawer.style.transition = 'transform 0.3s cubic-bezier(0.16, 1, 0.3, 1)';
    viewImage.style.transition = 'transform 0.3s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.3s ease';

    if (gestureLocked === 'horizontal' && !isTransitioning) {
      if (dx < -80 && hasNextImage()) {
        slideNext();
      } else if (dx > 80 && hasPrevImage()) {
        slidePrev();
      } else {
        viewImage.style.transform = 'none';
        viewImage.style.opacity = '1';
      }
    }
    else if (gestureLocked === 'vertical') {
      if (isDrawerOpen) {
        if (dy > 80) {
          toggleDrawer(false);
        } else {
          toggleDrawer(true);
        }
      } else {
        if (dy < -80 && !startedInDrawer) {
          toggleDrawer(true);
        } else if (dy > 80 && !startedInDrawer) {
          closeImageModal(true);
        } else {
          toggleDrawer(false);
          viewImage.style.transform = 'none';
          viewImage.style.opacity = '1';
        }
      }
    }
    else {
      if (dy > 80 && !startedInDrawer) {
        if (isDrawerOpen) {
          toggleDrawer(false);
        } else {
          closeImageModal(true);
        }
      } else if (dy < -80 && !startedInDrawer && !isDrawerOpen) {
        toggleDrawer(true);
      }
    }

    gestureLocked = null;
  }, { passive: true });
}

// ── Batch Config Modal ───────────────────────────────────────────────────────
let enumData = null;

async function loadDatabases(dbType = 'prompts') {
  try {
    const res = await fetch(`/api/databases?type=${encodeURIComponent(dbType)}`);
    return await res.json();
  } catch(e) { return []; }
}

async function populateDbSelect(selectId, dbType = 'prompts') {
  const dbs = await loadDatabases(dbType);
  const sel = document.getElementById(selectId);
  if (!sel) return;
  sel.innerHTML = '';
  dbs.forEach(db => {
    sel.innerHTML += `<option value="${db.name}" ${db.active ? 'selected' : ''}>${db.name} (${db.segments} segs)</option>`;
  });
}

async function switchDbType() {
  const dbType = document.getElementById('dbTypeSelect')?.value || 'prompts';
  document.getElementById('promptsEditor').style.display = 'none';
  document.getElementById('promptsLoading').style.display = 'flex';
  await populateDbSelect('promptsDbSelect', dbType);
  await loadCurrentDbSegments();
}

async function switchPromptsDb() {
  const dbType = document.getElementById('dbTypeSelect')?.value || 'prompts';
  const dbName = document.getElementById('promptsDbSelect').value;
  await fetch('/api/databases/active', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: dbName, type: dbType })
  });
  await switchDbType();
}

async function createNewDb() {
  const dbType = document.getElementById('dbTypeSelect')?.value || 'prompts';
  const name = prompt('Database name:');
  if (!name || !name.trim()) return;
  const safeName = name.trim().replace(/[^a-zA-Z0-9_-]/g, '_');
  await fetch('/api/databases/active', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: safeName, type: dbType })
  });
  await switchDbType();
}

async function deleteCurrentDb() {
  const dbType = document.getElementById('dbTypeSelect')?.value || 'prompts';
  const sel = document.getElementById('promptsDbSelect');
  const name = sel ? sel.value : '';
  if (!name || name === 'default') {
    alert('Cannot delete the default database.');
    return;
  }
  if (!confirm('Permanently delete database "' + name + '"? This cannot be undone.')) return;
  await fetch('/api/databases/' + encodeURIComponent(name) + '?type=' + encodeURIComponent(dbType), { method: 'DELETE' });
  await switchDbType();
}


async function onBatchDbChange() {
  const dbName = document.getElementById('cfgDatabase').value;
  await fetch('/api/databases/active', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: dbName })
  });
  const res = await fetch('/api/templates');
  const templates = await res.json();
  document.getElementById('batchTemplateCount').textContent = `${templates.count || templates.length} segments (${templates.db_name || '?'})`;
}

async function openBatchModal() {
  if (!batchModal) return;
  batchModal.style.display = 'flex';
  document.getElementById('batchForm').style.display = 'none';
  document.getElementById('batchLoading').style.display = 'flex';

  if (!enumData) {
    try {
      const [enumRes, tmplRes] = await Promise.all([
        fetch('/api/enums'),
        fetch('/api/templates')
      ]);
      enumData = await enumRes.json();
      const templates = await tmplRes.json();
      document.getElementById('batchTemplateCount').textContent = `${templates.count || templates.length} segments (${templates.db_name || '?'})`;
    } catch(e) {
      document.getElementById('batchLoading').innerHTML = '<p style="color:#ef4444;">Failed to load options</p>';
      return;
    }
  }

  await populateDbSelect('cfgDatabase');
  populateSelect('cfgStyle', enumData.style);
  populateSelect('cfgSubject', enumData.subject);
  populateSelect('cfgAppearance', enumData.appearance);
  populateSelect('cfgWardrobe', enumData.wardrobe);
  populateSelect('cfgPose', enumData.pose);
  populateSelect('cfgScene', enumData.scene);

  document.getElementById('batchLoading').style.display = 'none';
  document.getElementById('batchForm').style.display = 'flex';
}

function closeBatchModal() {
  if (batchModal) batchModal.style.display = 'none';
}

function populateSelect(id, options) {
  const el = document.getElementById(id);
  if (!el) return;
  let html = '';
  for (const opt of options) {
    const label = opt.name === 'NONE' ? '(none)' : opt.value || opt.name;
    html += `<option value="${opt.name}">${label}</option>`;
  }
  el.innerHTML = html;
}

function setSelectValueSafely(id, val) {
  const el = document.getElementById(id);
  if (!el) return;
  let found = false;
  for (let i = 0; i < el.options.length; i++) {
    if (el.options[i].value === val) {
      el.selectedIndex = i;
      found = true;
      break;
    }
  }
  if (!found && el.options.length > 0) {
    el.selectedIndex = 0;
  }
}

async function submitBatch() {
  const btn = document.getElementById('btnSubmitBatch');
  const originalText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span style="display:inline-flex;align-items:center;gap:8px;"><div class="spinner" style="width:16px;height:16px;"></div> Queueing...</span>';

  try {
    const res = await fetch('/api/generate-batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        style: document.getElementById('cfgStyle').value,
        subject: document.getElementById('cfgSubject').value,
        appearance: document.getElementById('cfgAppearance').value,
        wardrobe: document.getElementById('cfgWardrobe').value,
        pose: document.getElementById('cfgPose').value,
        scene: document.getElementById('cfgScene').value,
      })
    });
    if (!res.ok) {
      const err = await res.json();
      alert('Error: ' + (err.detail || 'Failed to generate batch'));
      return;
    }
    const result = await res.json();
    lastSessionId = result.session_id;
    closeBatchModal();
    currentView = 'sessionDetail';
    currentSessionId = result.session_id;
    loadedSessionId = result.session_id;
    currentSessionData = { config: result.items[0]?.config || {}, total: result.count, completed: 0, pending: result.count, failed: 0 };
    await loadSessionItems();
  } catch(e) {
    alert('Connection error.');
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalText;
  }
}

// ── Edit Prompt Modal ────────────────────────────────────────────────────────
function toggleEditConfigPanel(event) {
  if (event) event.stopPropagation();
  const panel = document.getElementById('editConfigPanel');
  if (!panel) return;
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
  } else {
    panel.style.display = 'none';
  }
}

async function openEditModal() {
  const item = galleryItems[modalIndex];
  if (!item) return;
  currentEditId = item.id;
  const imgIdx = item.image_index || 0;

  document.getElementById('editPromptText').value = 'Loading template...';
  const histContainer = document.getElementById('editPromptHistoryContainer');
  if (histContainer) histContainer.innerHTML = '';
  document.getElementById('editSegmentInfo').textContent = `(db: ${item.db_name || '?'} · seg ${item.segment_index != null ? item.segment_index + 1 : imgIdx + 1})`;
  
  const configPanel = document.getElementById('editConfigPanel');
  if (configPanel) configPanel.style.display = 'none';
  
  if (editModal) editModal.style.display = 'flex';

  if (!enumData) {
    try {
      const res = await fetch('/api/enums');
      enumData = await res.json();
    } catch(e) {}
  }

  if (enumData) {
    populateSelect('editCfgStyle', enumData.style || []);
    populateSelect('editCfgSubject', enumData.subject || []);
    populateSelect('editCfgAppearance', enumData.appearance || []);
    populateSelect('editCfgWardrobe', enumData.wardrobe || []);
    populateSelect('editCfgPose', enumData.pose || []);
    populateSelect('editCfgScene', enumData.scene || []);
  }

  let singlePrompt = '';
  let history = [];
  let itemConfig = item.config || {};
  try {
    const res = await fetch(`/api/item-template/${encodeURIComponent(item.id)}`);
    if (res.ok) {
      const data = await res.json();
      singlePrompt = data.prompt_original || '';
      history = data.history || [];
      if (data.config) {
        itemConfig = data.config;
      }
    }
  } catch(e) {}

  if (enumData) {
    setSelectValueSafely('editCfgStyle', itemConfig.style);
    setSelectValueSafely('editCfgSubject', itemConfig.subject);
    setSelectValueSafely('editCfgAppearance', itemConfig.appearance);
    setSelectValueSafely('editCfgWardrobe', itemConfig.wardrobe);
    setSelectValueSafely('editCfgPose', itemConfig.pose);
    setSelectValueSafely('editCfgScene', itemConfig.scene);
  }

  originalEditConfig = {
    style: itemConfig.style || '',
    subject: itemConfig.subject || '',
    appearance: itemConfig.appearance || '',
    wardrobe: itemConfig.wardrobe || '',
    pose: itemConfig.pose || '',
    scene: itemConfig.scene || ''
  };

  try {
    const tagsRes = await fetch('/api/autocomplete-tags');
    if (tagsRes.ok) {
      editModalDbTags = await tagsRes.json();
    }
  } catch(e) {}

  if (!singlePrompt) {
    const fullText = item.prompt_resolved || '';
    const segments = fullText.split(/\n\s*-{3,}\s*\n/).map(s => s.trim()).filter(s => s);
    singlePrompt = segments[imgIdx] || fullText;
  }

  document.getElementById('editPromptText').value = singlePrompt;
  originalEditPromptText = singlePrompt;
  currentEditHistory = history;
  document.getElementById('editSegmentInfo').textContent =
    `(db: ${item.db_name || '?'} · seg ${item.segment_index != null ? item.segment_index + 1 : imgIdx + 1})`;

  if (histContainer && history.length > 0) {
    histContainer.innerHTML = `
      <details style="font-size:10px;color:#6b7280;cursor:pointer;position:relative;text-align:right;">
        <summary style="outline:none;user-select:none;font-weight:600;color:#3b82f6;">${history.length} versions</summary>
        <div style="position:absolute;right:0;top:16px;background:#1f2937;border:1px solid #374151;border-radius:8px;padding:6px;z-index:10050;min-width:200px;max-width:300px;max-height:220px;overflow-y:auto;box-shadow:0 10px 15px -3px rgba(0,0,0,0.5);" class="no-scrollbar">
          ${history.map((h, hi) => `
            <div style="margin-bottom:6px;padding:6px;background:#111827;border-radius:4px;text-align:left;cursor:default;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                <span style="font-weight:700;color:#fff;font-size:9px;">v${hi+1}</span>
                <span style="font-size:8px;color:#6b7280;">${(h.updated_at||'').slice(0,16)}</span>
              </div>
              <pre style="white-space:pre-wrap;font-size:9px;color:#9ca3af;max-height:80px;overflow-y:auto;font-family:monospace;line-height:1.3;margin-bottom:4px;" class="no-scrollbar">${h.text}</pre>
              <button onclick="revertEditPromptToHistory(${hi})" 
                style="width:100%;background:#2563eb;color:#fff;border:none;border-radius:4px;padding:3px 0;font-size:9px;font-weight:600;cursor:pointer;transition:background 0.15s;"
                onmouseover="this.style.background='#1d4ed8'"
                onmouseout="this.style.background='#2563eb'">
                Restore Version
              </button>
            </div>
          `).join('')}
        </div>
      </details>
    `;
  }
}

function closeEditModal() {
  if (editModal) editModal.style.display = 'none';
  const menu = document.getElementById('editActionsMenu');
  if (menu) menu.style.display = 'none';
  const configPanel = document.getElementById('editConfigPanel');
  if (configPanel) configPanel.style.display = 'none';
  const instructionInput = document.getElementById('aiInstructionInput');
  const diffSection = document.getElementById('aiDiffSection');
  const origPreview = document.getElementById('aiOriginalPromptPreview');
  const newPreview = document.getElementById('aiNewPromptPreview');
  if (instructionInput) instructionInput.value = '';
  if (diffSection) diffSection.style.display = 'none';
  if (origPreview) origPreview.textContent = '';
  if (newPreview) newPreview.innerHTML = '';
  lastAISuggestion = null;
  lastAIInstruction = null;
  originalEditPromptText = '';
  originalEditConfig = {};
  currentEditHistory = [];
  editModalDbTags = [];
  const autoContainer = document.getElementById('autocompleteSuggestions');
  if (autoContainer) {
    autoContainer.style.display = 'none';
    autoContainer.innerHTML = '';
  }
  const histContainer = document.getElementById('editPromptHistoryContainer');
  if (histContainer) histContainer.innerHTML = '';
  if (instructionInput) {
    instructionInput.placeholder = "What to change? (e.g. 'crying, (blushing:1.1)')";
  }
}

function revertEditPromptToHistory(hi) {
  const h = currentEditHistory[hi];
  if (!h || !h.text) return;
  const mainText = document.getElementById('editPromptText');
  if (mainText) {
    mainText.value = h.text;
    mainText.dispatchEvent(new Event('input', { bubbles: true }));
    mainText.dispatchEvent(new Event('change', { bubbles: true }));
    showToast('Restored version v' + (hi + 1));
  }
  const details = document.querySelector('#editPromptHistoryContainer details');
  if (details) details.removeAttribute('open');
}

function adjustTagWeightAtCursor(delta) {
  const ta = document.getElementById('editPromptText');
  if (!ta) return;

  const text = ta.value;
  const cursor = ta.selectionStart;
  if (cursor === null) return;

  // Search backward for comma or start of string
  let start = cursor;
  while (start > 0 && text[start - 1] !== ',') {
    start--;
  }

  // Search forward for comma or end of string
  let end = cursor;
  while (end < text.length && text[end] !== ',') {
    end++;
  }

  const tag = text.slice(start, end);
  const leadingSpaces = tag.match(/^\s*/)[0];
  const trailingSpaces = tag.match(/\s*$/)[0];
  const tagTrimmed = tag.trim();

  if (!tagTrimmed) return;

  let word = tagTrimmed;
  let weight = 1.0;

  // Try to match Format A: (something:weight)
  const matchA = tagTrimmed.match(/^\(([^:]+):([0-9.]+)\)$/);
  if (matchA) {
    word = matchA[1];
    weight = parseFloat(matchA[2]);
  } else {
    // Try to match Format B: (something)
    const matchB = tagTrimmed.match(/^\(([^:]+)\)$/);
    if (matchB) {
      word = matchB[1];
      weight = 1.1;
    }
  }

  let newWeight = parseFloat((weight + delta).toFixed(2));
  if (newWeight < 0.1) newWeight = 0.1; // lower bound

  let newTagTrimmed = '';
  if (newWeight === 1.0) {
    newTagTrimmed = word;
  } else {
    newTagTrimmed = `(${word}:${newWeight})`;
  }

  const newTag = leadingSpaces + newTagTrimmed + trailingSpaces;
  const before = text.slice(0, start);
  const after = text.slice(end);

  ta.value = before + newTag + after;

  // Trigger text events
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  ta.dispatchEvent(new Event('change', { bubbles: true }));

  // Restore cursor selection to the end of the trimmed tag
  const newCursorPos = start + leadingSpaces.length + newTagTrimmed.length;
  ta.selectionStart = ta.selectionEnd = newCursorPos;
  ta.focus();
}

function highlightNewPrompt(original, newText) {
  const clean = (s) => s.replace(/[\[\]]/g, '').trim().toLowerCase();
  const origTagsClean = original.split(',').map(clean).filter(Boolean);
  const newTags = newText.split(',');
  const highlightedTags = newTags.map(tag => {
    const tagClean = clean(tag);
    if (tagClean && !origTagsClean.includes(tagClean)) {
      return `<span style="background:rgba(16,185,129,0.25);color:#a7f3d0;border:1px solid rgba(16,185,129,0.3);border-radius:4px;padding:1px 3px;" title="Modified/Added by AI">${tag}</span>`;
    }
    return tag;
  });
  return highlightedTags.join(',');
}

async function askAIPrompt() {
  const originalPrompt = document.getElementById('editPromptText').value.trim();
  const instruction = document.getElementById('aiInstructionInput').value.trim();
  if (!originalPrompt) {
    alert('Please enter a prompt first.');
    return;
  }
  if (!instruction) {
    alert('Please enter an AI instruction (what you want to change).');
    return;
  }

  const btn = document.getElementById('btnAskAI');
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Generating...';

  const requestAppearance = {
    original_prompt: originalPrompt,
    instruction: instruction
  };
  if (lastAISuggestion && lastAIInstruction) {
    requestAppearance.ai_suggestion = lastAISuggestion;
    requestAppearance.previous_instruction = lastAIInstruction;
  }

  try {
    const res = await fetch('/api/ai/preview-prompt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestAppearance)
    });
    if (!res.ok) {
      const err = await res.json();
      alert('AI Generation Error: ' + (err.detail || 'Failed to generate prompt.'));
      return;
    }
    const data = await res.json();
    document.getElementById('aiOriginalPromptPreview').textContent = data.original_prompt;
    document.getElementById('aiNewPromptPreview').innerHTML = highlightNewPrompt(data.original_prompt, data.new_prompt);
    document.getElementById('aiDiffSection').style.display = 'flex';
    
    lastAISuggestion = data.new_prompt;
    lastAIInstruction = instruction;

    const instructionInput = document.getElementById('aiInstructionInput');
    if (instructionInput) {
      instructionInput.value = '';
      instructionInput.placeholder = "Adjust this suggestion? (e.g. 'less blush', 'more crying')";
    }
  } catch(e) {
    alert('Connection error while talking to AI API.');
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

function rejectAISuggestion() {
  const diffSection = document.getElementById('aiDiffSection');
  const origPreview = document.getElementById('aiOriginalPromptPreview');
  const newPreview = document.getElementById('aiNewPromptPreview');
  if (diffSection) diffSection.style.display = 'none';
  if (origPreview) origPreview.textContent = '';
  if (newPreview) newPreview.innerHTML = '';
  lastAISuggestion = null;
  lastAIInstruction = null;
  const instructionInput = document.getElementById('aiInstructionInput');
  if (instructionInput) {
    instructionInput.placeholder = "What to change? (e.g. 'crying, (blushing:1.1)')";
  }
}

function acceptAISuggestion() {
  const newPreview = document.getElementById('aiNewPromptPreview');
  const mainText = document.getElementById('editPromptText');
  if (newPreview && mainText) {
    const newPrompt = newPreview.innerText.trim();
    if (newPrompt) {
      mainText.value = newPrompt;
      mainText.dispatchEvent(new Event('input', { bubbles: true }));
      mainText.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }
  rejectAISuggestion();
}

async function submitEdit() {
  const prompt = document.getElementById('editPromptText').value.trim();
  if (!prompt || !currentEditId) return;

  const currentCfg = {
    style: document.getElementById('editCfgStyle').value,
    subject: document.getElementById('editCfgSubject').value,
    appearance: document.getElementById('editCfgAppearance').value,
    wardrobe: document.getElementById('editCfgWardrobe').value,
    pose: document.getElementById('editCfgPose').value,
    scene: document.getElementById('editCfgScene').value,
  };

  const configChanged = 
    currentCfg.style !== originalEditConfig.style ||
    currentCfg.subject !== originalEditConfig.subject ||
    currentCfg.appearance !== originalEditConfig.appearance ||
    currentCfg.wardrobe !== originalEditConfig.wardrobe ||
    currentCfg.pose !== originalEditConfig.pose ||
    currentCfg.scene !== originalEditConfig.scene;

  if (prompt === originalEditPromptText && !configChanged) {
    const diffSection = document.getElementById('aiDiffSection');
    const hasSuggestion = diffSection && diffSection.style.display === 'flex';
    let msg = "O prompt e as configurações não foram alterados. Deseja realmente reenfileirar o mesmo prompt sem alterações?";
    if (hasSuggestion) {
      msg = "Você possui uma sugestão da IA pendente na tela, mas o prompt principal não foi atualizado (talvez você tenha clicado em 'Re-queue' antes de clicar em 'Use Suggestion').\n\nDeseja realmente reenfileirar o prompt original sem alterações?";
    }
    const confirmSame = confirm(msg);
    if (!confirmSame) return;
  }

  const btn = document.getElementById('btnSubmitEdit');
  const originalText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span style="display:inline-flex;align-items:center;gap:8px;"><div class="spinner" style="width:16px;height:16px;"></div> Re-queueing...</span>';

  try {
    const res = await fetch('/api/edit-prompt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_id: currentEditId, prompt, config: currentCfg })
    });
    if (!res.ok) {
      const err = await res.json();
      alert('Error: ' + (err.detail || 'Failed to re-queue'));
      return;
    }
    closeEditModal();
    closeImageModal();
    await loadSessionItems();
  } catch(e) {
    alert('Connection error.');
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalText;
  }
}

// ── Upscale actions ──────────────────────────────────────────────────────────
async function upscaleCurrent() {
  const item = galleryItems[modalIndex];
  if (!item || item.upscaled) return;

  const btn = document.getElementById('btnUpscaleCurrent');
  const origHTML = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.style.opacity = '0.7';
    btn.innerHTML = '<span style="display:inline-flex;align-items:center;gap:6px;"><div class="spinner" style="width:14px;height:14px;"></div> Upscaling...</span>';
  }

  try {
    const res = await fetch(`/api/upscale-item?item_id=${encodeURIComponent(item.id)}`, { method: 'POST' });
    if (!res.ok) {
      const err = await res.json();
      alert('Error: ' + (err.detail || 'Failed to upscale'));
      return;
    }
    closeImageModal();
    await loadSessionItems();
  } catch(e) {
    alert('Connection error.');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.innerHTML = origHTML;
    }
  }
}

async function insertSegmentFromEdit(position, useAI = false, instruction = null, count = 1) {
  if (!currentEditId) return;

  let btnId = position === 'before' ? 'btnInsertBeforeEdit' : 'btnInsertAfterEdit';
  if (useAI) {
    btnId = position === 'before' ? 'btnInsertBeforeAI' : 'btnInsertAfterAI';
  }
  const btn = document.getElementById(btnId);
  const originalHTML = btn ? btn.innerHTML : '';
  const submitBtn = document.getElementById('btnSubmitEdit');
  const submitOrigHTML = submitBtn ? submitBtn.innerHTML : '';

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span style="display:inline-flex;align-items:center;gap:6px;"><div class="spinner" style="width:12px;height:12px;border: 2px solid #fff;border-top: 2px solid transparent;"></div> Inserting...</span>`;
  }
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = `<span style="display:inline-flex;align-items:center;gap:6px;"><div class="spinner" style="width:12px;height:12px;border: 2px solid #fff;border-top: 2px solid transparent;"></div> Inserting...</span>`;
  }

  try {
    const payload = { item_id: currentEditId, position, count };
    if (useAI) {
      payload.use_ai = true;
      if (instruction) {
        payload.instruction = instruction;
      }
    }
    const res = await fetch('/api/insert-segment-job', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const err = await res.json();
      alert('Error: ' + (err.detail || 'Failed to insert segment'));
      return;
    }
    closeEditModal();
    closeImageModal();
    showToast('New segment(s) inserted & queued!');
    await loadSessionItems();
  } catch(e) {
    alert('Connection error.');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalHTML;
    }
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.innerHTML = submitOrigHTML;
    }
  }
}

function toggleEditActionsMenu(event) {
  if (event) event.stopPropagation();
  const menu = document.getElementById('editActionsMenu');
  if (menu) {
    if (menu.style.display === 'none' || menu.style.display === '') {
      menu.style.display = 'block';
    } else {
      menu.style.display = 'none';
    }
  }
}

function handleInsertBeforeFromMenu(event) {
  if (event) event.stopPropagation();
  const menu = document.getElementById('editActionsMenu');
  if (menu) menu.style.display = 'none';
  insertSegmentFromEdit('before');
}

function handleInsertAfterFromMenu(event) {
  if (event) event.stopPropagation();
  const menu = document.getElementById('editActionsMenu');
  if (menu) menu.style.display = 'none';
  insertSegmentFromEdit('after');
}

function handleInsertBeforeAI(event) {
  if (event) event.stopPropagation();
  const menu = document.getElementById('editActionsMenu');
  if (menu) menu.style.display = 'none';

  const countStr = prompt("Quantos quadros novos deseja inserir com a IA?", "1");
  if (countStr === null) return;
  const count = parseInt(countStr, 10);
  if (isNaN(count) || count < 1) {
    alert("Quantidade inválida.");
    return;
  }

  const instruction = prompt("Descreva o que acontece nessa nova sequência (deixe em branco para transição automática da IA):");
  if (instruction === null) return; // User cancelled
  insertSegmentFromEdit('before', true, instruction, count);
}

function handleInsertAfterAI(event) {
  if (event) event.stopPropagation();
  const menu = document.getElementById('editActionsMenu');
  if (menu) menu.style.display = 'none';

  const countStr = prompt("Quantos quadros novos deseja inserir com a IA?", "1");
  if (countStr === null) return;
  const count = parseInt(countStr, 10);
  if (isNaN(count) || count < 1) {
    alert("Quantidade inválida.");
    return;
  }

  const instruction = prompt("Descreva o que acontece nessa nova sequência (deixe em branco para transição automática da IA):");
  if (instruction === null) return; // User cancelled
  insertSegmentFromEdit('after', true, instruction, count);
}

async function handleDeleteSegmentFromMenu(event) {
  if (event) event.stopPropagation();
  const menu = document.getElementById('editActionsMenu');
  if (menu) menu.style.display = 'none';

  // Double confirmation
  const confirm1 = confirm("Tem certeza de que deseja apagar este segmento do banco de dados?\nEsta ação removerá o segmento e reordenará os índices subsequentes.");
  if (!confirm1) return;

  const confirm2 = confirm("CONFIRMAÇÃO ADICIONAL: Deseja realmente excluir este segmento? Isso apagará o prompt do banco de dados e os arquivos de imagem correspondentes.");
  if (!confirm2) return;

  await deleteSegmentFromEdit();
}

async function deleteSegmentFromEdit() {
  if (!currentEditId) return;

  const btn = document.getElementById('btnSubmitEdit');
  const origHTML = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span style="display:inline-flex;align-items:center;gap:6px;"><div class="spinner" style="width:12px;height:12px;border: 2px solid #fff;border-top: 2px solid transparent;"></div> Deleting...</span>`;
  }

  try {
    const res = await fetch('/api/delete-segment-job', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_id: currentEditId })
    });
    if (!res.ok) {
      const err = await res.json();
      alert('Error: ' + (err.detail || 'Failed to delete segment'));
      return;
    }
    closeEditModal();
    closeImageModal();
    showToast('Segment deleted successfully!');
    await loadSessionItems();
  } catch(e) {
    alert('Connection error.');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHTML;
    }
  }
}

// ── Bulk Selection ───────────────────────────────────────────────────────────
function toggleSelectionMode() {
  if (isSelectionMode) {
    disableSelectionMode();
  } else {
    isSelectionMode = true;
    selectedIds.clear();
    const btn = document.getElementById('btnToggleSelection');
    if (btn) {
      btn.classList.remove('bg-gray-850', 'text-gray-300');
      btn.classList.add('bg-blue-600', 'text-white');
    }

    const bar = document.getElementById('selectionActionBar');
    if (bar) {
      bar.style.display = 'flex';
      bar.classList.remove('translate-y-20');
      bar.classList.add('translate-y-0');
    }
    updateSelectionCountText();
    renderGallery();
  }
}

function disableSelectionMode() {
  isSelectionMode = false;
  selectedIds.clear();
  const btn = document.getElementById('btnToggleSelection');
  if (btn) {
    btn.classList.remove('bg-blue-600', 'text-white');
    btn.classList.add('bg-gray-850', 'text-gray-300');
  }

  const bar = document.getElementById('selectionActionBar');
  if (bar) {
    bar.classList.remove('translate-y-0');
    bar.classList.add('translate-y-20');
    setTimeout(() => {
      if (!isSelectionMode) bar.style.display = 'none';
    }, 300);
  }
  renderGallery();
}

function toggleItemSelection(id) {
  if (selectedIds.has(id)) selectedIds.delete(id);
  else selectedIds.add(id);
  updateSelectionCountText();
  if (currentView === 'sessions') renderSessions();
  else renderGallery();
}

function updateSelectionCountText() {
  const el = document.getElementById('txtSelectionCount');
  if (el) el.textContent = `${selectedIds.size} selected`;
}

async function deleteSingleItem(id, event) {
  if (event) {
    event.stopPropagation();
    event.preventDefault();
  }
  if (!confirm('Delete this image?')) return;
  try {
    const res = await fetch('/api/items/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: [id] })
    });
    if (res.ok) {
      if (currentView === 'sessionDetail') {
        await loadSessionItems();
        if (galleryItems.length === 0) backToSessions();
      } else {
        galleryItems = galleryItems.filter(item => item.id !== id);
        renderGallery();
      }
    }
  } catch(e) {}
}

async function upscaleSelected() {
  if (selectedIds.size === 0) return;
  if (!confirm(`Upscale ${selectedIds.size} selected image(s)?`)) return;

  const ids = Array.from(selectedIds);
  disableSelectionMode();

  for (const itemId of ids) {
    try {
      await fetch(`/api/upscale-item?item_id=${encodeURIComponent(itemId)}`, { method: 'POST' });
    } catch(e) {}
  }
  await loadSessionItems();
}

async function deleteSelected() {
  if (selectedIds.size === 0) return;
  if (!confirm(`Permanently delete/cancel ${selectedIds.size} selected item(s)?`)) return;

  let deleteIds = Array.from(selectedIds);

  if (currentView === 'sessions') {
    try {
      for (const sessionId of deleteIds) {
        await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/delete`, {
          method: 'POST'
        });
      }
      disableSelectionMode();
      await loadSessions();
    } catch(e) {
      console.error(e);
    }
    return;
  }

  try {
    const res = await fetch('/api/items/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: deleteIds, purge: false })
    });
    if (res.ok) {
      disableSelectionMode();
      if (currentView === 'sessionDetail') {
        await loadSessionItems();
        if (galleryItems.length === 0) backToSessions();
      } else {
        await loadSessions();
      }
    }
  } catch(e) {}
}

async function retryAllFailed(sessionId, event) {
  if (event) event.stopPropagation();
  const btn = event ? event.currentTarget : null;
  const originalText = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = 'Running...';
  }
  try {
    const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/retry-failed`, {
      method: 'POST'
    });
    if (!res.ok) {
      const err = await res.json();
      alert('Error retrying failed items: ' + (err.detail || 'Failed'));
      return;
    }
    showToast('Retrying all failed items in session...');
    await loadState();
  } catch(e) {
    alert('Connection error.');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalText;
    }
  }
}

// ── Settings Modal ───────────────────────────────────────────────────────────
let generationCatalog = { checkpoints: [], loras: [], samplers: [], schedulers: [], recommended_preset: null };

function populateSelectOptions(selectId, options, currentValue) {
  const select = document.getElementById(selectId);
  if (!select) return;
  select.innerHTML = '';
  [...new Set(options || [])].forEach(value => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  });
  if (currentValue && ![...select.options].some(option => option.value === currentValue)) {
    const option = document.createElement('option');
    option.value = currentValue;
    option.textContent = `${currentValue} (configured)`;
    select.appendChild(option);
  }
  if (currentValue) select.value = currentValue;
}

function applyRecommendedGenerationPreset() {
  const preset = generationCatalog.recommended_preset;
  if (!preset) return;
  if (!generationCatalog.checkpoints.includes(preset.checkpoint)) {
    alert(`Recommended checkpoint is not installed: ${preset.checkpoint}`);
    return;
  }
  populateSelectOptions('cfgSamplerName', generationCatalog.samplers, preset.sampler_name);
  populateSelectOptions('cfgScheduler', generationCatalog.schedulers, preset.scheduler);
  document.getElementById('cfgCheckpoint').value = preset.checkpoint;
  document.getElementById('cfgSteps').value = preset.steps;
  document.getElementById('cfgScale').value = preset.cfg_scale;
  document.getElementById('cfgDenoise').value = preset.denoise;
  document.getElementById('cfgHighresEnabled').checked = preset.highres_enabled;
  document.getElementById('cfgHighresScale').value = preset.highres_scale;
  document.getElementById('cfgHighresSteps').value = preset.highres_steps;
  document.getElementById('cfgHighresCfgScale').value = preset.highres_cfg_scale;
  document.getElementById('cfgHighresDenoise').value = preset.highres_denoise;
  if (preset.lora && generationCatalog.loras.includes(preset.lora)) {
    document.getElementById('cfgLora1Name').value = preset.lora;
    document.getElementById('cfgLora1StrengthModel').value = 1;
    document.getElementById('cfgLora1StrengthClip').value = 1;
  }
  const loraNote = generationCatalog.loras.includes(preset.lora) ? '' : ' (LoRA not installed)';
  showToast(`Fast Illustration preset applied${loraNote}.`);
}

function openGenerationSettings() {
  document.getElementById('settingsForm').style.display = 'none';
  document.getElementById('generationSettingsForm').style.display = 'flex';
}

function backToMainSettings() {
  document.getElementById('generationSettingsForm').style.display = 'none';
  document.getElementById('settingsForm').style.display = 'flex';
}

function handleResolutionPresetChange() {
  const val = document.getElementById('cfgResolutionPreset').value;
  if (val && val !== 'custom') {
    const [w, h] = val.split('x');
    document.getElementById('cfgWidth').value = parseInt(w);
    document.getElementById('cfgHeight').value = parseInt(h);
  }
}

async function openSettingsModal() {
  const modal = document.getElementById('settingsModal');
  if (modal) modal.style.display = 'flex';
  document.getElementById('settingsForm').style.display = 'none';
  document.getElementById('generationSettingsForm').style.display = 'none';
  document.getElementById('settingsLoading').style.display = 'flex';

  try {
    const [cfgRes, modelsRes] = await Promise.all([
      fetch('/api/config'),
      fetch('/api/comfy-models').catch(() => null)
    ]);
    
    const cfg = await cfgRes.json();
    let models = { checkpoints: [], loras: [], samplers: [], schedulers: [] };
    if (modelsRes && modelsRes.ok) {
      try {
        models = await modelsRes.json();
      } catch(e) {}
    }
    generationCatalog = models;

    // Populate main server settings
    document.getElementById('cfgComfyUrl').value = cfg.comfy_url || '';
    document.getElementById('cfgTargetNodeId').value = cfg.target_node_id || '';
    document.getElementById('cfgTargetInputKey').value = cfg.target_input_key || 'text';
    document.getElementById('cfgWidth').value = cfg.width || 768;
    document.getElementById('cfgHeight').value = cfg.height || 1024;
    document.getElementById('cfgComfyRoot').value = cfg.comfy_root || '';
    document.getElementById('cfgOpenRouterKey').value = '';
    document.getElementById('cfgOpenRouterClearKey').checked = false;
    document.getElementById('cfgOpenRouterModels').value = (cfg.openrouter_models || []).join('\n');
    const openRouterStatus = document.getElementById('cfgOpenRouterStatus');
    openRouterStatus.textContent = cfg.openrouter_configured ? 'Configured' : 'Not configured';
    openRouterStatus.style.color = cfg.openrouter_configured ? '#10b981' : '#6b7280';
    populateSelectOptions('cfgSamplerName', models.samplers, cfg.sampler_name || 'dpmpp_2m_sde_heun_gpu');
    populateSelectOptions('cfgScheduler', models.schedulers, cfg.scheduler || 'beta57');
    document.getElementById('cfgSteps').value = cfg.steps ?? 12;
    document.getElementById('cfgScale').value = cfg.cfg_scale ?? 1.0;
    document.getElementById('cfgDenoise').value = cfg.denoise ?? 1.0;
    document.getElementById('cfgHighresEnabled').checked = cfg.highres_enabled ?? true;
    document.getElementById('cfgHighresScale').value = cfg.highres_scale ?? 1.5;
    document.getElementById('cfgHighresSteps').value = cfg.highres_steps ?? 4;
    document.getElementById('cfgHighresCfgScale').value = cfg.highres_cfg_scale ?? 1.6;
    document.getElementById('cfgHighresDenoise').value = cfg.highres_denoise ?? 0.45;
    document.getElementById('cfgAdultContent').checked = cfg.adult_content ?? false;

    // Populate Checkpoints
    const ckptSelect = document.getElementById('cfgCheckpoint');
    if (ckptSelect) {
      ckptSelect.innerHTML = '<option value="">Select a checkpoint...</option>';
      (models.checkpoints || []).forEach(ckpt => {
        const opt = document.createElement('option');
        opt.value = ckpt;
        opt.textContent = ckpt;
        ckptSelect.appendChild(opt);
      });
      if (cfg.checkpoint && !models.checkpoints.includes(cfg.checkpoint)) {
        const opt = document.createElement('option');
        opt.value = cfg.checkpoint;
        opt.textContent = `${cfg.checkpoint} (configured)`;
        ckptSelect.appendChild(opt);
      }
      ckptSelect.value = cfg.checkpoint || '';
      if (!ckptSelect.value && models.checkpoints.length > 0) {
        ckptSelect.value = models.checkpoints[0];
      }
    }

    // Populate Resolution Preset
    const w = cfg.width || 768;
    const h = cfg.height || 1024;
    const presetSelect = document.getElementById('cfgResolutionPreset');
    if (presetSelect) {
      const matchVal = `${w}x${h}`;
      let matched = false;
      for (let i = 0; i < presetSelect.options.length; i++) {
        if (presetSelect.options[i].value === matchVal) {
          presetSelect.value = matchVal;
          matched = true;
          break;
        }
      }
      if (!matched) {
        presetSelect.value = 'custom';
      }
    }

    // Populate LoRAs
    const lora1Select = document.getElementById('cfgLora1Name');
    const lora2Select = document.getElementById('cfgLora2Name');
    const lora3Select = document.getElementById('cfgLora3Name');
    const lora4Select = document.getElementById('cfgLora4Name');
    if (lora1Select && lora2Select && lora3Select && lora4Select) {
      lora1Select.innerHTML = '<option value="">None (Disabled)</option>';
      lora2Select.innerHTML = '<option value="">None (Disabled)</option>';
      lora3Select.innerHTML = '<option value="">None (Disabled)</option>';
      lora4Select.innerHTML = '<option value="">None (Disabled)</option>';
      (models.loras || []).forEach(lora => {
        const opt1 = document.createElement('option');
        opt1.value = lora;
        opt1.textContent = lora;
        lora1Select.appendChild(opt1);

        const opt2 = document.createElement('option');
        opt2.value = lora;
        opt2.textContent = lora;
        lora2Select.appendChild(opt2);

        const opt3 = document.createElement('option');
        opt3.value = lora;
        opt3.textContent = lora;
        lora3Select.appendChild(opt3);

        const opt4 = document.createElement('option');
        opt4.value = lora;
        opt4.textContent = lora;
        lora4Select.appendChild(opt4);
      });

      const loraConfigs = cfg.loras || [];
      loraConfigs.forEach(loraConfig => {
        if (!loraConfig.name || models.loras.includes(loraConfig.name)) return;
        [lora1Select, lora2Select, lora3Select, lora4Select].forEach(select => {
          const opt = document.createElement('option');
          opt.value = loraConfig.name;
          opt.textContent = `${loraConfig.name} (configured)`;
          select.appendChild(opt);
        });
      });
      if (loraConfigs.length > 0) {
        lora1Select.value = loraConfigs[0].name || '';
        document.getElementById('cfgLora1StrengthModel').value = loraConfigs[0].strength_model !== undefined ? loraConfigs[0].strength_model : 1.0;
        document.getElementById('cfgLora1StrengthClip').value = loraConfigs[0].strength_clip !== undefined ? loraConfigs[0].strength_clip : 1.0;
      } else {
        lora1Select.value = '';
        document.getElementById('cfgLora1StrengthModel').value = 1.0;
        document.getElementById('cfgLora1StrengthClip').value = 1.0;
      }

      if (loraConfigs.length > 1) {
        lora2Select.value = loraConfigs[1].name || '';
        document.getElementById('cfgLora2StrengthModel').value = loraConfigs[1].strength_model !== undefined ? loraConfigs[1].strength_model : 1.0;
        document.getElementById('cfgLora2StrengthClip').value = loraConfigs[1].strength_clip !== undefined ? loraConfigs[1].strength_clip : 1.0;
      } else {
        lora2Select.value = '';
        document.getElementById('cfgLora2StrengthModel').value = 1.0;
        document.getElementById('cfgLora2StrengthClip').value = 1.0;
      }

      if (loraConfigs.length > 2) {
        lora3Select.value = loraConfigs[2].name || '';
        document.getElementById('cfgLora3StrengthModel').value = loraConfigs[2].strength_model !== undefined ? loraConfigs[2].strength_model : 1.0;
        document.getElementById('cfgLora3StrengthClip').value = loraConfigs[2].strength_clip !== undefined ? loraConfigs[2].strength_clip : 1.0;
      } else {
        lora3Select.value = '';
        document.getElementById('cfgLora3StrengthModel').value = 1.0;
        document.getElementById('cfgLora3StrengthClip').value = 1.0;
      }

      if (loraConfigs.length > 3) {
        lora4Select.value = loraConfigs[3].name || '';
        document.getElementById('cfgLora4StrengthModel').value = loraConfigs[3].strength_model !== undefined ? loraConfigs[3].strength_model : 1.0;
        document.getElementById('cfgLora4StrengthClip').value = loraConfigs[3].strength_clip !== undefined ? loraConfigs[3].strength_clip : 1.0;
      } else {
        lora4Select.value = '';
        document.getElementById('cfgLora4StrengthModel').value = 1.0;
        document.getElementById('cfgLora4StrengthClip').value = 1.0;
      }
    }
  } catch(e) {
    console.error('Error loading settings configuration:', e);
  }

  document.getElementById('settingsLoading').style.display = 'none';
  document.getElementById('settingsForm').style.display = 'flex';
}

function closeSettingsModal() {
  const modal = document.getElementById('settingsModal');
  if (modal) modal.style.display = 'none';
}

async function saveSettings() {
  const btn = document.getElementById('btnSaveSettings');
  const btnGen = document.getElementById('btnSaveSettingsGen');
  const orig = btn ? btn.innerHTML : '';
  const origGen = btnGen ? btnGen.innerHTML : '';
  if (btn) { btn.disabled = true; btn.innerHTML = 'Saving...'; }
  if (btnGen) { btnGen.disabled = true; btnGen.innerHTML = 'Saving...'; }

  try {
    const comfy_url = document.getElementById('cfgComfyUrl').value.trim();
    const target_node_id = document.getElementById('cfgTargetNodeId').value.trim();
    const target_input_key = document.getElementById('cfgTargetInputKey').value.trim();
    const width = parseInt(document.getElementById('cfgWidth').value) || 768;
    const height = parseInt(document.getElementById('cfgHeight').value) || 1024;
    const comfy_root = document.getElementById('cfgComfyRoot').value.trim();
    const checkpoint = document.getElementById('cfgCheckpoint') ? document.getElementById('cfgCheckpoint').value : '';
    const sampler_name = document.getElementById('cfgSamplerName').value;
    const scheduler = document.getElementById('cfgScheduler').value;
    const steps = Math.max(1, parseInt(document.getElementById('cfgSteps').value) || 12);
    const cfg_scale = Math.max(0, Number.parseFloat(document.getElementById('cfgScale').value) || 0);
    const denoise = Math.min(1, Math.max(0, Number.parseFloat(document.getElementById('cfgDenoise').value) || 0));
    const highres_enabled = document.getElementById('cfgHighresEnabled').checked;
    const highres_scale = Math.max(1, Number.parseFloat(document.getElementById('cfgHighresScale').value) || 1);
    const highres_steps = Math.max(1, parseInt(document.getElementById('cfgHighresSteps').value) || 4);
    const highres_cfg_scale = Math.max(0, Number.parseFloat(document.getElementById('cfgHighresCfgScale').value) || 0);
    const highres_denoise = Math.min(1, Math.max(0, Number.parseFloat(document.getElementById('cfgHighresDenoise').value) || 0));
    const adult_content = document.getElementById('cfgAdultContent').checked;
    const openrouter_api_key = document.getElementById('cfgOpenRouterKey').value.trim();
    const openrouter_clear_key = document.getElementById('cfgOpenRouterClearKey').checked;
    const openrouter_models = document.getElementById('cfgOpenRouterModels').value
      .split(/\r?\n|,/)
      .map(model => model.trim())
      .filter(Boolean);
    if (!checkpoint) {
      alert('Select a checkpoint model before saving.');
      return;
    }

    const loras = [];
    const lora1Name = document.getElementById('cfgLora1Name') ? document.getElementById('cfgLora1Name').value : '';
    if (lora1Name) {
      loras.push({
        name: lora1Name,
        strength_model: parseFloat(document.getElementById('cfgLora1StrengthModel').value) || 1.0,
        strength_clip: parseFloat(document.getElementById('cfgLora1StrengthClip').value) || 1.0
      });
    } else {
      loras.push({ name: '', strength_model: 0.0, strength_clip: 0.0 });
    }

    const lora2Name = document.getElementById('cfgLora2Name') ? document.getElementById('cfgLora2Name').value : '';
    if (lora2Name) {
      loras.push({
        name: lora2Name,
        strength_model: parseFloat(document.getElementById('cfgLora2StrengthModel').value) || 1.0,
        strength_clip: parseFloat(document.getElementById('cfgLora2StrengthClip').value) || 1.0
      });
    } else {
      loras.push({ name: '', strength_model: 0.0, strength_clip: 0.0 });
    }

    const lora3Name = document.getElementById('cfgLora3Name') ? document.getElementById('cfgLora3Name').value : '';
    if (lora3Name) {
      loras.push({
        name: lora3Name,
        strength_model: parseFloat(document.getElementById('cfgLora3StrengthModel').value) || 1.0,
        strength_clip: parseFloat(document.getElementById('cfgLora3StrengthClip').value) || 1.0
      });
    } else {
      loras.push({ name: '', strength_model: 0.0, strength_clip: 0.0 });
    }

    const lora4Name = document.getElementById('cfgLora4Name') ? document.getElementById('cfgLora4Name').value : '';
    if (lora4Name) {
      loras.push({
        name: lora4Name,
        strength_model: parseFloat(document.getElementById('cfgLora4StrengthModel').value) || 1.0,
        strength_clip: parseFloat(document.getElementById('cfgLora4StrengthClip').value) || 1.0
      });
    } else {
      loras.push({ name: '', strength_model: 0.0, strength_clip: 0.0 });
    }

    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        comfy_url,
        target_node_id,
        target_input_key,
        width,
        height,
        comfy_root,
        checkpoint,
        loras,
        chunk_size: 1,
        sampler_name,
        scheduler,
        steps,
        cfg_scale,
        denoise,
        highres_enabled,
        highres_scale,
        highres_steps,
        highres_cfg_scale,
        highres_denoise,
        adult_content,
        openrouter_api_key: openrouter_api_key || null,
        openrouter_clear_key,
        openrouter_models
      })
    });
    if (res.ok) {
      closeSettingsModal();
    } else {
      alert('Error saving settings.');
    }
  } catch(e) {
    alert('Connection error.');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = orig; }
    if (btnGen) { btnGen.disabled = false; btnGen.innerHTML = origGen; }
  }
}

// ── Prompt Editor Modal ──────────────────────────────────────────────────────
let allSegments = [];

async function openPromptsModal() {
  const modal = document.getElementById('promptsModal');
  if (modal) modal.style.display = 'flex';
  document.getElementById('promptsEditor').style.display = 'none';
  document.getElementById('promptsLoading').style.display = 'flex';
  
  const typeSel = document.getElementById('dbTypeSelect');
  if (typeSel) typeSel.value = 'prompts';

  await populateDbSelect('promptsDbSelect', 'prompts');
  await loadCurrentDbSegments();
}

async function loadCurrentDbSegments() {
  const dbType = document.getElementById('dbTypeSelect')?.value || 'prompts';
  try {
    const res = await fetch(`/api/prompts?type=${encodeURIComponent(dbType)}`);
    const data = await res.json();
    allSegments = (data.segments || []).map(s => ({ ...s, _savedText: s.text }));
    renderSegmentList();
  } catch(e) {}

  document.getElementById('promptsLoading').style.display = 'none';
  document.getElementById('promptsEditor').style.display = 'flex';
}

function closePromptsModal() {
  const modal = document.getElementById('promptsModal');
  if (modal) modal.style.display = 'none';
  allSegments = [];
}

function renderSegmentList() {
  const container = document.getElementById('segmentsList');
  if (!container) return;
  container.innerHTML = '';

  const toolbar = document.createElement('div');
  toolbar.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:8px;';
  toolbar.innerHTML = `<span style="font-size:11px;color:#6b7280;">${allSegments.length} segments</span>
    <button onclick="addNewSegment()" style="font-size:10px;font-weight:600;background:#10b981;color:#fff;border:none;border-radius:4px;padding:4px 10px;cursor:pointer;">+ Add</button>`;
  container.appendChild(toolbar);

  allSegments.forEach((seg, i) => {
    const block = document.createElement('div');
    block.style.cssText = 'display:flex;flex-direction:column;gap:4px;padding:8px;background:#0f172a;border-radius:8px;border:1px solid #1f2937;';

    const header = document.createElement('div');
    header.style.cssText = 'display:flex;align-items:center;justify-content:space-between;';
    header.innerHTML = `<span style="font-size:10px;font-weight:700;color:#6b7280;text-transform:uppercase;">#${seg.index + 1}</span>
      <div style="display:flex;gap:4px;align-items:center;">
        <button onclick="insertSegment(${i}, 'before')"
          style="font-size:9px;font-weight:600;background:rgba(59,130,246,0.1);color:#93c5fd;border:1px solid rgba(59,130,246,0.2);border-radius:3px;padding:2px 6px;cursor:pointer;transition:background 0.15s;"
          onmouseover="this.style.background='rgba(59,130,246,0.2)'"
          onmouseout="this.style.background='rgba(59,130,246,0.1)'"
          title="Insert new segment before">+ Before</button>
        <button onclick="insertSegment(${i}, 'after')"
          style="font-size:9px;font-weight:600;background:rgba(59,130,246,0.1);color:#93c5fd;border:1px solid rgba(59,130,246,0.2);border-radius:3px;padding:2px 6px;cursor:pointer;transition:background 0.15s;"
          onmouseover="this.style.background='rgba(59,130,246,0.2)'"
          onmouseout="this.style.background='rgba(59,130,246,0.1)'"
          title="Insert new segment after">+ After</button>
        <button onclick="saveSingleSegment(${i})" style="font-size:9px;font-weight:600;background:#2563eb;color:#fff;border:none;border-radius:3px;padding:2px 8px;cursor:pointer;">Save</button>
        <button onclick="deleteSegment(${i})" style="font-size:9px;font-weight:600;background:rgba(239,68,68,0.3);color:#fca5a5;border:1px solid rgba(239,68,68,0.3);border-radius:3px;padding:2px 6px;cursor:pointer;">✕</button>
      </div>`;

    const ta = document.createElement('textarea');
    ta.rows = 3;
    ta.style.cssText = 'width:100%;background:#1f2937;border:1px solid #374151;border-radius:6px;padding:8px 10px;color:#fff;font-size:12px;resize:vertical;outline:none;font-family:monospace;line-height:1.5;';
    ta.value = seg.text;
    ta.addEventListener('input', () => { allSegments[i].text = ta.value; });

    block.appendChild(header);

    if (seg.history && seg.history.length > 0) {
      const histDiv = document.createElement('div');
      histDiv.style.cssText = 'margin-bottom:4px;';
      histDiv.innerHTML = '<details style="font-size:10px;color:#6b7280;"><summary>' + seg.history.length + ' versions</summary>' +
        seg.history.map((h, hi) => `<div style="margin-top:3px;padding:4px 6px;background:#1e293b;border-radius:4px;font-size:9px;color:#9ca3af;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">
            <span><b>v${hi+1}</b> <span style="color:#4b5563;">${(h.updated_at||'').slice(0,16)}</span></span>
            <button onclick="revertToHistory(${i}, ${hi}, event)" style="font-size:8px;font-weight:600;background:#6366f1;color:#fff;border:none;border-radius:3px;padding:2px 8px;cursor:pointer;">Restore</button>
          </div>
          <pre style="margin-top:2px;white-space:pre-wrap;color:#d1d5db;">${h.text}</pre>
        </div>`).join('') +
        '</details>';
      block.appendChild(histDiv);
    }

    block.appendChild(ta);
    container.appendChild(block);
  });
}

async function saveSingleSegment(idx) {
  const dbType = document.getElementById('dbTypeSelect')?.value || 'prompts';
  const seg = allSegments[idx];
  if (!seg) return;
  const oldText = seg._savedText;
  const newText = seg.text;
  if (oldText === newText) return;
  try {
    const res = await fetch(`/api/prompts/segment?type=${encodeURIComponent(dbType)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index: seg.index, text: newText })
    });
    if (res.ok) {
      if (!seg.history) seg.history = [];
      seg.history.unshift({ text: oldText, updated_at: new Date().toISOString() });
      seg.history = seg.history.slice(0, 5);
      seg._savedText = newText;
      renderSegmentList();
      showToast('#' + (seg.index + 1) + ' saved');
    }
  } catch(e) {}
}

function revertToHistory(segIdx, histIdx, event) {
  if (event) event.stopPropagation();
  const seg = allSegments[segIdx];
  if (!seg || !seg.history || !seg.history[histIdx]) return;
  seg.text = seg.history[histIdx].text;
  renderSegmentList();
  saveSingleSegment(segIdx);
}

async function deleteSegment(idx) {
  const dbType = document.getElementById('dbTypeSelect')?.value || 'prompts';
  const seg = allSegments[idx];
  if (!seg) return;
  if (!confirm(`Delete segment #${seg.index + 1}? This cannot be undone.`)) return;
  try {
    const url = `/api/prompts/${seg.index}?type=${encodeURIComponent(dbType)}` + (currentSessionId ? `&session_id=${currentSessionId}` : '');
    const res = await fetch(url, { method: 'DELETE' });
    if (res.ok) {
      allSegments.splice(idx, 1);
      allSegments.forEach((s, i) => { s.index = i; });
      renderSegmentList();
      showToast('Segment deleted');
      if (currentSessionId) {
        await loadSessionItems();
      }
    }
  } catch(e) {}
}

async function insertSegment(currentIndex, position) {
  const dbType = document.getElementById('dbTypeSelect')?.value || 'prompts';
  const insertAt = position === 'before' ? currentIndex : currentIndex + 1;
  try {
    const res = await fetch(`/api/prompts/add?type=${encodeURIComponent(dbType)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: '', insert_at: insertAt, session_id: currentSessionId })
    });
    if (res.ok) {
      allSegments.splice(insertAt, 0, { index: insertAt, text: '', history: [], _savedText: '' });
      for (let idx = insertAt + 1; idx < allSegments.length; idx++) {
        allSegments[idx].index = idx;
      }
      renderSegmentList();
      showToast('Segment #' + (insertAt + 1) + ' inserted');
      if (currentSessionId) {
        await loadSessionItems();
      }
    }
  } catch(e) {
    showToast('Failed to insert segment');
  }
}

async function addNewSegment() {
  const dbType = document.getElementById('dbTypeSelect')?.value || 'prompts';
  try {
    const res = await fetch(`/api/prompts/add?type=${encodeURIComponent(dbType)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: '', session_id: currentSessionId })
    });
    if (res.ok) {
      const data = await res.json();
      allSegments.push({ index: data.index, text: '', history: [], _savedText: '' });
      renderSegmentList();
      showToast('Segment #' + (data.index + 1) + ' added');
      if (currentSessionId) {
        await loadSessionItems();
      }
    }
  } catch(e) {}
}


// ── ComfyUI Connection Status ───────────────────────────────────────────────
async function checkComfyStatus() {
  const el = document.getElementById('connectionStatus');
  const label = el ? el.querySelector('.status-label') : null;
  if (!el) return;

  try {
    const res = await fetch('/api/comfy-status');
    const data = await res.json();
    if (data.status === 'connected') {
      el.className = 'status-connected';
      if (label) label.textContent = 'comfyui';
    } else {
      el.className = 'status-disconnected';
      if (label) label.textContent = 'comfyui: offline';
    }
  } catch (e) {
    el.className = 'status-disconnected';
    if (label) label.textContent = 'comfyui: offline';
  }
}

// ── Init ─────────────────────────────────────────────────────────────────────
setInterval(loadState, 2500);
loadSessions();
fetch('/api/enums').then(r => r.json()).then(d => { enumData = d; }).catch(()=>{});

// Check status on load and every 10 seconds
checkComfyStatus();
setInterval(checkComfyStatus, 10000);

// Close editActionsMenu on click outside
document.addEventListener('click', (e) => {
  const menu = document.getElementById('editActionsMenu');
  const toggleBtn = document.getElementById('btnEditActionsToggle');
  if (menu && toggleBtn && menu.style.display === 'block') {
    if (!menu.contains(e.target) && !toggleBtn.contains(e.target)) {
      menu.style.display = 'none';
    }
  }
});

// ── Swipe to Go Back ────────────────────────────────────────────────────────
document.addEventListener('touchstart', (e) => {
  docTouchStartX = e.changedTouches[0].screenX;
  docTouchStartY = e.changedTouches[0].screenY;
}, { passive: true });

document.addEventListener('touchend', (e) => {
  if (currentView !== 'sessionDetail') return;
  
  // Prevent swipe gesture if a modal or popup is open
  const modals = ['promptsModal', 'batchModal', 'editModal', 'settingsModal'];
  for (const mId of modals) {
    const el = document.getElementById(mId);
    if (el && el.style.display !== 'none' && el.style.display !== '') return;
  }
  const imageModal = document.getElementById('imageModal');
  if (imageModal && imageModal.classList.contains('open')) return;

  const touchEndX = e.changedTouches[0].screenX;
  const touchEndY = e.changedTouches[0].screenY;
  
  const diffX = touchEndX - docTouchStartX;
  const diffY = touchEndY - docTouchStartY;
  
  // Swipe right (finger slides left-to-right) to go back to sessions list
  if (diffX > 120 && Math.abs(diffY) < 60) {
    backToSessions();
  }
}, { passive: true });

// ── Autocomplete ────────────────────────────────────────────────────────────
function handleEditPromptInputForAutocomplete() {
  const ta = document.getElementById('editPromptText');
  const autoContainer = document.getElementById('autocompleteSuggestions');
  if (!ta || !autoContainer || editModalDbTags.length === 0) return;

  const text = ta.value;
  const cursor = ta.selectionStart;
  if (cursor === null) {
    autoContainer.style.display = 'none';
    return;
  }

  // Search backward for comma or start of string
  let start = cursor;
  while (start > 0 && text[start - 1] !== ',') {
    start--;
  }

  const tagFragment = text.slice(start, cursor);
  const currentQuery = tagFragment.trim().toLowerCase();

  if (currentQuery.length < 1) {
    autoContainer.style.display = 'none';
    autoContainer.innerHTML = '';
    return;
  }

  // Filter tags from DB matching query (case-insensitive)
  const matches = editModalDbTags.filter(tag => {
    const cleanTag = tag.toLowerCase();
    return cleanTag.startsWith(currentQuery) && cleanTag !== currentQuery;
  }).slice(0, 15);

  if (matches.length === 0) {
    autoContainer.style.display = 'none';
    autoContainer.innerHTML = '';
    return;
  }

  // Render chips
  autoContainer.innerHTML = matches.map(match => {
    return `
      <button onclick="applyAutocompleteSuggestion('${match.replace(/'/g, "\\'")}', ${start}, ${cursor})"
        style="background:#1f2937; border:1px solid #374151; color:#fff; padding:4px 10px; border-radius:9999px; font-size:11px; cursor:pointer; white-space:nowrap; font-weight:500; transition:all 0.15s ease;"
        onmouseover="this.style.background='#374151'; this.style.borderColor='#3b82f6';"
        onmouseout="this.style.background='#1f2937'; this.style.borderColor='#374151';">
        ${match}
      </button>
    `;
  }).join('');

  autoContainer.style.display = 'flex';
}

function applyAutocompleteSuggestion(selectedTag, start, cursor) {
  const ta = document.getElementById('editPromptText');
  if (!ta) return;

  const text = ta.value;
  const tagFragment = text.slice(start, cursor);
  const leadingSpaces = tagFragment.match(/^\s*/)[0];
  const newTag = leadingSpaces + selectedTag;

  const before = text.slice(0, start);
  const after = text.slice(cursor);

  ta.value = before + newTag + after;

  // Trigger text events
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  ta.dispatchEvent(new Event('change', { bubbles: true }));

  // Hide suggestions
  const autoContainer = document.getElementById('autocompleteSuggestions');
  if (autoContainer) {
    autoContainer.style.display = 'none';
    autoContainer.innerHTML = '';
  }

  // Position cursor right after completed tag
  const newCursorPos = start + newTag.length;
  ta.selectionStart = ta.selectionEnd = newCursorPos;
  ta.focus();
}

// Bind events on DOM ready / script load
const taEditPrompt = document.getElementById('editPromptText');
if (taEditPrompt) {
  taEditPrompt.addEventListener('input', handleEditPromptInputForAutocomplete);
  taEditPrompt.addEventListener('click', handleEditPromptInputForAutocomplete);
  taEditPrompt.addEventListener('keyup', handleEditPromptInputForAutocomplete);
}
