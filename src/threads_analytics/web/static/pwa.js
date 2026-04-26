/**
 * PWA registration — registers the service worker and handles updates.
 */
(function () {
  if (!("serviceWorker" in navigator)) return;

  navigator.serviceWorker
    .register("/sw.js", { scope: "/" })
    .then(function (reg) {
      reg.addEventListener("updatefound", function () {
        const newWorker = reg.installing;
        if (!newWorker) return;
        newWorker.addEventListener("statechange", function () {
          if (
            newWorker.state === "activated" &&
            navigator.serviceWorker.controller
          ) {
            // New version available — prompt reload (optional)
            console.log("threads-analytics: new version available, reload to update");
          }
        });
      });
    })
    .catch(function (err) {
      console.error("Service Worker registration failed:", err);
    });
})();
