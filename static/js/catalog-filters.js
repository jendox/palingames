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

function initCatalogFloatInputs(root = document) {
  const inputs = root.querySelectorAll("[data-float-input]");

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

function initCatalogDropdowns(root = document) {
  const dropdowns = root.querySelectorAll("[data-catalog-dropdown]");

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

function initCatalogSortControls(root = document) {
  root.querySelectorAll("[data-sort-option]").forEach((option) => {
    if (option.dataset.sortBound === "true") {
      return;
    }

    option.dataset.sortBound = "true";
    option.addEventListener("click", () => {
      updateCatalogSortState(option.dataset.sortValue || "");
    });
  });

  root.querySelectorAll("[data-sort-reset]").forEach((control) => {
    if (control.dataset.sortResetBound === "true") {
      return;
    }

    control.dataset.sortResetBound = "true";
    control.addEventListener("click", () => {
      updateCatalogSortState("");
    });
  });
}

function initCatalogFilterReset(root = document) {
  root.querySelectorAll("[data-filter-reset]").forEach((button) => {
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

      if (window.htmx) {
        window.htmx.trigger(form, "submit");
        return;
      }

      form.requestSubmit();
    });
  });
}

function setFavoriteState(button, isFavorited) {
  button.dataset.favorited = isFavorited ? "true" : "false";
  button.setAttribute("aria-label", isFavorited ? "Удалить из избранного" : "Добавить в избранное");

  const icon = button.querySelector("[data-favorite-icon]");
  if (!(icon instanceof HTMLImageElement)) {
    return;
  }

  icon.src = isFavorited ? icon.dataset.iconActive : icon.dataset.iconInactive;
}

function setCartState(button, isInCart) {
  button.dataset.inCart = isInCart ? "true" : "false";
  button.classList.toggle("catalog-card-cart-button-active", isInCart);
  button.setAttribute("aria-label", isInCart ? "Удалить из корзины" : "Добавить в корзину");
}

function getCookie(name) {
  const cookieString = document.cookie || "";
  for (const part of cookieString.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) {
      return decodeURIComponent(rest.join("="));
    }
  }
  return null;
}

async function toggleCartOnServer(productId) {
  const csrfToken = getCookie("csrftoken");
  const body = new URLSearchParams({ product_id: String(productId) });

  const response = await fetch("/cart/toggle/", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Accept: "application/json",
      ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
    },
    body: body.toString(),
  });

  if (!response.ok) {
    throw new Error("Cart toggle failed");
  }

  return response.json();
}

function getCatalogPreviewDialog() {
  const dialog = document.getElementById("catalogProductPreviewDialog");
  return dialog instanceof HTMLDialogElement ? dialog : null;
}

function closeCatalogPreviewDialog(dialog) {
  dialog.classList.remove("opacity-100", "scale-100");
  dialog.classList.add("opacity-0", "scale-95");

  window.setTimeout(() => {
    if (dialog.open) {
      dialog.close();
    }
  }, 150);
}

function openCatalogPreviewDialog(trigger) {
  const dialog = getCatalogPreviewDialog();
  if (!dialog) {
    return;
  }

  const image = dialog.querySelector("[data-catalog-preview-image]");
  const title = dialog.querySelector("[data-catalog-preview-title]");
  const category = dialog.querySelector("[data-catalog-preview-category]");
  const price = dialog.querySelector("[data-catalog-preview-price]");
  const rating = dialog.querySelector("[data-catalog-preview-rating]");
  const link = dialog.querySelector("[data-catalog-preview-link]");

  if (image instanceof HTMLImageElement) {
    image.src = trigger.dataset.previewImage || "";
    image.alt = trigger.dataset.previewTitle || "";
  }
  if (title instanceof HTMLElement) {
    title.textContent = trigger.dataset.previewTitle || "";
  }
  if (category instanceof HTMLElement) {
    category.textContent = trigger.dataset.previewCategory || "";
  }
  if (price instanceof HTMLElement) {
    price.textContent = trigger.dataset.previewPrice || "";
  }
  if (rating instanceof HTMLElement) {
    rating.textContent = trigger.dataset.previewRating || "";
  }
  if (link instanceof HTMLAnchorElement) {
    link.href = trigger.dataset.previewUrl || "#";
  }

  if (!dialog.open) {
    dialog.showModal();
  }

  requestAnimationFrame(() => {
    dialog.classList.remove("opacity-0", "scale-95");
    dialog.classList.add("opacity-100", "scale-100");
  });
}

function initCatalogPreviewDialog() {
  const dialog = getCatalogPreviewDialog();
  if (!dialog || dialog.dataset.catalogPreviewBound === "true") {
    return;
  }

  dialog.dataset.catalogPreviewBound = "true";

  dialog.querySelectorAll("[data-catalog-preview-close]").forEach((button) => {
    button.addEventListener("click", () => {
      closeCatalogPreviewDialog(dialog);
    });
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      closeCatalogPreviewDialog(dialog);
    }
  });

  dialog.addEventListener("close", () => {
    dialog.classList.remove("opacity-100", "scale-100");
    dialog.classList.add("opacity-0", "scale-95");
  });
}

function initCatalogProductCards(root = document) {
  root.querySelectorAll("[data-catalog-favorite-toggle]").forEach((button) => {
    if (button.dataset.favoriteBound === "true") {
      return;
    }

    button.dataset.favoriteBound = "true";
    button.addEventListener("click", () => {
      const isFavorited = button.dataset.favorited === "true";
      setFavoriteState(button, !isFavorited);
    });
  });

  root.querySelectorAll("[data-catalog-cart-toggle]").forEach((button) => {
    if (button.dataset.cartBound === "true") {
      return;
    }

    button.dataset.cartBound = "true";
    button.addEventListener("click", async () => {
      if (button.dataset.cartPending === "true") {
        return;
      }

      const productId = Number.parseInt(button.dataset.productId || "", 10);
      if (!Number.isInteger(productId) || productId <= 0) {
        return;
      }

      button.dataset.cartPending = "true";
      button.setAttribute("aria-busy", "true");

      try {
        const payload = await toggleCartOnServer(productId);
        setCartState(button, Boolean(payload.in_cart));
      } catch (error) {
        console.error(error);
      } finally {
        button.dataset.cartPending = "false";
        button.removeAttribute("aria-busy");
      }
    });
  });

  root.querySelectorAll("[data-catalog-preview-open]").forEach((button) => {
    if (button.dataset.previewBound === "true") {
      return;
    }

    button.dataset.previewBound = "true";
    button.addEventListener("click", () => {
      openCatalogPreviewDialog(button);
    });
  });
}

function initCatalogUi(root = document) {
  initCatalogFloatInputs(root);
  initCatalogDropdowns(root);
  initCatalogSortControls(root);
  initCatalogFilterReset(root);
  initCatalogProductCards(root);
  initCatalogPreviewDialog();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => initCatalogUi(document));
} else {
  initCatalogUi(document);
}

document.body.addEventListener("htmx:load", (event) => {
  initCatalogUi(event.target);
});
