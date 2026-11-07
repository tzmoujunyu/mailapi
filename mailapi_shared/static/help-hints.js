(() => {
  function positionHint(hint) {
    const trigger = hint.querySelector('.help-hint-trigger');
    const content = document.getElementById(trigger.getAttribute('aria-controls'));
    const rect = trigger.getBoundingClientRect();
    const margin = 12;
    const width = document.documentElement.clientWidth;
    const left = rect.left + rect.width / 2 - content.offsetWidth / 2;
    content.style.left = `${Math.max(margin, Math.min(left, width - content.offsetWidth - margin))}px`;
    const below = rect.bottom + 8;
    const top = below + content.offsetHeight <= window.innerHeight - margin
      ? below : Math.max(margin, rect.top - content.offsetHeight - 8);
    content.style.top = `${top}px`;
  }

  function showHint(hint, open) {
    const trigger = hint.querySelector('.help-hint-trigger');
    const content = document.getElementById(trigger.getAttribute('aria-controls'));
    if (trigger.getAttribute('aria-expanded') === String(open)) return;
    trigger.setAttribute('aria-expanded', String(open));
    if (open) {
      content.hidden = false;
      content.showPopover();
      positionHint(hint);
    } else {
      content.hidePopover();
      content.hidden = true;
    }
  }

  function enterHint(event) {
    const hint = event.target.closest('.help-hint');
    if (hint && !hint.contains(event.relatedTarget)) showHint(hint, true);
  }

  function leaveHint(event) {
    const hint = event.target.closest('.help-hint');
    if (hint && !hint.contains(event.relatedTarget) && hint.dataset.pinned !== 'true') showHint(hint, false);
  }

  document.addEventListener('pointerover', enterHint);
  document.addEventListener('pointerout', leaveHint);
  document.addEventListener('focusin', enterHint);
  document.addEventListener('focusout', leaveHint);
  document.addEventListener('click', event => {
    const trigger = event.target.closest('.help-hint-trigger');
    if (!trigger) return;
    const hint = trigger.closest('.help-hint');
    const pinned = hint.dataset.pinned !== 'true';
    hint.dataset.pinned = String(pinned);
    trigger.setAttribute('aria-pressed', String(pinned));
    showHint(hint, pinned);
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('.help-hint').forEach(hint => {
      hint.dataset.pinned = 'false';
      hint.querySelector('.help-hint-trigger').setAttribute('aria-pressed', 'false');
      showHint(hint, false);
    });
  });

  function positionOpenHints() {
    document.querySelectorAll('.help-hint-trigger[aria-expanded="true"]').forEach(trigger => {
      positionHint(trigger.closest('.help-hint'));
    });
  }
  window.addEventListener('resize', positionOpenHints);
  document.addEventListener('scroll', positionOpenHints, true);
})();
