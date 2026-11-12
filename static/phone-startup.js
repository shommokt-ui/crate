"use strict";

// Included in the phone HTML itself, so even a failed startup stays readable.
(() => {
  const status = document.getElementById("phone-startup");
  const message = document.getElementById("phone-startup-message");
  const reload = document.getElementById("phone-reload");
  // Preserve the QR code when reloading, including in a home-screen web app.
  reload.href = location.href;
  let ready = false;
  function failed() {
    if (ready) return;
    status.hidden = false;
    message.textContent = "Crate couldn’t finish opening. Keep Crate open on your Mac, then reload this page.";
    reload.hidden = false;
  }
  const timer = setTimeout(failed, 10000);
  const onError = (event) => { if (event.target === window) failed(); };
  window.addEventListener("error", onError);
  document.addEventListener("crate:ready", () => {
    ready = true;
    clearTimeout(timer);
    status.hidden = true;
    window.removeEventListener("error", onError);
  }, { once: true });
})();
