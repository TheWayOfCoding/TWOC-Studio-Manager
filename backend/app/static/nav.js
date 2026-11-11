// Studio Hub shared menu behavior: mobile hamburger + Administration dropdown.
// No dependencies; loaded with defer on every hub page.
(function () {
  var bar = document.querySelector(".topbar");
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("mainnav");
  if (bar && toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = bar.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
  var drops = document.querySelectorAll(".navdrop");
  function closeAll(except) {
    drops.forEach(function (drop) {
      if (drop === except) return;
      drop.classList.remove("open");
      var btn = drop.querySelector(".navdrop-toggle");
      if (btn) btn.setAttribute("aria-expanded", "false");
    });
  }
  drops.forEach(function (drop) {
    var btn = drop.querySelector(".navdrop-toggle");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var open = drop.classList.toggle("open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) closeAll(drop);
    });
  });
  document.addEventListener("click", function (e) {
    drops.forEach(function (drop) {
      if (drop.contains(e.target)) return;
      drop.classList.remove("open");
      var btn = drop.querySelector(".navdrop-toggle");
      if (btn) btn.setAttribute("aria-expanded", "false");
    });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeAll(null);
  });
})();
