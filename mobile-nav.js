(function () {
  var topBar = document.querySelector(".topBar");
  var currentPage = document.body.getAttribute("data-page");
  var pageOrder = ["about", "services", "gallery", "contact"];
  var pageTargets = {
    about: "./about.html",
    services: "./services.html",
    gallery: "./gallery.html",
    contact: "./contact.html"
  };
  var lastScrollY = window.scrollY;
  var touchStartX = 0;
  var touchStartY = 0;
  var swipeThreshold = 60;

  function isMobileViewport() {
    return window.matchMedia("(max-width: 900px)").matches;
  }

  function updateHeaderVisibility() {
    if (!topBar) return;
    if (!isMobileViewport()) {
      topBar.classList.remove("hidden");
      return;
    }
    var currentScrollY = window.scrollY;
    var scrollingDown = currentScrollY > lastScrollY;
    var farEnough = currentScrollY > 80;
    topBar.classList.toggle("hidden", scrollingDown && farEnough);
    lastScrollY = currentScrollY;
  }

  function navigateBySwipe(direction) {
    if (!isMobileViewport() || !currentPage) return;
    var currentIndex = pageOrder.indexOf(currentPage);
    if (currentIndex === -1) return;
    var nextIndex = currentIndex + direction;
    if (nextIndex < 0 || nextIndex >= pageOrder.length) return;
    window.location.href = pageTargets[pageOrder[nextIndex]];
  }

  window.addEventListener("scroll", updateHeaderVisibility, { passive: true });
  window.addEventListener("resize", updateHeaderVisibility);
  updateHeaderVisibility();

  document.addEventListener("touchstart", function (event) {
    if (!isMobileViewport() || event.touches.length !== 1) return;
    touchStartX = event.touches[0].clientX;
    touchStartY = event.touches[0].clientY;
  }, { passive: true });

  document.addEventListener("touchend", function (event) {
    if (!isMobileViewport() || !touchStartX || event.changedTouches.length !== 1) return;
    var deltaX = event.changedTouches[0].clientX - touchStartX;
    var deltaY = event.changedTouches[0].clientY - touchStartY;
    touchStartX = 0;
    touchStartY = 0;
    if (Math.abs(deltaX) < swipeThreshold || Math.abs(deltaX) < Math.abs(deltaY)) return;
    navigateBySwipe(deltaX < 0 ? 1 : -1);
  }, { passive: true });
})();
