// frontend/src/js/features/review/download.js

/** Сохраняет проверенный PDF и освобождает временный адрес браузера. */

export function downloadReviewedPdf({ blob, filename }) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
