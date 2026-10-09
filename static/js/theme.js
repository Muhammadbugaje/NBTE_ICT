(function () {
  var btn = document.getElementById("theme-toggle");
  if (!btn) return;
  btn.addEventListener("click", function () {
    var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("nbte-theme", next); } catch (e) {}
    // Cookie copy lets the server know the preference later (and covers storage-blocked browsers).
    document.cookie = "nbte-theme=" + next + "; path=/; max-age=31536000; SameSite=Lax";
  });
})();
