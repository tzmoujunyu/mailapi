(() => {
  const directory = document.querySelector('.home-directory');
  const links = [...directory.querySelectorAll('a[href^="#"]')];
  const sections = links.map(link => document.getElementById(link.hash.slice(1)));
  let scheduled = false;

  function updateDirectory() {
    scheduled = false;
    const boundary = directory.getBoundingClientRect().bottom + 20;
    let active = 0;
    sections.forEach((section, index) => {
      if (section.getBoundingClientRect().top <= boundary) active = index;
    });
    const page = document.documentElement;
    if (page.scrollHeight > window.innerHeight && window.scrollY + window.innerHeight >= page.scrollHeight - 2) {
      active = sections.length - 1;
    }
    links.forEach((link, index) => {
      if (index === active) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  }

  function scheduleUpdate() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(updateDirectory);
  }

  window.addEventListener('scroll', scheduleUpdate, { passive: true });
  window.addEventListener('resize', scheduleUpdate);
  window.addEventListener('hashchange', scheduleUpdate);
  const contentSizeObserver = new ResizeObserver(scheduleUpdate);
  contentSizeObserver.observe(document.querySelector('.container'));
  updateDirectory();
})();
