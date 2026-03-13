function sanitizeFloatInputValue(value) {
  let result = "";
  let hasDecimalSeparator = false;

  for (const rawChar of value) {
    const char = rawChar === "." ? "," : rawChar;

    if (char >= "0" && char <= "9") {
      result += char;
      continue;
    }

    if (char === "," && !hasDecimalSeparator) {
      result += char;
      hasDecimalSeparator = true;
    }
  }

  return result;
}

function parseCommaFloat(value) {
  const normalized = sanitizeFloatInputValue(value).replace(",", ".");
  const parsed = Number.parseFloat(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatCommaFloat(value) {
  return value.toFixed(2).replace(".", ",");
}

function initCatalogFloatInputs() {
  const inputs = document.querySelectorAll("[data-float-input]");

  inputs.forEach((input) => {
    if (input.dataset.floatInputBound === "true") {
      return;
    }

    input.dataset.floatInputBound = "true";

    input.addEventListener("input", () => {
      const sanitizedValue = sanitizeFloatInputValue(input.value);

      if (input.value !== sanitizedValue) {
        input.value = sanitizedValue;
      }
    });

    input.addEventListener("paste", (event) => {
      event.preventDefault();

      const pastedText = event.clipboardData?.getData("text") || "";
      const sanitizedValue = sanitizeFloatInputValue(pastedText);

      if (!sanitizedValue) {
        return;
      }

      input.setRangeText(
        sanitizedValue,
        input.selectionStart ?? input.value.length,
        input.selectionEnd ?? input.value.length,
        "end",
      );
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    input.addEventListener("blur", () => {
      const parsedValue = parseCommaFloat(input.value);
      const minValue = parseCommaFloat(input.dataset.minPrice || "");
      const maxValue = parseCommaFloat(input.dataset.maxPrice || "");

      if (parsedValue === null) {
        input.value = "";
        return;
      }

      let normalizedValue = parsedValue;

      if (minValue !== null) {
        normalizedValue = Math.max(normalizedValue, minValue);
      }

      if (maxValue !== null) {
        normalizedValue = Math.min(normalizedValue, maxValue);
      }

      input.value = formatCommaFloat(normalizedValue);
    });
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initCatalogFloatInputs);
} else {
  initCatalogFloatInputs();
}
