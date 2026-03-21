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
  if (button.hasAttribute("data-cart-border-toggle")) {
    button.classList.toggle("border", !isInCart);
  }
  button.setAttribute("aria-label", isInCart ? "Удалить из корзины" : "Добавить в корзину");

  const icon = button.querySelector("[data-cart-icon]");
  if (!(icon instanceof HTMLImageElement)) {
    return;
  }

  icon.src = isInCart ? icon.dataset.iconFull : icon.dataset.iconEmpty;

  const productId = Number.parseInt(button.dataset.productId || "", 10);
  if (Number.isInteger(productId) && productId > 0) {
    document.body.dispatchEvent(
      new CustomEvent("catalog:cart-updated", {
        detail: { productId, inCart: isInCart },
      }),
    );
  }
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

function parseJsonScript(selector, root = document) {
  const script = root.querySelector(selector);
  if (!script) return null;

  try {
    return JSON.parse(script.textContent || "null");
  } catch {
    return null;
  }
}

function updateCatalogMobileViewButtons(root, view) {
  root.querySelectorAll("[data-catalog-mobile-view]").forEach((button) => {
    const isActive = button.getAttribute("data-catalog-mobile-view") === view;
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
    button.classList.toggle("opacity-100", isActive);
    button.classList.toggle("opacity-40", !isActive);
  });
}

function fillCatalogMobileProduct(node, product) {
  node.querySelectorAll("[data-product-link]").forEach((link) => {
    if (link instanceof HTMLAnchorElement) {
      link.href = product.url || "#";
    }
  });

  const image = node.querySelector("[data-product-image]");
  if (image instanceof HTMLImageElement) {
    image.src = product.image_url || "";
    image.alt = product.title || "";
  }

  const title = node.querySelector("[data-product-title]");
  if (title instanceof HTMLElement) {
    title.textContent = product.title || "";
  }

  const kind = node.querySelector("[data-product-kind]");
  if (kind instanceof HTMLElement) {
    kind.textContent = product.category || "";
  }

  node.querySelectorAll("[data-product-category]").forEach((categoryNode) => {
    if (categoryNode instanceof HTMLElement) {
      categoryNode.textContent = product.category || "";
    }
  });

  const price = node.querySelector("[data-product-price]");
  if (price instanceof HTMLElement) {
    price.textContent = product.price || "";
  }

  const rating = node.querySelector("[data-product-rating]");
  if (rating instanceof HTMLElement) {
    rating.textContent = product.rating || "";
  }

  const favoriteButton = node.querySelector("[data-catalog-favorite-toggle]");
  if (favoriteButton instanceof HTMLElement) {
    favoriteButton.dataset.favorited = product.is_favorited ? "true" : "false";
    setFavoriteState(favoriteButton, Boolean(product.is_favorited));
  }

  const previewButton = node.querySelector("[data-catalog-preview-open]");
  if (previewButton instanceof HTMLElement) {
    previewButton.dataset.previewTitle = product.title || "";
    previewButton.dataset.previewCategory = product.category || "";
    previewButton.dataset.previewPrice = product.price || "";
    previewButton.dataset.previewRating = product.rating || "";
    previewButton.dataset.previewImage = product.image_url || "";
    previewButton.dataset.previewUrl = product.url || "#";
  }

  const cartButton = node.querySelector("[data-catalog-cart-toggle]");
  if (cartButton instanceof HTMLElement) {
    cartButton.dataset.productId = String(product.id || "");
    cartButton.dataset.inCart = product.is_in_cart ? "true" : "false";
  }
}

function initCatalogMobileListing(root = document) {
  root.querySelectorAll("[data-catalog-mobile-listing]").forEach((listing) => {
    if (listing.dataset.catalogMobileBound === "true") {
      return;
    }

    listing.dataset.catalogMobileBound = "true";

    const products = parseJsonScript("#catalog-mobile-products-data", listing) || [];
    const listWrap = listing.querySelector("[data-catalog-mobile-products]");
    const emptyState = listing.querySelector("[data-catalog-mobile-empty]");
    const pagination = listing.querySelector("[data-catalog-mobile-pagination]");
    const gridTemplate = listing.querySelector("template[data-catalog-mobile-grid-template]");
    const listTemplate = listing.querySelector("template[data-catalog-mobile-list-template]");
    const renderPagination = window.AccountPagination?.render;

    if (!(listWrap instanceof HTMLElement) || !(gridTemplate instanceof HTMLTemplateElement) || !(listTemplate instanceof HTMLTemplateElement)) {
      return;
    }

    const state = {
      view: "grid",
      products: Array.isArray(products) ? products : [],
    };

    try {
      const stored = localStorage.getItem("catalogMobileView");
      if (stored === "grid" || stored === "list") {
        state.view = stored;
      }
    } catch {
      // ignore
    }

    const render = () => {
      listWrap.replaceChildren();
      listWrap.className = "";

      if (state.view === "grid") {
        listWrap.classList.add("mt-[18px]", "grid", "grid-cols-[repeat(2,170px)]", "justify-center", "gap-x-[16px]", "gap-y-[16px]");
      } else {
        listWrap.classList.add("mt-[18px]", "flex", "flex-col", "gap-[10px]");
      }

      if (!state.products.length) {
        if (emptyState instanceof HTMLElement) {
          emptyState.hidden = false;
        }
        if (pagination instanceof HTMLElement) {
          pagination.replaceChildren();
        }
        return;
      }

      if (emptyState instanceof HTMLElement) {
        emptyState.hidden = true;
      }

      const template = state.view === "grid" ? gridTemplate : listTemplate;
      for (const product of state.products) {
        const node = template.content.firstElementChild.cloneNode(true);
        fillCatalogMobileProduct(node, product);
        listWrap.appendChild(node);
      }

      initCatalogProductCards(listWrap);

      if (pagination instanceof HTMLElement && typeof renderPagination === "function") {
        const current = Number.parseInt(pagination.dataset.pageCurrent || "1", 10);
        const total = Number.parseInt(pagination.dataset.pageTotal || "1", 10);

        renderPagination(pagination, {
          page: current,
          totalPages: total,
          variant: "mobile",
          onChange: (targetPage) => {
            const url = new URL(window.location.href);
            url.searchParams.set("page", String(targetPage));
            window.location.href = url.toString();
          },
        });
      }
    };

    updateCatalogMobileViewButtons(listing, state.view);
    render();

    listing.querySelectorAll("[data-catalog-mobile-view]").forEach((button) => {
      button.addEventListener("click", () => {
        const nextView = button.getAttribute("data-catalog-mobile-view");
        if (!nextView || nextView === state.view) {
          return;
        }

        state.view = nextView;
        try {
          localStorage.setItem("catalogMobileView", nextView);
        } catch {
          // ignore
        }

        updateCatalogMobileViewButtons(listing, state.view);
        render();
      });
    });

    listing.addEventListener("click", (event) => {
      const filterButton = event.target.closest("[data-catalog-mobile-filter]");
      if (!filterButton) {
        return;
      }

      event.preventDefault();
      console.info("Catalog mobile filter clicked");
    });

    document.body.addEventListener("catalog:cart-updated", (event) => {
      const productId = Number.parseInt(event.detail?.productId, 10);
      if (!Number.isInteger(productId)) {
        return;
      }

      state.products = state.products.map((product) =>
        product.id === productId ? { ...product, is_in_cart: Boolean(event.detail?.inCart) } : product,
      );
    });
  });
}

function initCatalogProductCards(root = document) {
  root.querySelectorAll("[data-catalog-favorite-toggle]").forEach((button) => {
    if (button.dataset.favoriteBound === "true") {
      return;
    }

    button.dataset.favoriteBound = "true";
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const isFavorited = button.dataset.favorited === "true";
      setFavoriteState(button, !isFavorited);
    });
  });

  root.querySelectorAll("[data-catalog-cart-toggle]").forEach((button) => {
    if (button.dataset.cartBound === "true") {
      return;
    }

    button.dataset.cartBound = "true";
    setCartState(button, button.dataset.inCart === "true");
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();

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
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      openCatalogPreviewDialog(button);
    });
  });
}

function initCatalogUi(root = document) {
  initCatalogFloatInputs(root);
  initCatalogDropdowns(root);
  initCatalogSortControls(root);
  initCatalogFilterReset(root);
  initCatalogMobileListing(root);
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
