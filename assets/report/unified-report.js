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
    document.querySelectorAll('.preference-item .material-link').forEach(function (link) {
      var title = link.textContent.trim();
      if (title) link.setAttribute('title', title);
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
    var stopPattern = /(#[0-9a-f]{3,8}|rgba?\([^)]+\))\s+(-?[\d.]+)deg(?:\s+(-?[\d.]+)deg)?/gi;
    var explicitSlices = [];
    var singleStops = [];
    var match;
    while ((match = stopPattern.exec(source))) {
      if (match[3] !== undefined) {
        explicitSlices.push({
          start: Number(match[2]),
          end: Number(match[3]),
          color: match[1]
        });
      } else {
        singleStops.push({ angle: Number(match[2]), color: match[1] });
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
  observePieCharts();
}());
