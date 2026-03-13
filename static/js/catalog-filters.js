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

function initCatalogDropdowns() {
  const dropdowns = document.querySelectorAll("[data-catalog-dropdown]");

  dropdowns.forEach((dropdown) => {
    if (dropdown.dataset.catalogDropdownBound === "true") {
      return;
    }

    dropdown.dataset.catalogDropdownBound = "true";

    dropdown.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !dropdown.hasAttribute("open")) {
        return;
      }

      event.preventDefault();
      event.stopPropagation();
      dropdown.removeAttribute("open");

      const summary = dropdown.querySelector("summary");
      if (summary instanceof HTMLElement) {
        summary.blur();
      }
    });
  });
}

function updateCatalogSortState(selectedValue) {
  const sortInput = document.querySelector("[data-catalog-sort-input]");

  if (sortInput instanceof HTMLInputElement) {
    sortInput.value = selectedValue || "";
    sortInput.disabled = !selectedValue;
  }

  document.querySelectorAll("[data-sort-option]").forEach((option) => {
    const checkbox = option.querySelector(".checkbox-purple");
    if (!(checkbox instanceof HTMLElement)) {
      return;
    }

    checkbox.classList.toggle("checked", option.dataset.sortValue === selectedValue);
  });
}

function initCatalogSortControls() {
  document.querySelectorAll("[data-sort-option]").forEach((option) => {
    if (option.dataset.sortBound === "true") {
      return;
    }

    option.dataset.sortBound = "true";
    option.addEventListener("click", () => {
      updateCatalogSortState(option.dataset.sortValue || "");
    });
  });

  document.querySelectorAll("[data-sort-reset]").forEach((control) => {
    if (control.dataset.sortResetBound === "true") {
      return;
    }

    control.dataset.sortResetBound = "true";
    control.addEventListener("click", () => {
      updateCatalogSortState("");
    });
  });
}

function initCatalogFilterReset() {
  document.querySelectorAll("[data-filter-reset]").forEach((button) => {
    if (button.dataset.filterResetBound === "true") {
      return;
    }

    button.dataset.filterResetBound = "true";
    button.addEventListener("click", () => {
      const form = button.closest("[data-catalog-filter-form]");
      if (!(form instanceof HTMLFormElement)) {
        return;
      }

      form.querySelectorAll('input[type="checkbox"]').forEach((input) => {
        input.checked = false;
      });

      form.querySelectorAll("[data-float-input]").forEach((input) => {
        input.value = input.dataset.defaultValue || "";
      });

      form.requestSubmit();
    });
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    initCatalogFloatInputs();
    initCatalogDropdowns();
    initCatalogSortControls();
    initCatalogFilterReset();
  });
} else {
  initCatalogFloatInputs();
  initCatalogDropdowns();
  initCatalogSortControls();
  initCatalogFilterReset();
}
