const form = document.getElementById("analysisForm");
const result = document.getElementById("result");
const modal = document.getElementById("modal");
const modalText = document.getElementById("modalText");
const submitButton = document.getElementById("submitButton");

function setBusy(value, text = "Идёт анализ документа…") {
  modalText.textContent = text;
  modal.classList.toggle("hidden", !value);
  submitButton.disabled = value;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const pdf = document.getElementById("pdfFile").files[0];
  const dxf = document.getElementById("dxfFile").files[0];
  const pages = document.getElementById("pages").value.trim();

  if (!pdf) return;

  const body = new FormData();
  body.append("pdf", pdf);
  if (dxf) body.append("dxf", dxf);
  body.append("pages", pages);

  setBusy(true, "Отправляем PDF и параметры в n8n…");
  result.textContent = "Выполняется запрос…";

  try {
    const response = await fetch("/api/analysis", { method: "POST", body });
    const text = await response.text();
    let payload;
    try { payload = JSON.parse(text); } catch { payload = { raw: text }; }
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${JSON.stringify(payload)}`);
    result.textContent = JSON.stringify(payload, null, 2);
  } catch (error) {
    result.textContent = `Ошибка:\n${error.message}`;
  } finally {
    setBusy(false);
  }
});
