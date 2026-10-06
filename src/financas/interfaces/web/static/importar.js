// Importar lançamentos: drag-and-drop highlight and the chosen file name (progressive enhancement).
// The page works without it: the label wraps a real <input type="file">.
(() => {
  const zone = document.querySelector("[data-import-drop]");
  if (!zone) return;
  const input = zone.querySelector("input[type=file]");
  const name = zone.querySelector("[data-import-name]");
  if (!input) return;

  const show = () => {
    const file = input.files && input.files[0];
    if (name) name.textContent = file ? file.name : "Nenhum arquivo escolhido";
  };
  input.addEventListener("change", show);

  const stop = (event) => { event.preventDefault(); };
  ["dragenter", "dragover"].forEach((type) =>
    zone.addEventListener(type, (event) => { stop(event); zone.classList.add("is-drag"); }));
  ["dragleave", "dragend"].forEach((type) =>
    zone.addEventListener(type, () => zone.classList.remove("is-drag")));
  zone.addEventListener("drop", (event) => {
    stop(event);
    zone.classList.remove("is-drag");
    const files = event.dataTransfer && event.dataTransfer.files;
    if (!files || !files.length) return;
    input.files = files;
    input.dispatchEvent(new Event("change", { bubbles: true })); // htmx sends the form on "change"
  });
  // a file dropped outside the zone must not make the browser open it and leave the page
  ["dragover", "drop"].forEach((type) => window.addEventListener(type, (event) => {
    if (!zone.contains(event.target)) event.preventDefault();
  }));
})();
