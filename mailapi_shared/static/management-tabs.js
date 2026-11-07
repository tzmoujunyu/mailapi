(() => {
  document.querySelectorAll('.management-tabs[data-viewport]').forEach(tablist => {
    const tabs = [...tablist.querySelectorAll('[role="tab"]')];
    const panels = tabs.map(tab => document.getElementById(tab.getAttribute('aria-controls')));
    const viewport = document.getElementById(tablist.dataset.viewport);
    const track = viewport.querySelector('.management-panels');
    const scrollPositions = tabs.map(() => null);
    let active = 0;
    tablist.style.setProperty('--management-count', tabs.length);
    window.history.scrollRestoration = 'manual';

    function fitPanel() {
      viewport.style.setProperty('--management-tab-height', `${tablist.offsetHeight}px`);
      viewport.style.height = `${panels[active].offsetHeight}px`;
    }

    function contentStart() {
      return viewport.getBoundingClientRect().top + window.scrollY - tablist.offsetHeight
        - parseFloat(getComputedStyle(tablist).marginBottom);
    }

    function selectPanel(index, restore = true) {
      if (restore && index === active) return;
      const offset = window.scrollY - contentStart();
      if (restore) scrollPositions[active] = offset;
      active = index;
      tablist.style.setProperty('--management-index', index);
      track.style.setProperty('--management-index', index);
      tabs.forEach((tab, position) => {
        const selected = position === index;
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
        panels[position].setAttribute('aria-hidden', String(!selected));
        panels[position].inert = !selected;
      });
      // Set the new scroll range before restoring, including when leaving a shorter panel.
      viewport.classList.add('is-switching');
      fitPanel();
      if (restore) {
        window.scrollTo({
          top: Math.max(0, contentStart() + (scrollPositions[index] ?? Math.min(0, offset))),
          behavior: 'instant',
        });
      }
      viewport.classList.remove('is-switching');
    }

    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => selectPanel(index));
      tab.addEventListener('keydown', event => {
        const directions = {
          ArrowLeft: (index + tabs.length - 1) % tabs.length,
          ArrowRight: (index + 1) % tabs.length,
          Home: 0,
          End: tabs.length - 1,
        };
        if (!(event.key in directions)) return;
        event.preventDefault();
        const target = directions[event.key];
        selectPanel(target);
        tabs[target].focus({ preventScroll: true });
      });
    });
    const observer = new ResizeObserver(fitPanel);
    panels.forEach(panel => observer.observe(panel));
    observer.observe(tablist);
    function resetPanels() {
      scrollPositions.fill(null);
      selectPanel(0, false);
      window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
    }
    window.addEventListener('pageshow', resetPanels);
    resetPanels();
  });
})();
