"use strict";
document.querySelectorAll("[data-expires]").forEach((element) => {
  const target = element.querySelector("[data-countdown]");
  const end = Date.parse(element.dataset.expires);
  if (!target || !Number.isFinite(end)) return;
  function update() {
    const remaining = Math.max(0, Math.floor((end - Date.now()) / 1000));
    target.textContent = remaining ? `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")} ${element.dataset.remaining}` : element.dataset.expired;
    if (!remaining) element.closest("article").querySelectorAll("button").forEach((button) => { button.disabled = true; });
  }
  update();
  window.setInterval(update, 1000);
});
