(() => {
  document.querySelectorAll('.management-tabs[data-viewport]').forEach(tablist => {
    const tabs = [...tablist.querySelectorAll('[role="tab"]')];
    const panels = tabs.map(tab => document.getElementById(tab.getAttribute('aria-controls')));
    const viewport = document.getElementById(tablist.dataset.viewport);
    const track = viewport.querySelector('.management-panels');
    let active = 0;
    tablist.style.setProperty('--management-count', tabs.length);

    function fitPanel() {
      viewport.style.height = `${panels[active].offsetHeight}px`;
    }

    function selectPanel(index) {
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
      fitPanel();
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
        tabs[target].focus();
      });
    });
    const observer = new ResizeObserver(fitPanel);
    panels.forEach(panel => observer.observe(panel));
    selectPanel(0);
  });
})();
