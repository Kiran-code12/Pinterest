// Small progressive enhancements (kept external so the Content-Security-Policy can stay strict).
function flashButton(el, text) {
  var old = el.getAttribute('data-label') || el.textContent;
  el.setAttribute('data-label', old);
  el.textContent = text;
  setTimeout(function () { el.textContent = old; }, 1400);
}
function copyText(text, el) {
  function fallback() {  // clipboard API needs https/localhost; this works everywhere
    var ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    var ok = false; try { ok = document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(ta); flashButton(el, ok ? 'Copied ✓' : 'Press Ctrl+C');
  }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(function () { flashButton(el, 'Copied ✓'); }, fallback);
  } else { fallback(); }
}
document.addEventListener('click', function (e) {
  var el = e.target.closest('[data-copy],[data-copy-text]');
  if (!el) { return; }
  var text = el.getAttribute('data-copy-text');
  if (text === null) {
    var target = document.getElementById(el.getAttribute('data-copy'));
    text = target ? (target.value !== undefined && target.value !== '' ? target.value : target.textContent) : '';
  }
  copyText(text, el);
});
document.addEventListener('submit', function (e) {
  var msg = e.target.getAttribute('data-confirm') || (e.submitter && e.submitter.getAttribute('data-confirm'));
  if (msg && !window.confirm(msg)) { e.preventDefault(); }
});
