(function () {
  function syncReportNavigation() {
    var links = Array.from(document.querySelectorAll('.report-nav a[href^="#"]'));
    var sections = links.map(function (link) {
      return document.getElementById(link.getAttribute('href').slice(1));
    }).filter(Boolean);
    if (!links.length || !sections.length) return;

    var scheduled = false;
    function updateCurrentSection() {
      scheduled = false;
      var threshold = Math.min(120, window.innerHeight * 0.24);
      var current = sections[0];
      sections.forEach(function (section) {
        if (section.getBoundingClientRect().top <= threshold) current = section;
      });
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2) {
        current = sections[sections.length - 1];
      }
      links.forEach(function (link) {
        if (link.getAttribute('href') === '#' + current.id) {
          link.setAttribute('aria-current', 'location');
        } else {
          link.removeAttribute('aria-current');
        }
      });
    }

    function scheduleUpdate() {
      if (scheduled) return;
      scheduled = true;
      window.requestAnimationFrame(updateCurrentSection);
    }

    window.addEventListener('scroll', scheduleUpdate, { passive: true });
    window.addEventListener('resize', scheduleUpdate);
    updateCurrentSection();
  }

  function exposeTruncatedMaterialTitles() {
    document.querySelectorAll('.preference-item .material-link, .tab-list button, .material-deep-tab').forEach(function (link) {
      var title = link.textContent.trim();
      if (title) link.setAttribute('title', title);
    });
  }

  function normalizeYuntuHighlightPagination() {
    if (!document.body.classList.contains('unified-yuntu')) return;

    var pageSize = 8;
    function renderPage(panel, requestedPage) {
      var cards = Array.from(panel.querySelectorAll('[data-highlight-frame-card]'));
      var grid = panel.querySelector('.highlight-frame-grid');
      var pagination = panel.querySelector('[data-highlight-pagination]');
      if (!grid || !pagination) return;
      if (grid.nextElementSibling !== pagination) grid.after(pagination);

      if (cards.length <= pageSize) {
        cards.forEach(function (card) { card.hidden = false; });
        pagination.hidden = true;
        return;
      }

      var pageCount = Math.ceil(cards.length / pageSize);
      var page = typeof requestedPage === 'number'
        ? requestedPage
        : Number(panel.getAttribute('data-unified-highlight-page') || '1');
      page = Math.max(1, Math.min(pageCount, page));
      panel.setAttribute('data-unified-highlight-page', String(page));
      cards.forEach(function (card, index) {
        card.hidden = index < (page - 1) * pageSize || index >= page * pageSize;
      });
      pagination.hidden = false;
      var status = pagination.querySelector('[data-highlight-page-status]');
      var previous = pagination.querySelector('[data-highlight-page="previous"]');
      var next = pagination.querySelector('[data-highlight-page="next"]');
      if (status) status.textContent = page + ' / ' + pageCount;
      if (previous) previous.disabled = page <= 1;
      if (next) next.disabled = page >= pageCount;
    }

    document.querySelectorAll('.highlight-category-panel').forEach(function (panel) {
      renderPage(panel, 1);
    });
    document.addEventListener('click', function (event) {
      var button = event.target.closest('[data-highlight-page]');
      if (!button) return;
      var panel = button.closest('.highlight-category-panel');
      if (!panel) return;
      var paginationScrollX = window.scrollX;
      var paginationScrollY = window.scrollY;
      event.preventDefault();
      event.stopPropagation();
      var currentPage = Number(panel.getAttribute('data-unified-highlight-page') || '1');
      renderPage(panel, button.getAttribute('data-highlight-page') === 'next' ? currentPage + 1 : currentPage - 1);
      window.requestAnimationFrame(function () {
        window.scrollTo(paginationScrollX, paginationScrollY);
      });
    }, true);
  }

  function bindStaticYuntuHighlightPagination() {
    if (!document.body.classList.contains('unified-yuntu')) return;

    document.addEventListener('click', function (event) {
      var label = event.target.closest('.static-highlight-pagination label.highlight-page-button[for]');
      if (!label) return;
      var control = document.getElementById(label.htmlFor);
      if (!control) return;
      var staticPageScrollX = window.scrollX;
      var staticPageScrollY = window.scrollY;
      event.preventDefault();
      event.stopPropagation();
      control.checked = true;
      if (typeof control.focus === 'function') control.focus({ preventScroll: true });
      window.requestAnimationFrame(function () {
        window.scrollTo(staticPageScrollX, staticPageScrollY);
        window.requestAnimationFrame(function () {
          window.scrollTo(staticPageScrollX, staticPageScrollY);
        });
      });
    }, true);
  }

  function createUnifiedHighlightLightbox() {
    var existingOverlay = document.getElementById('unified-highlight-lightbox');
    if (existingOverlay) return existingOverlay;

    var overlay = document.createElement('div');
    overlay.id = 'unified-highlight-lightbox';
    overlay.className = 'unified-highlight-lightbox';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', '高光帧大图');
    overlay.hidden = true;

    var surface = document.createElement('div');
    surface.className = 'unified-highlight-lightbox-surface';
    var closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.setAttribute('data-unified-close-highlight-lightbox', 'true');
    closeButton.setAttribute('aria-label', '关闭高光帧大图');
    closeButton.textContent = '关闭';
    var image = document.createElement('img');
    image.alt = '高光帧大图';
    var caption = document.createElement('p');

    surface.append(closeButton, image, caption);
    overlay.append(surface);
    document.body.append(overlay);
    return overlay;
  }

  function bindUnifiedHighlightLightbox() {
    var overlay = createUnifiedHighlightLightbox();
    var lastFocusedElement = null;

    function closeOverlay() {
      if (!overlay || overlay.hidden) return;
      overlay.hidden = true;
      document.body.style.overflow = '';
      var overlayImage = overlay.querySelector('img');
      if (overlayImage) overlayImage.removeAttribute('src');
      if (lastFocusedElement && typeof lastFocusedElement.focus === 'function') {
        lastFocusedElement.focus({ preventScroll: true });
      }
      lastFocusedElement = null;
    }

    function openLightbox(button) {
      var source = button.getAttribute('data-highlight-src');
      if (!source) return;
      var caption = button.getAttribute('data-highlight-caption') || button.getAttribute('aria-label') || '';
      var scrollX = window.scrollX;
      var scrollY = window.scrollY;
      if (overlay) {
        lastFocusedElement = document.activeElement;
        var overlayImage = overlay.querySelector('img');
        var overlayCaption = overlay.querySelector('p');
        if (overlayImage) overlayImage.src = source;
        if (overlayCaption) overlayCaption.textContent = caption;
        overlay.hidden = false;
        document.body.style.overflow = 'hidden';
        var overlayClose = overlay.querySelector('[data-unified-close-highlight-lightbox]');
        if (overlayClose && typeof overlayClose.focus === 'function') {
          overlayClose.focus({ preventScroll: true });
        }
        window.requestAnimationFrame(function () {
          window.scrollTo(scrollX, scrollY);
        });
        return;
      }
      var dialog = document.getElementById('highlight-lightbox');
      if (dialog && typeof dialog.showModal === 'function') {
        var dialogImage = dialog.querySelector('img');
        var dialogCaption = dialog.querySelector('p');
        if (dialogImage) dialogImage.src = source;
        if (dialogCaption) dialogCaption.textContent = caption;
        if (!dialog.open) dialog.showModal();
        var dialogClose = dialog.querySelector('[data-close-highlight-lightbox]');
        if (dialogClose && typeof dialogClose.focus === 'function') {
          dialogClose.focus({ preventScroll: true });
        }
        window.requestAnimationFrame(function () {
          window.scrollTo(scrollX, scrollY);
        });
        return;
      }
      var frameLightbox = document.getElementById('frame-lightbox');
      if (!frameLightbox) return;
      var overlayImage = frameLightbox.querySelector('img');
      var overlayCaption = frameLightbox.querySelector('.frame-lightbox-caption');
      if (overlayImage) overlayImage.src = source;
      if (overlayCaption) overlayCaption.textContent = caption;
      frameLightbox.hidden = false;
      document.body.style.overflow = 'hidden';
      var closeButton = frameLightbox.querySelector('.frame-lightbox-close');
      if (closeButton) closeButton.focus();
    }

    document.addEventListener('click', function (event) {
      var button = event.target.closest('[data-highlight-src]');
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      openLightbox(button);
    }, true);
    document.addEventListener('click', function (event) {
      if (!overlay) return;
      if (event.target === overlay || event.target.closest('[data-unified-close-highlight-lightbox]')) {
        event.preventDefault();
        closeOverlay();
      }
    }, true);
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeOverlay();
    });
  }

  function normalizeQianchuanPresentation() {
    if (!document.body.classList.contains('unified-qianchuan')) return;

    var subtitle = document.getElementById('report-subtitle');
    if (subtitle) subtitle.textContent = subtitle.textContent.replace(/[。.]$/, '');

    var scope = document.querySelector('#top-material-roi .top-roi-scope');
    if (scope) {
      var divider = document.createElement('hr');
      divider.className = 'unified-divider';
      divider.setAttribute('aria-hidden', 'true');
      scope.replaceWith(divider);
    }
  }

  function polarPoint(angle, radius, center) {
    var radians = (angle - 90) * Math.PI / 180;
    return {
      x: center + radius * Math.cos(radians),
      y: center + radius * Math.sin(radians)
    };
  }

  function createPieSlicePath(startAngle, endAngle) {
    var center = 160;
    var radius = 132;
    var safeEnd = Math.min(endAngle, startAngle + 359.999);
    var start = polarPoint(startAngle, radius, center);
    var end = polarPoint(safeEnd, radius, center);
    var largeArc = safeEnd - startAngle > 180 ? 1 : 0;
    return [
      'M', center, center,
      'L', start.x, start.y,
      'A', radius, radius, 0, largeArc, 1, end.x, end.y,
      'Z'
    ].join(' ');
  }

  function readPieSlices(pie, legendItems) {
    var structured = pie.getAttribute('data-pie-slices');
    if (structured) {
      try {
        return JSON.parse(structured).map(function (slice, index) {
          var swatch = legendItems[index] && legendItems[index].querySelector('.legend-swatch');
          return {
            label: slice.label,
            start: Number(slice.start),
            end: Number(slice.end),
            color: swatch ? swatch.style.backgroundColor || swatch.style.background : ''
          };
        });
      } catch (error) {
        return [];
      }
    }

    var source = pie.style.background || pie.style.backgroundImage || '';
    var slices = parseSerializedPieStops(source);
    return slices.map(function (slice, index) {
      var item = legendItems[index];
      var title = item ? item.getAttribute('title') || item.textContent.trim() : '';
      slice.label = title.split('：')[0] || '数据项 ' + (index + 1);
      return slice;
    });
  }

  function parseSerializedPieStops(source) {
    var stopPattern = /(#[0-9a-f]{3,8}|rgba?\([^)]+\))\s+(-?[\d.]+)(deg|%)(?:\s+(-?[\d.]+)\3)?/gi;
    var explicitSlices = [];
    var singleStops = [];
    var match;
    function toDegrees(value, unit) {
      return unit === '%' ? Number(value) * 3.6 : Number(value);
    }
    while ((match = stopPattern.exec(source))) {
      if (match[4] !== undefined) {
        explicitSlices.push({
          start: toDegrees(match[2], match[3]),
          end: toDegrees(match[4], match[3]),
          color: match[1]
        });
      } else {
        singleStops.push({ angle: toDegrees(match[2], match[3]), color: match[1] });
      }
    }
    if (explicitSlices.length) return explicitSlices;

    var pairedSlices = [];
    for (var index = 0; index + 1 < singleStops.length; index += 2) {
      var start = singleStops[index];
      var end = singleStops[index + 1];
      var startColor = start.color.replace(/\s+/g, '').toLowerCase();
      var endColor = end.color.replace(/\s+/g, '').toLowerCase();
      if (startColor !== endColor || end.angle < start.angle) return [];
      pairedSlices.push({ start: start.angle, end: end.angle, color: start.color });
    }
    return pairedSlices;
  }

  function upgradePieChart(pie) {
    if (!pie || pie.getAttribute('data-unified-pie-ready') === 'true') return;
    var layout = pie.closest('.pie-layout, .audience-pie-layout');
    if (!layout) return;
    var legendItems = Array.from(layout.querySelectorAll('.pie-legend .legend-item'));
    var slices = readPieSlices(pie, legendItems);
    if (!slices.length || slices.length !== legendItems.length) return;

    pie.setAttribute('data-unified-pie-ready', 'true');
    pie.setAttribute('role', 'group');
    pie.removeAttribute('tabindex');
    var namespace = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(namespace, 'svg');
    svg.setAttribute('class', 'unified-pie-svg');
    svg.setAttribute('viewBox', '0 0 320 320');
    svg.setAttribute('aria-label', pie.getAttribute('aria-label') || '饼图');
    var paths = [];
    var lockedIndex = -1;
    var pointerIndex = -1;
    var focusIndex = -1;

    function renderActiveState() {
      var activeIndex = pointerIndex >= 0
        ? pointerIndex
        : focusIndex >= 0
          ? focusIndex
          : lockedIndex;
      paths.forEach(function (path, index) {
        path.classList.toggle('is-active', index === activeIndex);
        path.setAttribute('aria-pressed', index === lockedIndex ? 'true' : 'false');
      });
      legendItems.forEach(function (item, index) {
        item.setAttribute('aria-pressed', index === lockedIndex ? 'true' : 'false');
      });
    }

    function toggleLocked(index) {
      lockedIndex = lockedIndex === index ? -1 : index;
      renderActiveState();
    }

    slices.forEach(function (slice, index) {
      var path = document.createElementNS(namespace, 'path');
      var midpoint = (slice.start + slice.end) / 2;
      var radians = (midpoint - 90) * Math.PI / 180;
      path.setAttribute('d', createPieSlicePath(slice.start, slice.end));
      path.setAttribute('fill', slice.color || 'var(--unified-accent)');
      path.setAttribute('class', 'unified-pie-slice');
      path.setAttribute('data-slice-index', String(index));
      path.setAttribute('tabindex', '0');
      path.setAttribute('role', 'button');
      path.setAttribute('aria-label', legendItems[index].getAttribute('title') || slice.label);
      path.style.setProperty('--slice-offset-x', (Math.cos(radians) * 10).toFixed(2) + 'px');
      path.style.setProperty('--slice-offset-y', (Math.sin(radians) * 10).toFixed(2) + 'px');
      path.addEventListener('pointerenter', function () {
        pointerIndex = index;
        renderActiveState();
      });
      path.addEventListener('pointerleave', function () {
        if (pointerIndex === index) pointerIndex = -1;
        renderActiveState();
      });
      path.addEventListener('pointerdown', function (event) {
        event.preventDefault();
      });
      path.addEventListener('focus', function () {
        focusIndex = index;
        renderActiveState();
      });
      path.addEventListener('blur', function () {
        if (focusIndex === index) focusIndex = -1;
        renderActiveState();
      });
      path.addEventListener('click', function () { toggleLocked(index); });
      path.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        toggleLocked(index);
      });
      paths.push(path);
      svg.append(path);

      var legendItem = legendItems[index];
      legendItem.setAttribute('role', 'button');
      legendItem.setAttribute('tabindex', '0');
      legendItem.addEventListener('pointerenter', function () {
        pointerIndex = index;
        renderActiveState();
      });
      legendItem.addEventListener('pointerleave', function () {
        if (pointerIndex === index) pointerIndex = -1;
        renderActiveState();
      });
      legendItem.addEventListener('pointerdown', function (event) {
        event.preventDefault();
      });
      legendItem.addEventListener('focus', function () {
        focusIndex = index;
        renderActiveState();
      });
      legendItem.addEventListener('blur', function () {
        if (focusIndex === index) focusIndex = -1;
        renderActiveState();
      });
      legendItem.addEventListener('click', function () { toggleLocked(index); });
      legendItem.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        toggleLocked(index);
      });
    });

    pie.append(svg);
    renderActiveState();
  }

  function syncPieCharts(root) {
    if (root && root.matches && root.matches('.pie-chart')) upgradePieChart(root);
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll('.pie-chart').forEach(upgradePieChart);
  }

  function observePieCharts() {
    syncPieCharts(document);
    var observer = new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        mutation.addedNodes.forEach(syncPieCharts);
      });
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  document.body.setAttribute('data-unified-report-ready', 'true');
  syncReportNavigation();
  exposeTruncatedMaterialTitles();
  normalizeQianchuanPresentation();
  normalizeYuntuHighlightPagination();
  bindStaticYuntuHighlightPagination();
  bindUnifiedHighlightLightbox();
  observePieCharts();
}());
