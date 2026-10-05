// Small progressive enhancements (kept external so the Content-Security-Policy can stay strict).
document.addEventListener('click', function (e) {
  var el = e.target.closest('[data-copy]');
  if (el) {
    var target = document.getElementById(el.getAttribute('data-copy'));
    if (target && navigator.clipboard) {
      navigator.clipboard.writeText(target.value || target.textContent).then(function () {
        var old = el.textContent; el.textContent = 'Copied'; setTimeout(function () { el.textContent = old; }, 1200);
      });
    }
  }
});
document.addEventListener('submit', function (e) {
  var msg = e.target.getAttribute('data-confirm');
  if (msg && !window.confirm(msg)) { e.preventDefault(); }
});
