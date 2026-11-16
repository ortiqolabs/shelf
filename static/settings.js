const form = document.getElementById("settings-form");
const statusEl = document.getElementById("status");
const saveButton = form.querySelector('button[type="submit"]');

function say(message, tone = "") {
  statusEl.textContent = message;
  statusEl.dataset.tone = tone;
}

function fill(settings) {
  document.getElementById("downloadDir").value = settings.downloadDir || "";
  document.getElementById("maxConcurrent").value = settings.maxConcurrent ?? 4;
  document.getElementById("maxDownloadLimit").value = settings.maxDownloadLimit || "0";
  document.getElementById("configPath").textContent = settings.configPath
    ? `Written to ${settings.configPath}`
    : "";
}

function readForm() {
  return {
    downloadDir: document.getElementById("downloadDir").value.trim(),
    maxConcurrent: Number(document.getElementById("maxConcurrent").value),
    maxDownloadLimit: document.getElementById("maxDownloadLimit").value.trim() || "0",
  };
}

function markInvalid(id, invalid) {
  const input = document.getElementById(id);
  if (input) input.setAttribute("aria-invalid", invalid ? "true" : "false");
}

fetch("/api/settings")
  .then((response) => {
    if (!response.ok) throw new Error("Could not load settings");
    return response.json();
  })
  .then(fill)
  .catch((error) => say(error.message, "error"));

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  ["downloadDir", "maxConcurrent", "maxDownloadLimit"].forEach((id) => markInvalid(id, false));
  saveButton.disabled = true;
  say("Saving");
  try {
    const response = await fetch("/api/settings", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(readForm()),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const field = body.field;
      if (field) markInvalid(field, true);
      say(body.detail || "Could not save the settings", "error");
      return;
    }
    fill(body);
    say("Saved", "ok");
  } catch {
    say("Cannot reach the Pi", "error");
  } finally {
    saveButton.disabled = false;
  }
});
